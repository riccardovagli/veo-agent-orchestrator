"""Targeted graph tools for Stanley; reuses existing MCP auth and Firestore."""
import asyncio
from copy import deepcopy
from datetime import datetime
import re
import unicodedata

from google.cloud import firestore


def validate_patch(node_id, node_type, content, depends_on, nodes):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', node_id):
        raise ValueError('ID nodo non valido')
    if node_type not in ('Concept', 'Asset', 'Scene'):
        raise ValueError('Tipo nodo non valido')
    if not isinstance(content, str) or not content.strip() or len(content) > 40000:
        raise ValueError('Contenuto non valido')
    if len(depends_on) > 100 or len(set(depends_on)) != len(depends_on):
        raise ValueError('Dipendenze non valide')
    if node_id in depends_on or any(dep not in nodes for dep in depends_on):
        raise ValueError('Dipendenza inesistente o circolare')
    def reaches(start, target, seen):
        if start == target:
            return True
        if start in seen:
            return False
        seen.add(start)
        return any(reaches(dep, target, seen) for dep in (nodes.get(start) or {}).get('depends_on', []))
    if any(reaches(dep, node_id, set()) for dep in depends_on):
        raise ValueError('Dipendenza circolare')


def register_stanley_tools(mcp, get_db, validate_graph_request):
    @mcp.tool()
    async def read_stanley_project(project_id: str, user_id: str, project_context_token: str,
                                   node_ids: list[str] | None = None, query: str = '',
                                   offset: int = 0, limit: int = 20) -> dict:
        """Read relevant project graph nodes. Empty selection returns a compact index.
        Query names treat spaces/underscores/hyphens equivalently. A query ending
        in asset N selects that exact variant in asset_matches, including its UUID.
        Select node_ids to get full narrative content and asset/job identifiers.
        graph_revision is required for the subsequent validated write.
        """
        error = await asyncio.to_thread(validate_graph_request, project_id, user_id, project_context_token)
        if error:
            return {'error':str(error)}
        if not 0 <= offset <= 10000 or not 1 <= limit <= 40 or len(node_ids or []) > 40:
            return {'error':'Selezione troppo ampia'}
        def read():
            snap = get_db().collection('agent_orchestration_state').document(project_id).get()
            nodes = (snap.to_dict() or {}).get('graph_nodes') or {}
            def normalize(value):
                value = unicodedata.normalize('NFKC', str(value)).casefold()
                return ' '.join(re.sub(r'[_\-·]+', ' ', value).split())
            requested_index = None
            label = query.strip()
            parsed = re.search(r'(?:\s|·)+asset\s+(\d+)\s*$', label, re.I)
            if parsed:
                requested_index = int(parsed.group(1))
                label = label[:parsed.start()].strip()
            needle = normalize(label)
            matches = [(key, node) for key, node in sorted(nodes.items())
                       if (not node_ids or key in node_ids)
                       and (not needle or needle in normalize(key+' '+str(node.get('content', ''))))]
            selected = matches[offset:offset+limit]
            fields = ('type', 'content', 'status', 'depends_on', 'generated_assets',
                      'generated_videos', 'video_jobs', 'video_job_id', 'last_video_job_id',
                      'scene_order', 'production_status')
            def clean(value):
                if isinstance(value, dict):
                    return {k:clean(v) for k,v in value.items()
                            if k not in ('gcs_uri', 'signed_url', 'thumbnail_url', 'thumbnail_uri', 'prompt')}
                if isinstance(value, list):
                    return [clean(v) for v in value]
                if isinstance(value, datetime):
                    return value.isoformat()
                return value
            result = {key:{field:clean(node[field]) for field in fields if field in node}
                      for key,node in selected}
            if not node_ids:
                for node in result.values():
                    node['content'] = str(node.get('content', ''))[:240]
                    node.pop('generated_assets', None);node.pop('generated_videos', None)
            asset_matches = []
            if query.strip():
                for key, node in selected:
                    for position, asset in enumerate(node.get('generated_assets') or []):
                        if not isinstance(asset, dict) or not asset.get('asset_id'):
                            continue
                        try:
                            index = int(asset.get('asset_index', position))
                        except (TypeError, ValueError):
                            continue
                        if requested_index is not None and index != requested_index:
                            continue
                        asset_matches.append({'asset_id':asset['asset_id'], 'node_id':key,
                            'asset_index':index, 'asset_type':asset.get('asset_type'),
                            'mention':key+' · asset '+str(index)})
            return {'nodes':result, 'asset_matches':asset_matches[:40],
                    'asset_matches_truncated':len(asset_matches)>40, 'total_matches':len(matches),
                    'next_offset':offset+limit if offset+limit < len(matches) else None,
                    'graph_revision':snap.update_time.isoformat() if snap.exists else 'absent'}
        return await asyncio.to_thread(read)

    @mcp.tool()
    async def apply_stanley_node_patch(project_id: str, user_id: str, project_context_token: str,
                                       node_id: str, node_type: str, content: str,
                                       depends_on: list[str], expected_revision: str,
                                       request_id: str) -> dict:
        """Save one user-agreed narrative node, preserving production metadata.
        Read current graph_revision first; stale writes return conflict without changes.
        """
        error = await asyncio.to_thread(validate_graph_request, project_id, user_id, project_context_token)
        if error:
            return {'error':str(error)}
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            return {'error':'request_id non valido'}
        def write():
            db = get_db()
            ref = db.collection('agent_orchestration_state').document(project_id)
            receipt_ref = db.collection('projects').document(project_id).collection('stanley_graph_receipts').document(request_id)
            @firestore.transactional
            def commit(tx):
                project = db.collection('projects').document(project_id).get(transaction=tx).to_dict() or {}
                if project.get('owner_id') != user_id:
                    return {'error':'Project access denied'}
                snap = ref.get(transaction=tx)
                receipt = receipt_ref.get(transaction=tx)
                if receipt.exists:
                    return receipt.to_dict()['result']
                revision = snap.update_time.isoformat() if snap.exists else 'absent'
                if revision != expected_revision:
                    return {'error':'graph_conflict', 'message':'Il grafo è cambiato: rileggi prima di aggiornare.'}
                nodes = deepcopy((snap.to_dict() or {}).get('graph_nodes') or {})
                validate_patch(node_id, node_type, content, depends_on, nodes)
                old = nodes.get(node_id) or {}
                nodes[node_id] = {**old, 'type':node_type, 'content':content.strip(),
                                  'depends_on':depends_on, 'status':old.get('status', 'VALID'),
                                  'last_updated':firestore.SERVER_TIMESTAMP, 'updated_by':user_id}
                result = {'saved':True, 'node_id':node_id, 'node_type':node_type}
                tx.set(ref, {'graph_nodes':nodes}, merge=True)
                tx.create(receipt_ref, {'result':result, 'created_at':firestore.SERVER_TIMESTAMP})
                return result
            try:
                return commit(db.transaction())
            except ValueError as exc:
                return {'error':str(exc)}
        return await asyncio.to_thread(write)
