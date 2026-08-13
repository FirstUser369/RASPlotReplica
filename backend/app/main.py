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

from .routers import hecras_api, report
from .routers.hecras_api import router as hecras_router
from .routers.report import router as report_router

app = FastAPI(title="RAS-Plot Replica", version="0.1.0")

app.include_router(hecras_router)
app.include_router(report_router)


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
        candidate = STATIC_DIR / full_path
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
