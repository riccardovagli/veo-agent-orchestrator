"""Explicit image relocation tool. Uses the backend's transactional operation."""
import asyncio
from app.backend_errors import BackendAgentError


def register_asset_move_tool(mcp,call_backend,validate_graph_request):
    @mcp.tool()
    async def move_project_asset(project_id:str,user_id:str,project_context_token:str,
                                 asset_id:str,target_node_id:str,expected_revision:str,request_id:str)->dict:
        """Move ONE existing image to another existing Asset node in the SAME project.
        Only on explicit user instruction. Read graph_revision first. Preserve UUID,
        original, thumbnail and provenance. Backend assigns a collision-free index.
        Never use a narrative patch to move an image. Active image jobs block moves.
        """
        error=await asyncio.to_thread(validate_graph_request,project_id,user_id,project_context_token)
        if error:return {'error':str(error),'code':'PROJECT_ACCESS_DENIED','uncertain':False}
        try:
            return await asyncio.to_thread(call_backend,'/agent/move-asset',{
                'project_id':project_id,'user_id':user_id,'project_context_token':project_context_token,
                'asset_id':asset_id,'target_node_id':target_node_id,'expected_revision':expected_revision,
                'request_id':request_id},user_id=user_id,project_context_token=project_context_token)
        except BackendAgentError as error:
            return error.result()
