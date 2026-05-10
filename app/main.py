from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from pydantic import BaseModel

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


@mcp.tool()
async def upsert_graph_node(
    project_id: str,
    node_id: str,
    node_type: str,
    content: str,
    depends_on: list[str] | None = None,
    status: str = "VALID",
) -> str:
    try:
        db = get_db()

        doc_ref = db.collection("agent_orchestration_state").document(project_id)

        node_data = {
            "content": content,
            "type": node_type,
            "status": status,
            "depends_on": depends_on or [],
            "last_updated": firestore.SERVER_TIMESTAMP,
        }

        doc_ref.set(
            {"graph_nodes": {node_id: node_data}},
            merge=True,
        )

        return f"✅ Nodo '{node_id}' salvato in '{project_id}'"

    except Exception as e:
        return f"❌ Errore: {str(e)}"


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