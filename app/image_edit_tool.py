"""Start asynchronous edits on the backend; never generate in the frontend."""
import os
import httpx


async def start_image_edit(project_id, user_id, project_context_token, node_id=None,
                           asset_index=None, instruction='', aspect_ratio=None,
                           variant=None, asset_id=None, request_id=None):
    payload = {
        'project_id':project_id, 'user_id':user_id, 'project_context_token':project_context_token,
        'node_id':node_id, 'asset_index':asset_index, 'asset_id':asset_id,
        'instruction':instruction, 'aspect_ratio':aspect_ratio, 'variant':variant,
        'source':'agent', 'request_id':request_id,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10)) as client:
            response = await client.post(
                os.environ['FRAMEBUTCHER_BACKEND_URL'].rstrip('/') + '/agent/edit-image',
                headers={'X-FrameButcher-Internal-Token':os.environ['BACKEND_INTERNAL_TOKEN']}, json=payload,
            )
        if not response.is_success:
            detail = response.json().get('detail', '') if 'application/json' in response.headers.get('content-type', '') else ''
            return {'action':'error','message':f'Avvio modifica non riuscito (HTTP {response.status_code}): {str(detail)[:300]}. Non dichiarare la modifica avviata.'}
        job = response.json()
        return {
            'action':'image_job_started','operation':'edit', 'job_id':job['job_id'],
            'project_id':project_id,'node_id':job['node_id'],'status':job['status'],'model':job['model'],
            'message':'Modifica immagine presa in carico. Rispondi ora all’utente; il frontend riceverà il risultato via SSE. Non attendere e non rilanciare la modifica.',
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return {'action':'error','message':'Conferma della modifica non ricevuta. Il job potrebbe essere stato registrato: non ripetere automaticamente la richiesta.'}
