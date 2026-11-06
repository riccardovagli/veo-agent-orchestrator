"""One complete sequence plan per submission; no generation or waiting in MCP."""
import json

def register_sequence_tool(mcp,validate_graph_request,call_backend_agent_endpoint):
    @mcp.tool()
    async def start_omni_sequence(project_id:str,user_id:str,project_context_token:str,
            request_id:str,original_instruction:str,frame_asset_ids:list[str],
            segment_instructions:list[str],resolution:str,aspect_ratio:str,
            segment_durations:list[int | None] | None=None) -> str:
        """Start ONE final sequence from 3–14 ORDERED verified frame UUIDs.
        For N frames supply N-1 optimized prompts: frames[0]->frames[1], etc.
        All clips render independently in parallel, then the backend concatenates them.
        Each duration is 3–10 seconds or None for automatic. If omitted all are automatic.
        A user total duration must be distributed across segments, never copied to each.
        Resolution/aspect ratio are project defaults unless explicitly overridden.
        One sequence counts as video_jobs=1. Return the parent receipt immediately.
        Do not call individual clip tools, poll, concatenate locally or retry uncertainty.
        """
        error=validate_graph_request(project_id,user_id,project_context_token)
        if error:return json.dumps({'error':str(error)},ensure_ascii=False)
        if (not 3<=len(frame_asset_ids)<=14 or len(segment_instructions)!=len(frame_asset_ids)-1
                or (segment_durations is not None and len(segment_durations)!=len(segment_instructions))):
            return json.dumps({'error':'Piano non valido: servono N frame e N-1 istruzioni/durate.'})
        durations=segment_durations if segment_durations is not None else [None]*len(segment_instructions)
        if any(d is not None and (type(d) is not int or not 3<=d<=10) for d in durations):
            return json.dumps({'error':'Ogni clip con primo e ultimo frame dura 3–10 secondi.'})
        payload={'project_id':project_id,'request_id':request_id,'original_instruction':original_instruction,
                 'frame_asset_ids':frame_asset_ids,'segments':[{'instruction':s,'duration':d}
                     for s,d in zip(segment_instructions,durations)],
                 'resolution':resolution,'aspect_ratio':aspect_ratio}
        try:
            result=call_backend_agent_endpoint('/agent/create-sequence',payload,
                user_id=user_id,project_context_token=project_context_token)
            if not result.get('jobId'):
                return json.dumps({'error':'Avvio non confermato. Non ripetere.', 'uncertain':True})
            if result.get('status')=='UNKNOWN':
                return json.dumps({'error':'Pianificazione non confermata. Verificare il piano nel progetto.',
                                   'job_id':result['jobId'],'uncertain':True})
            return json.dumps({'action':'omni_video_started','generation_type':'sequence',
                'job_id':result['jobId'],'node_id':result.get('nodeId'),
                'status':result.get('status','PROCESSING'),
                'message':'Piano sequenza avviato. Clip in parallelo; risultato concatenato via SSE.'},ensure_ascii=False)
        except Exception as error:
            from app.backend_errors import BackendAgentError
            if isinstance(error,BackendAgentError):return json.dumps(error.result(),ensure_ascii=False)
            return json.dumps({'error':'Esito avvio piano sequenza non confermato. Non ripetere.', 'uncertain':True})
