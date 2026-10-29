"""Asynchronous Nano Banana 2.1 composition, 2 to 14 project images."""
import os
import httpx


async def start_image_composition(project_id, user_id, project_context_token,
                                  asset_node_ids=None, asset_indexes=None, instruction='',
                                  aspect_ratio='1:1', node_id=None, variant=None,
                                  asset_ids=None, request_id=None):
    payload = {
        'project_id':project_id, 'user_id':user_id, 'project_context_token':project_context_token,
        'asset_node_ids':asset_node_ids, 'asset_indexes':asset_indexes, 'asset_ids':asset_ids,
        'instruction':instruction, 'aspect_ratio':aspect_ratio, 'node_id':node_id,
        'variant':variant, 'source':'agent', 'request_id':request_id,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=10)) as client:
            response = await client.post(
                os.environ['FRAMEBUTCHER_BACKEND_URL'].rstrip('/') + '/agent/compose-assets',
                headers={'X-FrameButcher-Internal-Token':os.environ['BACKEND_INTERNAL_TOKEN']},json=payload,
            )
        if not response.is_success:
            detail = response.json().get('detail','') if 'application/json' in response.headers.get('content-type','') else ''
            return {'action':'error','message':f'Composizione non avviata (HTTP {response.status_code}): {str(detail)[:300]}. Non dichiarare il lavoro avviato.'}
        job=response.json()
        return {'action':'image_job_started','operation':'compose','job_id':job['job_id'],
                'project_id':project_id,'node_id':job['node_id'],'status':job['status'],'model':job['model'],
                'message':'Composizione presa in carico. Rispondi subito all’utente: il risultato arriverà via SSE. Non attendere e non rilanciare la composizione.'}
    except (httpx.HTTPError,ValueError,KeyError):
        return {'action':'error','message':'Conferma della composizione non ricevuta. Potrebbe esserci un job registrato: non ripetere automaticamente la richiesta.'}
