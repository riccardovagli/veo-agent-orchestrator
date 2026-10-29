"""Submit frame extraction on the backend; never wait for FFmpeg in MCP."""
import os
import httpx


async def start_frame_extraction(project_id, user_id, project_context_token, scene_node_id,
                                 frame='last', timestamp=None, job_id=None, variant=None, request_id=None):
    payload = {
        'project_id': project_id, 'user_id': user_id, 'project_context_token': project_context_token,
        'scene_node_id': scene_node_id, 'job_id': job_id, 'frame': frame,
        'timestamp': timestamp, 'variant': variant, 'request_id': request_id, 'source': 'agent',
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10)) as client:
            response = await client.post(
                os.environ['FRAMEBUTCHER_BACKEND_URL'].rstrip('/') + '/agent/extract-frame',
                headers={'X-FrameButcher-Internal-Token': os.environ['BACKEND_INTERNAL_TOKEN']}, json=payload,
            )
        if not response.is_success:
            detail = response.json().get('detail', '') if 'application/json' in response.headers.get('content-type', '') else ''
            return {'action': 'error', 'message': f'Estrazione non avviata (HTTP {response.status_code}): {str(detail)[:300]}. Non dichiarare il fotogramma pronto.'}
        job = response.json()
        return {
            'action': 'image_job_started', 'operation': 'extract_frame', 'asset_type': 'frame',
            'job_id': job['job_id'], 'project_id': project_id, 'node_id': job['node_id'],
            'status': job['status'], 'model': job['model'],
            'source_job': job.get('source_job'), 'frame_type': frame, 'timestamp': timestamp,
            'message': 'Estrazione presa in carico. Rispondi subito: il fotogramma arriverà tra gli asset e sarà notificato via SSE. Non attendere e non rilanciare l’estrazione.',
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return {'action': 'error', 'message': 'Conferma dell’estrazione non ricevuta: potrebbe esserci un job registrato. Non ripetere automaticamente la richiesta.'}
