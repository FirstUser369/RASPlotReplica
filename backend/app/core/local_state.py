"""Serialized local session transactions and session-owned expiring artifacts."""
from functools import wraps
import inspect
from pathlib import Path
from threading import RLock
import secrets
import tempfile
import time
import json
from fastapi.responses import FileResponse
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

STATE_LOCK = RLock()
ARTIFACTS = {}
ARTIFACT_TTL = 24 * 60 * 60
SESSION_LIMIT = 32
SESSION_TTL = 24 * 60 * 60
ARTIFACT_LIMIT = 256
ACTIVE_DOWNLOADS = {}


def cleanup_sessions():
    from ..routers.hecras_api import _SESSIONS
    now = time.time()
    for sid, session in list(_SESSIONS.items()):
        if now - session.get('last_access', now) >= SESSION_TTL:
            _SESSIONS.pop(sid, None)


class OwnedDownload(FileResponse):
    """Pin until ASGI finishes streaming, not merely until the route returns."""
    def __init__(self, path, **kwargs):
        self.owned_key = str(Path(path).resolve())
        ACTIVE_DOWNLOADS[self.owned_key] = ACTIVE_DOWNLOADS.get(self.owned_key, 0) + 1
        super().__init__(path, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with STATE_LOCK:
                count = ACTIVE_DOWNLOADS.get(self.owned_key, 1) - 1
                if count:
                    ACTIVE_DOWNLOADS[self.owned_key] = count
                else:
                    ACTIVE_DOWNLOADS.pop(self.owned_key, None)


def require_artifact_capacity(sid):
    cleanup_artifacts()
    cleanup_previous_artifacts()
    if len(ARTIFACTS) >= 2048:
        raise HTTPException(409, 'Local retained export capacity reached. Existing artifacts expire after 24 hours; save required downloads before restarting.')
    if sum(owner == sid for owner, _ in ARTIFACTS.values()) >= ARTIFACT_LIMIT:
        raise HTTPException(409, 'This session has reached 256 retained exports. Start a new session; existing downloads remain valid until expiry.')


class LocalRoute(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        signature = inspect.signature(endpoint, eval_str=True)
        request_names = [name for name, parameter in signature.parameters.items()
                         if parameter.annotation is Request]
        if len(request_names) > 1:
            raise TypeError(f'{endpoint.__name__} declares multiple FastAPI Request parameters.')
        inject_request = not request_names
        request_name = request_names[0] if request_names else '_local_request'

        @wraps(endpoint)
        def transaction(*args, **kw):
            with STATE_LOCK:
                cleanup_sessions()
                # Preserve a real endpoint Request argument. Only remove the
                # private dependency when this wrapper introduced it.
                request = kw.pop(request_name, None) if inject_request else kw.get(request_name)
                if request is not None:
                    sid = request.path_params.get('sid')
                    if sid is None:
                        sid = next((getattr(v, 'session_id', None) for v in kw.values() if getattr(v, 'session_id', None)), None)
                    token = request.headers.get('x-rasplot-context')
                    if sid and token:
                        from ..routers.hecras_api import _SESSIONS
                        try:
                            client, generation = token.rsplit(':', 1)
                            generation = int(generation)
                            if not client or len(client) > 64 or generation < 0: raise ValueError()
                        except ValueError:
                            raise HTTPException(400, 'Invalid request context')
                        session = _SESSIONS.get(sid)
                        if session is not None:
                            versions = session.setdefault('client_contexts', {})
                            if generation < versions.get(client, -1):
                                raise HTTPException(409, 'Superseded project/reach/profile request; reload the active context.')
                            versions[client] = generation
                    expected_reach = request.headers.get('x-rasplot-reach')
                    if sid and expected_reach is not None and not request.url.path.endswith('/select-reach'):
                        from ..routers.hecras_api import _SESSIONS
                        session = _SESSIONS.get(sid)
                        if session and session.get('reach_idx') is not None and expected_reach != str(session.get('reach_idx')):
                            raise HTTPException(409, 'The active reach changed; reload before editing or exporting.')
                    expected_model = request.headers.get('x-rasplot-model')
                    if sid and expected_model and request.method != 'DELETE':
                        from ..routers.hecras_api import _SESSIONS
                        session = _SESSIONS.get(sid)
                        context = session.get('model_context') if session else None
                        if context and expected_model != context.model_id:
                            raise HTTPException(409, 'The active model changed; discard this obsolete response and reload the active model.')
                    if sid:
                        from ..routers.hecras_api import _SESSIONS
                        if sid in _SESSIONS:
                            _SESSIONS[sid]['last_access'] = time.time()
                return endpoint(*args, **kw)
        transaction.__signature__ = signature if not inject_request else signature.replace(parameters=[
            *signature.parameters.values(),
            inspect.Parameter('_local_request', inspect.Parameter.KEYWORD_ONLY, annotation=Request),
        ])
        super().__init__(path, transaction, **kwargs)


def cache_root():
    root = Path(tempfile.gettempdir()).resolve() / "rasplotreplica"
    if root.resolve() != root:
        raise HTTPException(400, "Artifact cache must not be redirected by a filesystem link.")
    return root


def owned_path(sid, path):
    root = (cache_root() / sid).resolve()
    try:
        root.relative_to(cache_root())
        candidate = Path(path).resolve()
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Path outside session cache")
    record = ARTIFACTS.get(str(candidate))
    if not record or record[0] != sid or time.time() - record[1] >= ARTIFACT_TTL:
        raise HTTPException(404, "Artifact is unknown, expired or belongs to another session; generate it again.")
    return candidate


def register_artifact(sid, path):
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(cache_root() / sid)
    except ValueError:
        raise HTTPException(400, "Artifact path outside its session directory")
    cleanup_artifacts()
    # Only this marker authorizes later cleanup after a process restart.
    marker = candidate.with_name(candidate.name + '.rasplot-artifact.json')
    marker.write_text(json.dumps({'sid': sid, 'file': candidate.name, 'created': time.time()}), encoding='utf-8')
    ARTIFACTS[str(candidate)] = (sid, time.time())


def discard_failed_artifact(sid, path):
    if not path:
        return
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(cache_root() / sid)
        candidate.unlink(missing_ok=True)
        candidate.with_name(candidate.name + '.rasplot-artifact.json').unlink(missing_ok=True)
        ARTIFACTS.pop(str(candidate), None)
    except (ValueError, OSError):
        pass


def cleanup_artifacts():
    # Delete only individually registered files; never recursively delete user paths.
    for name, (sid, created) in list(ARTIFACTS.items()):
        if time.time() - created < ARTIFACT_TTL:
            continue
        if ACTIVE_DOWNLOADS.get(name):
            continue
        path = Path(name)
        try:
            path.resolve().relative_to(cache_root() / sid)
            path.unlink(missing_ok=True)
            path.with_name(path.name + '.rasplot-artifact.json').unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
        ARTIFACTS.pop(name, None)


def cleanup_previous_artifacts():
    """Only expired marked artifacts, resolved inside cache; never user inputs."""
    root = cache_root()
    if not root.exists():
        return
    for marker in root.glob('*/*.rasplot-artifact.json'):
        try:
            marker.resolve().relative_to(root)
            record = json.loads(marker.read_text(encoding='utf-8'))
            candidate = marker.parent / record['file']
            if (marker.parent.name != record['sid'] or candidate.name != record['file'] or
                    candidate.resolve().parent != marker.parent.resolve() or
                    time.time() - record['created'] < ARTIFACT_TTL or
                    ACTIVE_DOWNLOADS.get(str(candidate.resolve()))):
                continue
            candidate.unlink(missing_ok=True)
            marker.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            continue


def artifact_name(prefix, suffix):
    return f"{prefix}_{secrets.token_hex(16)}{suffix}"
