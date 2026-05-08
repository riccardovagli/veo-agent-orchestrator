from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Regista-Manager")

mcp_app = mcp.streamable_http_app()

app = FastAPI(
    title="Veo Agent Orchestrator",
    lifespan=mcp_app.lifespan,
)


def get_db():
    # La libreria rileva automaticamente il progetto GCP
    # dall'ambiente Cloud Run.
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


@app.get("/")
def health():
    return {
        "status": "online",
        "mcp_endpoint": "/mcp/mcp",
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