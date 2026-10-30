"""Project authorization. No ownership migration or database writes."""
import hashlib
import hmac
import os
from fastapi import HTTPException


def identifier(value):
    if not isinstance(value,str) or not value or len(value)>200 or '/' in value:
        raise HTTPException(400,'Invalid identifier')
    return value


def require_project(db,project_id,user):
    project_id=identifier(project_id)
    if not isinstance(user,dict) or not user.get('sub'):
        raise HTTPException(401,'Identità utente mancante')
    snap=db.collection('projects').document(project_id).get()
    if not snap.exists:raise HTTPException(404,'Project not found')
    data=snap.to_dict() or {}
    if data.get('owner_id'):
        allowed=data['owner_id']==user['sub']
    else:
        # Transitional policy only, until existing projects have a stable owner_id.
        allowed=bool(user.get('email') and user.get('email_verified') is True and data.get('owner_email')==user['email'])
    if not allowed:raise HTTPException(403,'Project access denied')
    if data.get('migration_state') == 'archived':
        raise HTTPException(410, 'Progetto migrato: ricarica l’elenco dei progetti')
    return data


def actor_for_project(db,project_id,user_id):
    """Resolve signed service context against server-recorded login identity."""
    identifier(user_id);identifier(project_id)
    snap=db.collection('projects').document(project_id).get()
    if not snap.exists:raise HTTPException(404,'Project not found')
    project=snap.to_dict() or {}
    if project.get('owner_id'):
        if project['owner_id']!=user_id:raise HTTPException(403,'Project access denied')
        return {'sub':user_id}
    # Session documents are created only after verified Google login. Never read tokens from requests.
    for session in db.collection('auth_sessions').where('user.sub','==',user_id).stream():
        actor=(session.to_dict() or {}).get('user') or {}
        if actor.get('sub')==user_id and actor.get('email_verified') is True and actor.get('email')==project.get('owner_email'):
            return actor
    raise HTTPException(403,'Identità proprietario non risolta: accedi nuovamente al progetto')


def require_signed_project(db,project_id,user_id,token):
    identifier(project_id);identifier(user_id)
    secret=os.environ.get('PROJECT_CONTEXT_SECRET','')
    if not secret:raise HTTPException(503,'Project context authentication not configured')
    expected=hmac.new(secret.encode(),f'{user_id}:{project_id}'.encode(),hashlib.sha256).hexdigest()
    if not isinstance(token,str) or not hmac.compare_digest(expected,token):raise HTTPException(403,'Invalid project context')
    actor=actor_for_project(db,project_id,user_id)
    return require_project(db,project_id,actor)


def require_service_project(db,project_id,request):
    expected=os.environ.get('BACKEND_INTERNAL_TOKEN','')
    token=request.headers.get('X-FrameButcher-Internal-Token','')
    if not expected:raise HTTPException(503,'Internal authentication not configured')
    if not token or not hmac.compare_digest(expected,token):raise HTTPException(401,'Invalid internal token')
    return require_signed_project(db,project_id,request.headers.get('X-FrameButcher-User-Id',''),
                                  request.headers.get('X-FrameButcher-Project-Context',''))


def require_job(db,job_id,user,project_id=None,collection='video_jobs'):
    identifier(job_id)
    snap=db.collection(collection).document(job_id).get()
    if not snap.exists:raise HTTPException(404,'Job not found')
    data=snap.to_dict() or {};actual=data.get('project_id')
    # Legacy orphan jobs are not assigned to the current user implicitly.
    if not actual:raise HTTPException(403,'Job senza progetto: verifica amministrativa necessaria')
    require_project(db,actual,user)
    if project_id is not None and actual!=project_id:raise HTTPException(403,'Job does not belong to the selected project')
    return data


def require_job_project(db,job_id,project_id):
    """Call only after authorizing project_id; reject cross-project video inputs."""
    identifier(job_id)
    snap=db.collection('video_jobs').document(job_id).get()
    if not snap.exists:raise HTTPException(404,'Source video job not found')
    data=snap.to_dict() or {}
    if data.get('project_id')!=project_id:raise HTTPException(403,'Source video belongs to another project')
    return data
