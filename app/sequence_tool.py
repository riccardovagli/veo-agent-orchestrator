"""One complete sequence plan per submission; no generation or waiting in MCP."""
import json

from typing import Literal
from typing_extensions import TypedDict


class SceneClip(TypedDict, total=False):
    instruction: str
    duration: int
    first_frame_asset_id: str
    last_frame_asset_id: str
    reference_asset_ids: list[str]
    join: Literal['cut', 'continuous']


def register_sequence_tool(mcp,validate_graph_request,call_backend_agent_endpoint):
    def sequence_call(endpoint,project_id,user_id,token,payload):
        error=validate_graph_request(project_id,user_id,token)
        if error:
            return {'error':str(error)}
        try:
            return call_backend_agent_endpoint(endpoint,{'project_id':project_id,**payload},
                                               user_id=user_id,project_context_token=token)
        except Exception as error:
            from app.backend_errors import BackendAgentError
            if isinstance(error,BackendAgentError):
                return error.result()
            return {'error':'Esito della richiesta non confermato.', 'uncertain':True}

    @mcp.tool()
    async def inspect_omni_sequence(project_id:str,user_id:str,project_context_token:str,
                                    job_id:str | None=None, capabilities_only:bool=False) -> str:
        """List sequences, inspect one by job ID, or read current OMNI clip duration limits.
        To plan a new scene, use capabilities_only=True without a job ID.
        """
        return json.dumps(sequence_call('/agent/sequence-jobs',project_id,user_id,
                                        project_context_token,
                                        {'job_id':job_id,'capabilities_only':capabilities_only}),ensure_ascii=False)

    @mcp.tool()
    async def revise_omni_sequence(project_id:str,user_id:str,project_context_token:str,
                                   job_id:str, prompts:dict[str,str] | None=None,
                                   frames:dict[str,str] | None=None,
                                   replacements:dict[str,str] | None=None) -> str:
        """Revise an identified sequence. Keys are one-based clip/frame numbers as strings.
        prompts supplies clip instructions, frames supplies verified new image UUIDs for
        boundaries, replacements supplies completed video job IDs. For stopped plans
        supply prompts for the failed clips. Unchanged clips are reused by the backend.
        """
        result=sequence_call('/agent/revise-sequence',project_id,user_id,project_context_token,
                             {'job_id':job_id,'revision':{'prompts':prompts or {},
                                                         'frames':frames or {},
                                                         'replacements':replacements or {}}})
        if 'error' in result:
            return json.dumps(result,ensure_ascii=False)
        if not result.get('jobId') or result.get('status')=='UNKNOWN':
            return json.dumps({'error':'Pianificazione del montaggio non confermata.',
                               'job_id':result.get('jobId'),'uncertain':True},ensure_ascii=False)
        return json.dumps({'action':'omni_video_started','generation_type':('scene' if result.get('scene') else 'sequence'),
                           'job_id':result['jobId'],'node_id':result.get('nodeId'),
                           'status':result.get('status','PROCESSING'),
                           'message':'Revisione avviata. Le clip confermate vengono riusate.'},ensure_ascii=False)

    @mcp.tool()
    async def start_omni_sequence(project_id:str,user_id:str,project_context_token:str,
            request_id:str,original_instruction:str,frame_asset_ids:list[str] | None=None,
            segment_instructions:list[str] | None=None,resolution:str='',
            aspect_ratio:str='',segment_durations:list[int | None] | None=None,
            open_ending:bool=False,scene_clips:list[SceneClip] | None=None,
            total_duration:int | None=None) -> str:
        """Produce one final video from 2–13 clips. Counts as one video job.
        For a continuous plan, supply ordered frame_asset_ids and segment_instructions.
        With open_ending=True add a final prompt for the last frame with a free ending.
        Legacy clip durations are 3–10 seconds or None (automatic).
        For a scene, supply scene_clips and total_duration in seconds. Each clip has
        instruction, duration, and either reference_asset_ids (1–10), first_frame_asset_id,
        or first_frame_asset_id plus last_frame_asset_id (max 10 seconds). Frame and
        reference IDs are verified project asset UUIDs. Other limits follow the active
        OMNI profile. Each clip after the first has join='cut' or join='continuous'
        with the previous last frame as its first. Supply the project resolution and
        aspect ratio (or user overrides) for the entire scene. Submit the full plan
        once and return the parent receipt. Rendering and assembly are asynchronous.
        """
        error=validate_graph_request(project_id,user_id,project_context_token)
        if error:return json.dumps({'error':str(error)},ensure_ascii=False)
        if not resolution or not aspect_ratio:
            return json.dumps({'error':'Indica risoluzione e formato del progetto.'})
        if scene_clips is not None:
            if frame_asset_ids or segment_instructions or segment_durations or open_ending:
                return json.dumps({'error':'Per la scena specifica gli input dentro ciascuna clip.'})
            for index, clip in enumerate(scene_clips):
                first = clip.get('first_frame_asset_id')
                last = clip.get('last_frame_asset_id')
                refs = clip.get('reference_asset_ids')
                number = index + 1
                if last and not first:
                    problem = f'Clip {number}: manca il primo frame per usare l’ultimo frame.'
                elif first and refs:
                    problem = f'Clip {number}: scegli tra riferimenti e modalità con frame.'
                elif not first and not refs:
                    problem = f'Clip {number}: mancano riferimenti o primo frame.'
                elif clip.get('join') == 'continuous' and (
                    index == 0 or not first or first != scene_clips[index - 1].get('last_frame_asset_id')
                ):
                    problem = (
                        f'Clip {number}: un raccordo continuo richiede una clip precedente.'
                        if index == 0 else
                        f'Clip {number - 1} → {number}: assegna lo stesso asset verificato '
                        'come ultimo frame della prima clip e primo frame della seconda.'
                    )
                else:
                    continue
                return json.dumps({'error': problem, 'submitted': False}, ensure_ascii=False)
            payload = {'project_id':project_id,'request_id':request_id,'original_instruction':original_instruction,
                       'resolution':resolution,'aspect_ratio':aspect_ratio,'scene':True,
                       'total_duration':total_duration,'segments':scene_clips}
        else:
            frame_asset_ids = frame_asset_ids or []
            segment_instructions = segment_instructions or []
            expected = len(frame_asset_ids) - (0 if open_ending else 1)
            if (not 2<=expected<=13 or len(frame_asset_ids)>14 or len(segment_instructions)!=expected
                    or (segment_durations is not None and len(segment_durations)!=len(segment_instructions))):
                return json.dumps({'error':'Piano non valido: servono da 2 a 13 clip, una per coppia più il finale libero se richiesto.'})
            durations=segment_durations if segment_durations is not None else [None]*len(segment_instructions)
            if any(d is not None and (type(d) is not int or not 3<=d<=10) for d in durations):
                return json.dumps({'error':'Ogni clip con primo e ultimo frame dura 3–10 secondi.'})
            payload={'project_id':project_id,'request_id':request_id,'original_instruction':original_instruction,
                     'frame_asset_ids':frame_asset_ids,'segments':[{'instruction':s,'duration':d}
                         for s,d in zip(segment_instructions,durations)],
                     'resolution':resolution,'aspect_ratio':aspect_ratio}
            if open_ending:
                payload['open_ending'] = True
        try:
            result=call_backend_agent_endpoint('/agent/create-sequence',payload,
                user_id=user_id,project_context_token=project_context_token)
            if not result.get('jobId'):
                return json.dumps({'error':'Avvio non confermato. Non ripetere.', 'uncertain':True})
            if result.get('status')=='UNKNOWN':
                return json.dumps({'error':'Pianificazione non confermata. Verificare il piano nel progetto.',
                                   'job_id':result['jobId'],'uncertain':True})
            return json.dumps({'action':'omni_video_started','generation_type':('scene' if result.get('scene') else 'sequence'),
                'job_id':result['jobId'],'node_id':result.get('nodeId'),
                'status':result.get('status','PROCESSING'),'scene':bool(result.get('scene')),
                'message':('Scena avviata. Le clip saranno montate in un unico video.' if scene_clips is not None
                           else 'Piano sequenza avviato. Le clip saranno unite in un unico video.')},ensure_ascii=False)
        except Exception as error:
            from app.backend_errors import BackendAgentError
            if isinstance(error,BackendAgentError):return json.dumps(error.result(),ensure_ascii=False)
            return json.dumps({'error':'Esito avvio piano sequenza non confermato. Non ripetere.', 'uncertain':True})
