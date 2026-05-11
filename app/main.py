from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from pydantic import BaseModel

import os
import hmac
import hashlib


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


mcp_app = mcp.streamable_http_app()

app = FastAPI(
    title="Veo Agent Orchestrator",
    lifespan=mcp_app.router.lifespan_context,
)

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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


