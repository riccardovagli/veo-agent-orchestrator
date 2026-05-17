from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from pydantic import BaseModel

import os
import hmac
import hashlib

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from datetime import datetime

import json

# Definiamo cosa ci arriva dal Backend 1
class ProcessRequest(BaseModel):
    project_id: str
    message: str
    token: str


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "localhost:*",
        "127.0.0.1:*",
        "veo-agent-orchestrator-841196451446.europe-west1.run.app",
        "veo-agent-orchestrator-841196451446.europe-west1.run.app:*",
    ],
    allowed_origins=[
        "http://localhost:*",
        "https://veo-agent-orchestrator-841196451446.europe-west1.run.app",
        "https://veo-agent-orchestrator-841196451446.europe-west1.run.app:*",
    ],
)

mcp = FastMCP(
    "Regista-Manager",
    transport_security=transport_security,
)


def get_db():
    return firestore.Client()

def make_project_context_token(user_id: str, project_id: str) -> str:
    secret = os.environ["PROJECT_CONTEXT_SECRET"].encode("utf-8")
    payload = f"{user_id}:{project_id}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_project_context_token(user_id: str, project_id: str, token: str) -> bool:
    expected = make_project_context_token(user_id, project_id)
    return hmac.compare_digest(expected, token)

def validate_graph_request(project_id: str, user_id: str, project_context_token: str) -> str | None:
    if not project_id:
        return "❌ Errore: project_id mancante"

    if "/" in project_id:
        return "❌ Errore: project_id non valido"

    if not user_id:
        return "❌ Errore: user_id mancante"

    if not project_context_token:
        return "❌ Errore: project_context_token mancante"

    if not verify_project_context_token(user_id, project_id, project_context_token):
        return "❌ Errore: token progetto non valido"

    return None


def replace_dependency(depends_on: list[str], old_node_id: str, new_node_id: str) -> list[str]:
    result = []
    seen = set()

    for dep in depends_on or []:
        updated = new_node_id if dep == old_node_id else dep

        # evita duplicati se old e new erano entrambi presenti
        if updated not in seen:
            result.append(updated)
            seen.add(updated)

    return result




@mcp.tool()
async def upsert_graph_node(
    project_id: str,
    user_id: str,
    project_context_token: str,
    node_id: str,
    node_type: str,
    content: str,
    depends_on: list[str] | None = None,
    status: str = "VALID",
) -> str:
    try:
        print("MCP TOOL CALLED: upsert_graph_node")
        print("PROJECT_ID:", project_id)
        print("USER_ID:", user_id)
        print("NODE_ID:", node_id)
        print("NODE_TYPE:", node_type)

        if not project_id:
            return "❌ Errore: project_id mancante"

        if "/" in project_id:
            return "❌ Errore: project_id non valido"

        if not user_id:
            return "❌ Errore: user_id mancante"

        if not project_context_token:
            return "❌ Errore: project_context_token mancante"

        if not verify_project_context_token(user_id, project_id, project_context_token):
            print("INVALID PROJECT TOKEN")
            return "❌ Errore: token progetto non valido"

        db = get_db()

        doc_ref = db.collection("agent_orchestration_state").document(project_id)

        node_data = {
            "content": content,
            "type": node_type,
            "status": status,
            "depends_on": depends_on or [],
            "last_updated": firestore.SERVER_TIMESTAMP,
            "updated_by": user_id,
        }

        doc_ref.set(
            {"graph_nodes": {node_id: node_data}},
            merge=True,
        )

        print("FIRESTORE WRITE OK:", doc_ref.path)

        return f"✅ Nodo '{node_id}' salvato in '{project_id}'"

    except Exception as e:
        print("MCP ERROR:", str(e))
        return f"❌ Errore: {str(e)}"


@mcp.tool()
async def get_graph_state(
    project_id: str,
    user_id: str,
    project_context_token: str,
) -> dict:
    if not verify_project_context_token(user_id, project_id, project_context_token):
        return {"error": "Invalid project context token"}

    db = get_db()
    doc = db.collection("agent_orchestration_state").document(project_id).get()

    if not doc.exists:
        return {"graph_nodes": {}}

    return doc.to_dict().get("graph_nodes", {})


@mcp.tool()
async def delete_graph_node(
    project_id: str,
    user_id: str,
    project_context_token: str,
    node_id: str,
) -> str:
    if not verify_project_context_token(user_id, project_id, project_context_token):
        return "❌ Errore: token progetto non valido"

    db = get_db()
    doc_ref = db.collection("agent_orchestration_state").document(project_id)

    doc_ref.update({
        f"graph_nodes.{node_id}": firestore.DELETE_FIELD
    })

    return f"✅ Nodo '{node_id}' eliminato"


@mcp.tool()
async def merge_graph_nodes(
    project_id: str,
    user_id: str,
    project_context_token: str,
    canonical_node_id: str,
    duplicate_node_id: str,
    merged_content: str | None = None,
    merged_status: str = "VALID",
) -> str:
    try:
        print("MCP TOOL CALLED: merge_graph_nodes")
        print("PROJECT_ID:", project_id)
        print("CANONICAL:", canonical_node_id)
        print("DUPLICATE:", duplicate_node_id)

        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return error

        if not canonical_node_id or not duplicate_node_id:
            return "❌ Errore: canonical_node_id e duplicate_node_id sono obbligatori"

        if canonical_node_id == duplicate_node_id:
            return "❌ Errore: canonical_node_id e duplicate_node_id coincidono"

        db = get_db()
        doc_ref = db.collection("agent_orchestration_state").document(project_id)
        snapshot = doc_ref.get()

        if not snapshot.exists:
            return f"❌ Errore: progetto '{project_id}' non trovato"

        data = snapshot.to_dict() or {}
        graph_nodes = data.get("graph_nodes", {})

        if canonical_node_id not in graph_nodes:
            return f"❌ Errore: nodo canonico '{canonical_node_id}' non trovato"

        if duplicate_node_id not in graph_nodes:
            return f"❌ Errore: nodo duplicato '{duplicate_node_id}' non trovato"

        canonical_node = graph_nodes[canonical_node_id]
        duplicate_node = graph_nodes[duplicate_node_id]

        canonical_content = canonical_node.get("content", "")
        duplicate_content = duplicate_node.get("content", "")

        final_content = merged_content
        if not final_content:
            if duplicate_content and duplicate_content not in canonical_content:
                final_content = f"{canonical_content}\n\nInformazioni consolidate: {duplicate_content}".strip()
            else:
                final_content = canonical_content

        final_depends_on = canonical_node.get("depends_on", []) or []
        for dep in duplicate_node.get("depends_on", []) or []:
            if dep != duplicate_node_id and dep not in final_depends_on:
                final_depends_on.append(dep)

        updates = {
            f"graph_nodes.{canonical_node_id}.content": final_content,
            f"graph_nodes.{canonical_node_id}.type": canonical_node.get("type", duplicate_node.get("type", "Asset")),
            f"graph_nodes.{canonical_node_id}.status": merged_status,
            f"graph_nodes.{canonical_node_id}.depends_on": final_depends_on,
            f"graph_nodes.{canonical_node_id}.last_updated": firestore.SERVER_TIMESTAMP,
            f"graph_nodes.{canonical_node_id}.updated_by": user_id,
            f"graph_nodes.{duplicate_node_id}": firestore.DELETE_FIELD,
        }

        # Aggiorna tutte le dipendenze che puntano al duplicato
        for node_id, node in graph_nodes.items():
            if node_id == duplicate_node_id:
                continue

            depends_on = node.get("depends_on", []) or []
            if duplicate_node_id in depends_on:
                updates[f"graph_nodes.{node_id}.depends_on"] = replace_dependency(
                    depends_on,
                    duplicate_node_id,
                    canonical_node_id,
                )
                updates[f"graph_nodes.{node_id}.last_updated"] = firestore.SERVER_TIMESTAMP
                updates[f"graph_nodes.{node_id}.updated_by"] = user_id

        doc_ref.update(updates)

        print("MERGE OK:", duplicate_node_id, "->", canonical_node_id)

        return (
            f"✅ Nodo duplicato '{duplicate_node_id}' consolidato in "
            f"'{canonical_node_id}'. Dipendenze aggiornate."
        )

    except Exception as e:
        print("MCP MERGE ERROR:", str(e))
        return f"❌ Errore merge_graph_nodes: {str(e)}"

@mcp.tool()
async def rename_graph_node(
    project_id: str,
    user_id: str,
    project_context_token: str,
    old_node_id: str,
    new_node_id: str,
) -> str:
    try:
        print("MCP TOOL CALLED: rename_graph_node")
        print("PROJECT_ID:", project_id)
        print("OLD_NODE_ID:", old_node_id)
        print("NEW_NODE_ID:", new_node_id)

        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return error

        if not old_node_id or not new_node_id:
            return "❌ Errore: old_node_id e new_node_id sono obbligatori"

        if old_node_id == new_node_id:
            return "❌ Errore: old_node_id e new_node_id coincidono"

        if "/" in old_node_id or "/" in new_node_id:
            return "❌ Errore: node_id non valido"

        db = get_db()
        doc_ref = db.collection("agent_orchestration_state").document(project_id)
        snapshot = doc_ref.get()

        if not snapshot.exists:
            return f"❌ Errore: progetto '{project_id}' non trovato"

        data = snapshot.to_dict() or {}
        graph_nodes = data.get("graph_nodes", {})

        if old_node_id not in graph_nodes:
            return f"❌ Errore: nodo '{old_node_id}' non trovato"

        if new_node_id in graph_nodes:
            return (
                f"❌ Errore: il nodo '{new_node_id}' esiste già. "
                f"Usa merge_graph_nodes se vuoi consolidare due nodi."
            )

        old_node = graph_nodes[old_node_id]

        renamed_node = {
            **old_node,
            "last_updated": firestore.SERVER_TIMESTAMP,
            "updated_by": user_id,
        }

        updates = {
            f"graph_nodes.{new_node_id}": renamed_node,
            f"graph_nodes.{old_node_id}": firestore.DELETE_FIELD,
        }

        # Aggiorna tutte le dipendenze che puntano al vecchio node_id
        for node_id, node in graph_nodes.items():
            if node_id == old_node_id:
                continue

            depends_on = node.get("depends_on", []) or []
            if old_node_id in depends_on:
                updates[f"graph_nodes.{node_id}.depends_on"] = replace_dependency(
                    depends_on,
                    old_node_id,
                    new_node_id,
                )
                updates[f"graph_nodes.{node_id}.last_updated"] = firestore.SERVER_TIMESTAMP
                updates[f"graph_nodes.{node_id}.updated_by"] = user_id

        doc_ref.update(updates)

        print("RENAME OK:", old_node_id, "->", new_node_id)

        return (
            f"✅ Nodo '{old_node_id}' rinominato in '{new_node_id}'. "
            f"Dipendenze aggiornate."
        )

    except Exception as e:
        print("MCP RENAME ERROR:", str(e))
        return f"❌ Errore rename_graph_node: {str(e)}"


@mcp.tool()
async def prepare_visual_asset_generation(
    project_id: str,
    user_id: str,
    project_context_token: str,
    node_id: str,
    asset_type: str,
    image_prompt: str,
    aspect_ratio: str = "16:9",
    model_version: str = "2.5",
) -> dict:
    try:
        print("MCP TOOL CALLED: prepare_visual_asset_generation")
        print("PROJECT_ID:", project_id)
        print("USER_ID:", user_id)
        print("NODE_ID:", node_id)
        print("ASSET_TYPE:", asset_type)

        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return {
                "action": "error",
                "message": error,
            }

        if not node_id:
            return {
                "action": "error",
                "message": "node_id mancante",
            }

        if asset_type not in ["character", "object", "environment"]:
            return {
                "action": "error",
                "message": "asset_type non valido. Usa character, object o environment.",
            }

        if aspect_ratio not in ["16:9", "9:16", "1:1"]:
            return {
                "action": "error",
                "message": "aspect_ratio non valido. Usa 16:9, 9:16 o 1:1.",
            }

        if model_version not in ["2.5", "3.1"]:
            return {
                "action": "error",
                "message": "model_version non valido. Usa 2.5 o 3.1.",
            }

        if not image_prompt or len(image_prompt.strip()) < 20:
            return {
                "action": "error",
                "message": "image_prompt mancante o troppo breve.",
            }

        db = get_db()
        doc_ref = db.collection("agent_orchestration_state").document(project_id)
        snapshot = doc_ref.get()

        if not snapshot.exists:
            return {
                "action": "error",
                "message": f"Progetto '{project_id}' non trovato.",
            }

        graph_nodes = (snapshot.to_dict() or {}).get("graph_nodes", {})

        if node_id not in graph_nodes:
            return {
                "action": "error",
                "message": f"Nodo '{node_id}' non trovato nel grafo.",
            }

        node = graph_nodes[node_id]

        if node.get("type") != "Asset":
            return {
                "action": "error",
                "message": f"Il nodo '{node_id}' non è un Asset.",
            }

        clean_prompt = image_prompt.strip()

        # Salviamo nel grafo il prompt preparato, ma NON generiamo l'immagine qui.
        doc_ref.update({
            f"graph_nodes.{node_id}.prepared_image_prompt": clean_prompt,
            f"graph_nodes.{node_id}.prepared_asset_type": asset_type,
            f"graph_nodes.{node_id}.prepared_aspect_ratio": aspect_ratio,
            f"graph_nodes.{node_id}.prepared_model_version": model_version,
            f"graph_nodes.{node_id}.production_status": "PROMPT_PREPARED",
            f"graph_nodes.{node_id}.last_updated": firestore.SERVER_TIMESTAMP,
            f"graph_nodes.{node_id}.updated_by": user_id,
        })

        print("VISUAL ASSET PROMPT PREPARED:", node_id)

        return {
            "action": "create_image_asset",
            "node_id": node_id,
            "asset_type": asset_type,
            "prompt": clean_prompt,
            "aspect_ratio": aspect_ratio,
            "model_version": model_version,
        }

    except Exception as e:
        print("MCP prepare_visual_asset_generation ERROR:", str(e))
        return {
            "action": "error",
            "message": str(e),
        }


@mcp.tool()
async def prepare_visual_asset_edit(
    project_id: str,
    user_id: str,
    project_context_token: str,
    node_id: str,
    asset_index: int,
    instruction: str,
    variant: str | None = None,
) -> str:
    try:
        print("========== MCP prepare_visual_asset_edit START ==========")
        print("project_id:", project_id)
        print("user_id:", user_id)
        print("node_id:", node_id)
        print("asset_index:", asset_index)
        print("instruction:", instruction)
        print("variant:", variant)

        error = validate_graph_request(project_id, user_id, project_context_token)
        print("validate_graph_request error:", error)

        if error:
            result = {"error": error}
            print("prepare_visual_asset_edit RESULT:", result)
            return json.dumps(result, ensure_ascii=False)

        db = get_db()

        graph_ref = db.collection("agent_orchestration_state").document(project_id)
        graph_snapshot = graph_ref.get()

        print("graph exists:", graph_snapshot.exists)

        if not graph_snapshot.exists:
            result = {"error": f"Project graph not found: {project_id}"}
            print("prepare_visual_asset_edit RESULT:", result)
            return json.dumps(result, ensure_ascii=False)

        graph_data = graph_snapshot.to_dict() or {}
        graph_nodes = graph_data.get("graph_nodes", {})

        print("graph_nodes keys:", list(graph_nodes.keys()))

        node = graph_nodes.get(node_id)
        print("node found:", bool(node))

        if not node:
            result = {"error": f"Node not found: {node_id}"}
            print("prepare_visual_asset_edit RESULT:", result)
            return json.dumps(result, ensure_ascii=False)

        generated_assets = node.get("generated_assets", []) or []
        print("generated_assets count:", len(generated_assets))

        for a in generated_assets:
            print("asset candidate:", {
                "asset_index": a.get("asset_index"),
                "asset_id": a.get("asset_id"),
                "asset_type": a.get("asset_type"),
            })

        selected_asset = None
        for asset in generated_assets:
            if int(asset.get("asset_index", -1)) == int(asset_index):
                selected_asset = asset
                break

        print("selected_asset:", selected_asset)

        if not selected_asset:
            result = {
                "error": f"Asset index {asset_index} not found for node {node_id}"
            }
            print("prepare_visual_asset_edit RESULT:", result)
            return json.dumps(result, ensure_ascii=False)

        asset_id = selected_asset.get("asset_id")
        print("selected asset_id:", asset_id)

        if not asset_id:
            result = {
                "error": f"Asset index {asset_index} for node {node_id} has no asset_id"
            }
            print("prepare_visual_asset_edit RESULT:", result)
            return json.dumps(result, ensure_ascii=False)

        result = {
            "action": "edit_image_asset",
            "node_id": node_id,
            "asset_index": asset_index,
            "asset_id": asset_id,
            "asset_type": selected_asset.get("asset_type") or node.get("prepared_asset_type"),
            "instruction": instruction,
            "variant": variant,
        }

        print("prepare_visual_asset_edit RESULT:", result)
        print("========== MCP prepare_visual_asset_edit END ==========")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        print("========== MCP prepare_visual_asset_edit ERROR ==========")
        print("ERROR:", str(e))
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def prepare_visual_asset_composition(
    project_id: str,
    user_id: str,
    project_context_token: str,
    source_node_id: str,
    source_asset_index: int,
    target_node_id: str,
    target_asset_index: int,
    instruction: str,
    variant: str | None = None,
) -> str:
    try:
        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return json.dumps({"error": error}, ensure_ascii=False)

        db = get_db()
        graph_ref = db.collection("agent_orchestration_state").document(project_id)
        graph_snapshot = graph_ref.get()

        if not graph_snapshot.exists:
            return json.dumps({"error": f"Project graph not found: {project_id}"}, ensure_ascii=False)

        graph_data = graph_snapshot.to_dict() or {}
        graph_nodes = graph_data.get("graph_nodes", {})

        source_node = graph_nodes.get(source_node_id)
        target_node = graph_nodes.get(target_node_id)

        if not source_node:
            return json.dumps({"error": f"Source node not found: {source_node_id}"}, ensure_ascii=False)

        if not target_node:
            return json.dumps({"error": f"Target node not found: {target_node_id}"}, ensure_ascii=False)

        def resolve_asset(node, node_id, asset_index):
            generated_assets = node.get("generated_assets", []) or []
            for asset in generated_assets:
                if int(asset.get("asset_index", -1)) == int(asset_index):
                    return asset
            return None

        source_asset = resolve_asset(source_node, source_node_id, source_asset_index)
        target_asset = resolve_asset(target_node, target_node_id, target_asset_index)

        if not source_asset:
            return json.dumps({
                "error": f"Source asset index {source_asset_index} not found for node {source_node_id}"
            }, ensure_ascii=False)

        if not target_asset:
            return json.dumps({
                "error": f"Target asset index {target_asset_index} not found for node {target_node_id}"
            }, ensure_ascii=False)

        source_asset_id = source_asset.get("asset_id")
        target_asset_id = target_asset.get("asset_id")

        if not source_asset_id:
            return json.dumps({
                "error": f"Source asset index {source_asset_index} for node {source_node_id} has no asset_id"
            }, ensure_ascii=False)

        if not target_asset_id:
            return json.dumps({
                "error": f"Target asset index {target_asset_index} for node {target_node_id} has no asset_id"
            }, ensure_ascii=False)

        return json.dumps({
            "action": "compose_assets",
            "source_node_id": source_node_id,
            "source_asset_index": source_asset_index,
            "source_asset_id": source_asset_id,
            "target_node_id": target_node_id,
            "target_asset_index": target_asset_index,
            "target_asset_id": target_asset_id,
            "asset_type": target_asset.get("asset_type") or target_node.get("prepared_asset_type"),
            "instruction": instruction,
            "variant": variant,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)



@mcp.tool()
async def prepare_scene_video_generation(
    project_id: str,
    user_id: str,
    project_context_token: str,
    scene_node_id: str,
    first_frame_node_id: str,
    first_frame_asset_index: int,
    instruction: str,
    scene_order: int,

    # nuovo: frame finale opzionale
    last_frame_node_id: str | None = None,
    last_frame_asset_index: int | None = None,

    aspect_ratio: str = "16:9",
    duration: int = 8,
    render_mode: str = "preview",
    resolution: str = "1080p",
    seed: int = 0,
    camera_angle: str = "eye-level",
    camera_movement: str = "slow push-in",
    lens: str = "standard",
    lighting: str = "cinematic mysterious light",
    tone: str = "mysterious",
    style: str = "cinematic realistic",
    temporal: str = "normal",
    environment: str = "",
    music: str = "none",
) -> str:
    try:
        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return json.dumps({"error": error}, ensure_ascii=False)

        db = get_db()

        graph_ref = db.collection("agent_orchestration_state").document(project_id)
        graph_snapshot = graph_ref.get()

        if not graph_snapshot.exists:
            return json.dumps({"error": f"Project graph not found: {project_id}"}, ensure_ascii=False)

        graph_data = graph_snapshot.to_dict() or {}
        graph_nodes = graph_data.get("graph_nodes", {})

        scene_node = graph_nodes.get(scene_node_id)
        if not scene_node:
            return json.dumps({"error": f"Scene node not found: {scene_node_id}"}, ensure_ascii=False)

        def resolve_asset_id(node_id: str, asset_index: int, label: str) -> str | None:
            node = graph_nodes.get(node_id)
            if not node:
                raise ValueError(f"{label} node not found: {node_id}")

            generated_assets = node.get("generated_assets", []) or []

            selected_asset = None
            for asset in generated_assets:
                if int(asset.get("asset_index", -1)) == int(asset_index):
                    selected_asset = asset
                    break

            if not selected_asset:
                raise ValueError(
                    f"{label} asset index {asset_index} not found for node {node_id}"
                )

            asset_id = selected_asset.get("asset_id")
            if not asset_id:
                raise ValueError(
                    f"{label} asset index {asset_index} for node {node_id} has no asset_id"
                )

            return asset_id

        try:
            first_frame_asset_id = resolve_asset_id(
                first_frame_node_id,
                first_frame_asset_index,
                "First frame"
            )
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        last_frame_asset_id = None

        if last_frame_node_id is not None or last_frame_asset_index is not None:
            if last_frame_node_id is None:
                return json.dumps({
                    "error": "last_frame_node_id is required when last_frame_asset_index is provided"
                }, ensure_ascii=False)

            if last_frame_asset_index is None:
                return json.dumps({
                    "error": "last_frame_asset_index is required when last_frame_node_id is provided"
                }, ensure_ascii=False)

            try:
                last_frame_asset_id = resolve_asset_id(
                    last_frame_node_id,
                    last_frame_asset_index,
                    "Last frame"
                )
            except ValueError as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        scene_content = scene_node.get("content", "")

        final_scene_text = instruction.strip()
        if scene_content:
            final_scene_text = f"{scene_content}\n\nVideo direction: {instruction.strip()}"

        result = {
            "action": "create_video_scene",
            "scene_node_id": scene_node_id,
            "scene_order": scene_order,
            "scene": final_scene_text,

            "first_frame_node_id": first_frame_node_id,
            "first_frame_asset_index": first_frame_asset_index,
            "first_frame_asset_id": first_frame_asset_id,

            "last_frame_node_id": last_frame_node_id,
            "last_frame_asset_index": last_frame_asset_index,
            "last_frame_asset_id": last_frame_asset_id,

            "reference_asset_ids": [],

            "camera_angle": camera_angle,
            "camera_movement": camera_movement,
            "lens": lens,
            "lighting": lighting,
            "tone": tone,
            "style": style,
            "temporal": temporal,
            "environment": environment,
            "music": music,
            "duration": duration,
            "render_mode": render_mode,
            "aspect_ratio": aspect_ratio,
            "seed": seed,
            "resolution": resolution,
        }

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        print("MCP PREPARE SCENE VIDEO GENERATION ERROR:", str(e))
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def prepare_video_frame_extraction(
    project_id: str,
    user_id: str,
    project_context_token: str,
    scene_node_id: str,
    frame: str = "last",  # first | last | current
    timestamp: float | None = None,
    job_id: str | None = None,
    variant: str | None = None,
) -> str:
    try:
        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return json.dumps({"error": error}, ensure_ascii=False)

        if frame not in ["first", "last", "current"]:
            return json.dumps({
                "error": f"Invalid frame type: {frame}. Expected first, last or current."
            }, ensure_ascii=False)

        if frame == "current" and timestamp is None:
            return json.dumps({
                "error": "timestamp is required when frame is current"
            }, ensure_ascii=False)

        db = get_db()

        graph_ref = db.collection("agent_orchestration_state").document(project_id)
        graph_snapshot = graph_ref.get()

        if not graph_snapshot.exists:
            return json.dumps({
                "error": f"Project graph not found: {project_id}"
            }, ensure_ascii=False)

        graph_data = graph_snapshot.to_dict() or {}
        graph_nodes = graph_data.get("graph_nodes", {})

        scene_node = graph_nodes.get(scene_node_id)
        if not scene_node:
            return json.dumps({
                "error": f"Scene node not found: {scene_node_id}"
            }, ensure_ascii=False)

        resolved_job_id = job_id

        if not resolved_job_id:
            resolved_job_id = scene_node.get("last_video_job_id")

        if not resolved_job_id:
            video_jobs = scene_node.get("video_jobs", []) or []

            # Preferisci l’ultimo job completato, se lo trovi nel grafo.
            completed_jobs = [
                j for j in video_jobs
                if j.get("job_id") and j.get("status") in ["COMPLETED", "VIDEO_CREATED"]
            ]

            if completed_jobs:
                resolved_job_id = completed_jobs[-1].get("job_id")
            elif video_jobs:
                # Fallback: ultimo job registrato.
                resolved_job_id = video_jobs[-1].get("job_id")

        if not resolved_job_id:
            return json.dumps({
                "error": f"No video job found for scene node: {scene_node_id}"
            }, ensure_ascii=False)

        # Controllo leggero su video_jobs/{job_id}, così evitiamo di preparare
        # un’estrazione da un job inesistente o non completato.
        job_doc = db.collection("video_jobs").document(resolved_job_id).get()

        if not job_doc.exists:
            return json.dumps({
                "error": f"Video job not found: {resolved_job_id}"
            }, ensure_ascii=False)

        job_data = job_doc.to_dict() or {}
        job_status = job_data.get("status")

        if job_status != "COMPLETED":
            return json.dumps({
                "error": f"Video job is not completed: {resolved_job_id} has status {job_status}"
            }, ensure_ascii=False)

        output = job_data.get("output") or {}
        if not output.get("gcs_uri"):
            return json.dumps({
                "error": f"Video job has no output gcs_uri: {resolved_job_id}"
            }, ensure_ascii=False)

        result = {
            "action": "extract_video_frame",
            "scene_node_id": scene_node_id,
            "job_id": resolved_job_id,
            "frame": frame,
            "timestamp": timestamp if frame == "current" else None,
            "variant": variant,
        }

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        print("MCP PREPARE VIDEO FRAME EXTRACTION ERROR:", str(e))
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def get_project_chat_context(
    project_id: str,
    user_id: str,
    project_context_token: str,
    limit: int = 10,
) -> dict:
    try:
        print("MCP TOOL CALLED: get_project_chat_context")
        print("PROJECT_ID:", project_id)
        print("USER_ID:", user_id)
        print("LIMIT:", limit)

        error = validate_graph_request(project_id, user_id, project_context_token)
        if error:
            return {"error": error}

        if limit <= 0:
            limit = 10

        if limit > 30:
            limit = 30

        db = get_db()

        docs = (
            db.collection("agent_chat_sessions")
            .document(user_id)
            .collection("projects")
            .document(project_id)
            .collection("messages")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit * 2)
            .stream()
        )

        messages = []

        for doc in docs:
            data = doc.to_dict() or {}

            role = data.get("role")
            text = data.get("text", "")

            if role not in ["user", "agent"]:
                continue

            if not text:
                continue

            messages.append({
                "role": role,
                "text": text,
                "created_at": data.get("created_at"),
                "turn_created_at": data.get("turn_created_at"),
                "turn_order": data.get("turn_order", 0),
            })

        def sort_key(m):
            # Compatibilità con messaggi vecchi senza turn_created_at
            base_time = m.get("turn_created_at") or m.get("created_at") or datetime.min
            return (
                base_time,
                m.get("turn_order", 0),
            )

        messages.sort(key=sort_key)

        # ultimi N messaggi in ordine cronologico
        messages = messages[-limit:]

        return {
            "project_id": project_id,
            "messages": [
                {
                    "role": m["role"],
                    "text": m["text"],
                }
                for m in messages
            ],
            "count": len(messages),
        }

    except Exception as e:
        print("MCP GET PROJECT CHAT CONTEXT ERROR:", str(e))
        return {"error": str(e)}

mcp_app = mcp.streamable_http_app()

app = FastAPI(
    title="Veo Agent Orchestrator",
    lifespan=mcp_app.router.lifespan_context,
)




@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    # Questo scrive nei LOG di Google Cloud il motivo del 422
    print(f"!!! ERRORE DI VALIDAZIONE RILEVATO: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body}
    )

@app.get("/")
def health():
    return {
        "status": "online",
        "mcp_endpoint": "/mcp",
    }


@app.get("/debug-firestore")
async def debug_firestore(project_id: str = "Catnip"):
    try:
        db = get_db()

        doc_ref = db.collection("agent_orchestration_state").document(project_id)

        doc_ref.set(
            {
                "status": "test_funzionante",
                "debug_info": "Test eseguito correttamente",
                "timestamp": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        return {
            "message": f"SCRITTURA RIUSCITA su progetto: {project_id}!",
            "note": "Controlla ora la collection agent_orchestration_state",
        }

    except Exception as e:
        return {"error": str(e)}

@app.post("/process")
async def handle_agent_process(req: ProcessRequest):
    try:
        # Per parlare con l'agente FastMCP e fargli generare una risposta
        # usiamo il metodo .chat() dell'istanza mcp che hai già creato.
        # Questo avvierà il ragionamento dell'LLM (Claude/Gemini) configurato.
        
        result = await mcp.chat(req.message)
        
        # FastMCP restituisce un oggetto, noi prendiamo il testo della risposta
        return {"reply": str(result)} 
        
    except Exception as e:
        print(f"Errore Agent Orchestrator: {e}")
        return {"reply": f"Il Regista ha avuto un mancamento: {str(e)}"}




app.mount("/", mcp_app)



