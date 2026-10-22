"""Portable project Save/Open API with candidate-session rollback."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.local_state import LocalRoute
from ..core.project_file import (
    ProjectFileError, read_project_file, verify_project_source, write_project_file,
)
from ..core.project_restore import (
    ProjectRestoreError, build_project_document, restore_candidate_session,
)
from ..release import BUILD_ID, VERSION
from . import hecras_api

router = APIRouter(prefix="/api/project", route_class=LocalRoute)


class OpenProjectRequest(BaseModel):
    path: str
    replacement_path: str | None = None
    model_generation: int = 0


class SaveProjectRequest(BaseModel):
    path: str | None = None
    overwrite: bool = False


def _project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.name.lower().endswith(".rasplot.json"):
        raise HTTPException(400, "Project path must be an absolute .rasplot.json file path.")
    return path.resolve(strict=False)


def _local_picker(request: Request, mode: Literal["open", "save"]) -> dict:
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Project file selection requires the local Windows application.")
    from ..core.windows_file_picker import choose_project_open, choose_project_save
    try:
        return {"path": (choose_project_open() if mode == "open" else choose_project_save())}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/browse-open")
def browse_project_open(request: Request) -> dict:
    return _local_picker(request, "open")


@router.post("/browse-save")
def browse_project_save(request: Request) -> dict:
    return _local_picker(request, "save")


@router.post("/open")
def open_project(req: OpenProjectRequest) -> dict:
    project_path = _project_path(req.path)
    try:
        document = read_project_file(project_path)
        verification = verify_project_source(
            project_path, document.source, replacement_path=req.replacement_path)
    except ProjectFileError as exc:
        raise HTTPException(400, str(exc)) from exc
    if verification.status != "VERIFIED" or not verification.resolved_path:
        status = 404 if verification.status == "SOURCE_MISSING" else 409
        raise HTTPException(status, {
            "message": "The saved HDF source could not be verified.",
            "source_status": verification.status,
            "warnings": list(verification.warnings),
        })
    candidate_id = None
    try:
        payload = hecras_api.open_source(hecras_api.OpenRequest(
            path=verification.resolved_path, model_generation=req.model_generation))
        candidate_id = payload["session_id"]
        warnings = restore_candidate_session(candidate_id, document)
        session = hecras_api._SESSIONS[candidate_id]
        session["project_path"] = str(project_path)
        session["project_dirty"] = False
        hecras_api.commit_session(candidate_id)
        payload = hecras_api.plan_to_dict(session["plan"])
        payload.update({
            "session_id": candidate_id,
            "model_context": session["model_context"].to_dict(),
            "db": session["db"].to_dict(),
            "requires_reach_selection": False,
            "project_path": str(project_path),
            "source_relocated": verification.relocated,
            "warnings": warnings,
        })
        return payload
    except (ProjectRestoreError, ValueError, HTTPException) as exc:
        if candidate_id:
            hecras_api._SESSIONS.pop(candidate_id, None)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        if candidate_id:
            hecras_api._SESSIONS.pop(candidate_id, None)
        raise


@router.post("/{sid}/save")
def save_project(sid: str, req: SaveProjectRequest) -> dict:
    session = hecras_api._SESSIONS.get(sid)
    if session is None:
        raise HTTPException(404, "Unknown session")
    context = session.get("model_context")
    if context is None or context.status != "ACTIVE":
        raise HTTPException(409, "Commit the active HDF session before saving a project.")
    raw_path = req.path or session.get("project_path")
    if not raw_path:
        raise HTTPException(400, "Choose a project destination with Save As.")
    path = _project_path(raw_path)
    overwrite = bool(req.overwrite)
    try:
        document = build_project_document(session, path, VERSION, BUILD_ID)
        written = write_project_file(path, document, overwrite=overwrite)
    except (ProjectFileError, ProjectRestoreError, OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    session["project_path"] = str(written)
    session["project_dirty"] = False
    return {"ok": True, "path": str(written), "project_id": document.project_id,
            "saved_at_utc": document.saved_at_utc}
