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



