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
                                       node_id: str = '', node_type: str = '', content: str = '',
                                       depends_on: list[str] | None = None, expected_revision: str = '',
                                       request_id: str = '', patches: list[dict] | None = None) -> dict:
        """Save narrative nodes atomically, preserving production metadata.

        For multiple nodes pass patches, a list of objects with node_id,
        node_type, content and depends_on. Dependencies may reference other
        entries in the same batch. For one node use the original named fields.
        Pass expected_revision from one graph read. A batch counts as one
        graph update in the production plan. A conflict writes nothing.
        """
        if patches is None:
            entries = [{'node_id':node_id, 'node_type':node_type, 'content':content,
                        'depends_on':[] if depends_on is None else depends_on}]
        elif node_id or node_type or content or depends_on is not None:
            return {'error':'Usa patches oppure i campi del singolo nodo.'}
        else:
            entries = patches
        if not entries:
            return {'error':'Indica almeno un nodo per scrittura.'}
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'node_id', 'node_type', 'content', 'depends_on'}:
                return {'error':'Ogni nodo richiede node_id, node_type, content e depends_on.'}
            identifier = entry['node_id']
            deps = entry['depends_on']
            if (not isinstance(identifier, str) or identifier in seen
                    or not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps)):
                return {'error':'ID duplicato o dipendenze non valide.'}
            seen.add(identifier)
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
                saved = []
                for entry in entries:
                    key = entry['node_id']
                    previous = nodes.get(key) or {}
                    nodes[key] = {**previous, 'type':entry['node_type'],
                                  'content':entry['content'], 'depends_on':entry['depends_on'],
                                  'status':previous.get('status', 'VALID'),
                                  'last_updated':firestore.SERVER_TIMESTAMP, 'updated_by':user_id}
                    saved.append({'node_id':key, 'node_type':entry['node_type']})
                for entry in entries:
                    validate_patch(entry['node_id'], entry['node_type'], entry['content'],
                                   entry['depends_on'], nodes)
                    nodes[entry['node_id']]['content'] = entry['content'].strip()
                result = ({'saved':True, **saved[0]} if patches is None
                          else {'saved':True, 'nodes':saved})
                tx.set(ref, {'graph_nodes':nodes}, merge=True)
                tx.create(receipt_ref, {'result':result, 'created_at':firestore.SERVER_TIMESTAMP})
                return result
            try:
                return commit(db.transaction())
            except ValueError as exc:
                return {'error':str(exc)}
        return await asyncio.to_thread(write)
