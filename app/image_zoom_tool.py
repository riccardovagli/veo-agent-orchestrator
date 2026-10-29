"""Asynchronous generative zoom out of one original project image."""
import os
import httpx


async def start_image_zoom_out(project_id, user_id, project_context_token,
                               node_id=None, asset_index=None, scale_factor=0.7,
                               instruction=None, variant=None, mode='generative',
                               asset_id=None, request_id=None):
    payload = {
        'project_id':project_id, 'user_id':user_id, 'project_context_token':project_context_token,
        'node_id':node_id, 'asset_index':asset_index, 'asset_id':asset_id,
        'scale_factor':scale_factor, 'instruction':instruction, 'variant':variant,
        'source':'agent', 'request_id':request_id, 'mode':mode,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=10)) as client:
            response = await client.post(
                os.environ['FRAMEBUTCHER_BACKEND_URL'].rstrip('/') + '/agent/zoom-out-image',
                headers={'X-FrameButcher-Internal-Token':os.environ['BACKEND_INTERNAL_TOKEN']},json=payload,
            )
        if not response.is_success:
            detail = response.json().get('detail','') if 'application/json' in response.headers.get('content-type','') else ''
            return {'action':'error','message':f'Zoom out non avviato (HTTP {response.status_code}): {str(detail)[:300]}. Non dichiarare il lavoro avviato.'}
        job=response.json()
        return {'action':'image_job_started','operation':'zoom_out','job_id':job['job_id'],
                'project_id':project_id,'node_id':job['node_id'],'status':job['status'],'model':job['model'],
                'message':'Zoom out preso in carico. Rispondi subito all’utente: il risultato arriverà via SSE. Non attendere e non rilanciare lo zoom out.'}
    except (httpx.HTTPError,ValueError,KeyError):
        return {'action':'error','message':'Conferma dello zoom out non ricevuta. Potrebbe esserci un job registrato: non ripetere automaticamente la richiesta.'}
