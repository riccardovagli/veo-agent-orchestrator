from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP

PROJECT_ID = "project-df9827fe-fcdb-4e91-83f"

app = FastAPI(title="Veo Agent Orchestrator")
mcp = FastMCP("Regista-Manager")


def get_db():
    return firestore.Client(project=PROJECT_ID)


@mcp.tool()
async def upsert_graph_node(
    node_id: str,
    node_type: str,
    content: str,
    depends_on: list[str] | None = None,
    status: str = "VALID",
) -> str:
    try:
        db = get_db()

        doc_ref = db.collection("agent_orchestration_state").document(PROJECT_ID)

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

        return f"✅ Nodo '{node_id}' salvato."

    except Exception as e:
        return f"❌ Errore: {str(e)}"


@app.get("/")
def health():
    return {"status": "online", "mcp_endpoint": "/mcp"}


app.mount("/mcp", mcp.streamable_http_app())

@app.get("/debug-firestore")
async def debug_firestore():
    try:
        db = get_db()
        doc_ref = db.collection("agent_orchestration_state").document(PROJECT_ID)
        doc_ref.set({"status": "test_funzionante", "timestamp": firestore.SERVER_TIMESTAMP}, merge=True)
        return {"message": "SCRITTURA RIUSCITA!"}
    except Exception as e:
        return {"error": str(e)}