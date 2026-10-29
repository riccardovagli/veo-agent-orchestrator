"""Replacement implementation for the existing MCP generation tool name."""
import os

import httpx


async def start_image_generation(
    project_id, user_id, project_context_token, node_id, asset_type, image_prompt,
    aspect_ratio="16:9", model_version="3.1", request_id=None,
):
    # Backend validates the signed context, project and inputs. No Firestore
    # writes and no image generation are performed in this MCP process.
    payload = {
        "project_id": project_id, "user_id": user_id,
        "project_context_token": project_context_token,
        "node_id": node_id, "type": asset_type, "prompt": image_prompt,
        "aspect_ratio": aspect_ratio, "source": "agent", "request_id": request_id,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10)) as client:
            response = await client.post(
                os.environ["FRAMEBUTCHER_BACKEND_URL"].rstrip("/") + "/agent/create-image",
                headers={"X-FrameButcher-Internal-Token": os.environ["BACKEND_INTERNAL_TOKEN"]},
                json=payload,
            )
        if not response.is_success:
            return {"action": "error", "message": f"Avvio immagine non riuscito (HTTP {response.status_code}); non dichiarare la generazione avviata."}
        job = response.json()
        return {
            "action": "image_job_started", "job_id": job["job_id"],
            "project_id": project_id, "node_id": job["node_id"],
            "status": job["status"], "model": job["model"],
            "message": "Richiesta immagine presa in carico. Il risultato arriverà nell'interfaccia; non attendere il completamento e rispondi ora all'utente.",
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return {
            "action": "error",
            "message": "Conferma di presa in carico non ricevuta. Il frontend recupererà eventuali job registrati; non ripetere automaticamente la generazione.",
        }
