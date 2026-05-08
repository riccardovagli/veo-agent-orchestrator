import os
from fastapi import FastAPI
from google.cloud import firestore
from mcp.server.fastmcp import FastMCP

# Sostituisci con il tuo Project ID se diverso, 
# ma questo è quello che abbiamo usato finora
PROJECT_ID = "project-df9827fe-fcdb-4e91-83f"
db = firestore.Client(project=PROJECT_ID)

app = FastAPI(title="Veo Agent Orchestrator")
mcp_server = FastApiServer(name="Regista-Manager")

@mcp_server.tool()
async def upsert_graph_node(
    node_id: str, 
    node_type: str, 
    content: str, 
    depends_on: list[str] = None,
    status: str = "VALID"
):
    """
    Crea o aggiorna un nodo nel grafo di produzione su Firestore.
    Tipi ammessi: 'concept', 'asset_character', 'asset_location', 'scene'.
    """
    try:
        doc_ref = db.collection('agent_orchestration_state').document(PROJECT_ID)
        
        node_data = {
            "content": content,
            "type": node_type,
            "status": status,
            "depends_on": depends_on or [],
            "last_updated": firestore.SERVER_TIMESTAMP
        }
        
        # Merge=True garantisce di aggiornare solo il nodo specifico
        doc_ref.set({"graph_nodes": {node_id: node_data}}, merge=True)
        
        return [TextContent(text=f"✅ Nodo '{node_id}' salvato.")]
    except Exception as e:
        return [TextContent(text=f"❌ Errore: {str(e)}")]

# Endpoint per il protocollo MCP
app.mount("/mcp", mcp_server.app)

@app.get("/")
def health():
    return {"status": "online", "mcp_endpoint": "/mcp"}