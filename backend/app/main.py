"""FastAPI application entry point.

Run:
    python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765

Serves the React build at / (during development, the dev server runs on
:5173 with a proxy to /api).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .routers import hecras_api, report, project_api
from .routers.hecras_api import router as hecras_router
from .routers.report import router as report_router
from .routers.project_api import router as project_router

from .release import VERSION
app = FastAPI(title="RAS-Plot Replica", version=VERSION)


@app.on_event('startup')
def expire_previous_artifacts():
    from .core.local_state import cleanup_previous_artifacts
    cleanup_previous_artifacts()


from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from .core.errors import error_code, log


@app.exception_handler(StarletteHTTPException)
async def expected_error(request, exc):
    return JSONResponse({'detail': exc.detail, 'code': error_code(exc, request.url.path)},
                        status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    log.error('Unexpected failure on %s', request.url.path, exc_info=exc)
    return JSONResponse({'detail': 'An unexpected application error occurred. See the local diagnostic log.',
                         'code': 'INTERNAL_ERROR'}, status_code=500)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Rejected NaN/Infinity inputs must not break serialization of the error
    # response itself. This changes error descriptions, never hydraulic values.
    import math
    def safe(value):
        if isinstance(value,float) and not math.isfinite(value): return str(value)
        if isinstance(value,dict): return {k:safe(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)): return [safe(v) for v in value]
        if isinstance(value,Exception): return str(value)
        return value
    return JSONResponse({"detail":safe(exc.errors()), "code":"INVALID_INPUT"},status_code=422)

@app.middleware("http")
async def local_requests(request, call_next):
    from urllib.parse import urlsplit
    host = request.headers.get("host", "")
    try:
        hostname = urlsplit("http://" + host).hostname
        origin = request.headers.get("origin")
        origin_url = urlsplit(origin) if origin else None
    except ValueError:
        return JSONResponse({"detail":"Invalid local Host or Origin", "code":"SECURITY_REJECTION"},status_code=403)
    peer = request.client.host if request.client else ""
    testing = peer == "testclient" and hostname == "testserver"
    if not testing and (peer not in {"127.0.0.1", "::1"} or hostname not in {"127.0.0.1", "localhost", "::1"}):
        return JSONResponse({"detail":"RAS-Plot accepts local loopback requests only.", "code":"SECURITY_REJECTION"}, status_code=403)
    if origin_url and (origin_url.netloc != host or origin_url.scheme != request.url.scheme):
        return JSONResponse({"detail":"Request origin must match this local application.", "code":"SECURITY_REJECTION"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail":"Cross-site requests are not permitted.", "code":"SECURITY_REJECTION"}, status_code=403)
    return await call_next(request)


app.include_router(hecras_router)
app.include_router(report_router)
app.include_router(project_router)


# ---- Serve the React build (built into backend/app/static) -----------------
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Do not shadow API (or docs) with the SPA shell
        if full_path == "api" or full_path.startswith("api/") or full_path in ("docs", "openapi.json", "redoc"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return JSONResponse({"detail":"Path outside static root"}, status_code=404)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/")
    def dev_placeholder() -> dict:
        return {
            "message": "RAS-Plot Replica backend (no built frontend found). "
                       "Run `npm run dev` in frontend/ for the dev server, then "
                       "open http://localhost:5173 (frontend proxies /api to "
                       "127.0.0.1:8765).",
            "api_docs": "/docs",
        }
