from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

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


app.mount("/", mcp_app)