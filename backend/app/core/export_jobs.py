"""Bounded, session-owned background export jobs.

Jobs contain an immutable export snapshot supplied by the report router.  This
module deliberately knows nothing about HDF sessions or drawing geometry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any, Callable
import secrets
import time


TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
STAGES = {
    "QUEUED", "VALIDATION", "DRAWING_DEFINITION", "RENDERING", "WRITING",
    "FINALIZATION", "COMPLETED", "FAILED", "CANCELLED",
}


class ExportCancelled(RuntimeError):
    """Raised cooperatively before an unfinished artifact is finalized."""


@dataclass
class ExportJob:
    job_id: str
    session_id: str
    export_type: str
    output_filename: str = ""
    state: str = "QUEUED"
    stage: str = "QUEUED"
    message: str = "Export is queued."
    result: dict[str, Any] | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    cancel_event: Event = field(default_factory=Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "session_id": self.session_id,
            "export_type": self.export_type, "output_filename": self.output_filename,
            "state": self.state, "stage": self.stage, "message": self.message,
            "result": self.result, "error": self.error,
        }


_LOCK = RLock()
_JOBS: dict[str, ExportJob] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rasplot-export")
_MAX_JOBS = 128


def _prune() -> None:
    terminal = sorted(
        (job for job in _JOBS.values() if job.state in TERMINAL_STATES),
        key=lambda job: job.updated,
    )
    while len(_JOBS) >= _MAX_JOBS and terminal:
        _JOBS.pop(terminal.pop(0).job_id, None)
    if len(_JOBS) >= _MAX_JOBS:
        raise RuntimeError("Export job capacity reached; wait for an active export to finish.")


def start_export_job(session_id: str, export_type: str,
                     worker: Callable[[Callable[[str, str], None], Callable[[], None]], dict[str, Any]]) -> ExportJob:
    with _LOCK:
        _prune()
        job = ExportJob(secrets.token_hex(16), session_id, export_type)
        _JOBS[job.job_id] = job

    def update(stage: str, message: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown export stage {stage!r}.")
        with _LOCK:
            if job.cancel_event.is_set():
                raise ExportCancelled("Export cancelled by user.")
            job.state = "RUNNING"
            job.stage = stage
            job.message = message
            job.updated = time.time()

    def check_cancelled() -> None:
        if job.cancel_event.is_set():
            raise ExportCancelled("Export cancelled by user.")

    def run() -> None:
        try:
            result = worker(update, check_cancelled)
            with _LOCK:
                job.result = result
                job.output_filename = str(result.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
                job.state = job.stage = "COMPLETED"
                job.message = "Export completed successfully."
                job.updated = time.time()
        except ExportCancelled:
            with _LOCK:
                job.state = job.stage = "CANCELLED"
                job.message = "Export cancelled. No artifact was finalized."
                job.updated = time.time()
        except Exception as exc:  # endpoint reports the actionable renderer/preflight message
            with _LOCK:
                job.state = job.stage = "FAILED"
                job.error = str(getattr(exc, "detail", None) or exc)
                job.message = "Export failed. No successful artifact was registered."
                job.updated = time.time()

    _EXECUTOR.submit(run)
    return job


def get_export_job(session_id: str, job_id: str) -> ExportJob:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.session_id != session_id:
            raise KeyError(job_id)
        return job


def cancel_export_job(session_id: str, job_id: str) -> ExportJob:
    job = get_export_job(session_id, job_id)
    with _LOCK:
        if job.state not in TERMINAL_STATES:
            job.cancel_event.set()
            job.message = "Cancellation requested; cleaning up unfinished output."
            job.updated = time.time()
        return job


def cancel_active_export_jobs() -> int:
    """Request cooperative cancellation for every unfinished job at shutdown."""
    with _LOCK:
        active = [job for job in _JOBS.values() if job.state not in TERMINAL_STATES]
        for job in active:
            job.cancel_event.set()
            job.message = "Application shutdown requested; cancelling unfinished export."
            job.updated = time.time()
        return len(active)
