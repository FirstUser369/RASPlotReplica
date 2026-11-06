import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from backend.app.core.export_jobs import (
    ExportCancelled, cancel_active_export_jobs, cancel_export_job, get_export_job, start_export_job,
)
from backend.app.core.dxf_export import export_dxf
from backend.app.routers.report import render_profile_pdf_from_db
from backend.app.routers import report, hecras_api
from backend.tests.test_export_scale import sample


def wait_for(sid, job_id, timeout=5):
    limit = time.time() + timeout
    while time.time() < limit:
        job = get_export_job(sid, job_id)
        if job.state in {"COMPLETED", "FAILED", "CANCELLED"}:
            return job
        time.sleep(.01)
    raise AssertionError("export job did not finish")


class ExportJobTests(unittest.TestCase):
    def test_shutdown_requests_cancellation_for_unfinished_jobs(self):
        release = Event()

        def worker(update, check_cancelled):
            update("RENDERING", "Rendering")
            release.wait(2)
            check_cancelled()
            return {"path": "must-not-complete.pdf"}

        job = start_export_job("shutdown-session", "pdf", worker)
        for _ in range(100):
            if get_export_job("shutdown-session", job.job_id).state == "RUNNING":
                break
            time.sleep(.01)
        self.assertGreaterEqual(cancel_active_export_jobs(), 1)
        release.set()
        cancelled = wait_for("shutdown-session", job.job_id)
        self.assertEqual(cancelled.state, "CANCELLED")
        self.assertIsNone(cancelled.result)

    def test_truthful_stage_lifecycle_and_result(self):
        seen = []
        def worker(update, check):
            for stage in ("VALIDATION", "DRAWING_DEFINITION", "RENDERING", "WRITING", "FINALIZATION"):
                update(stage, stage.lower())
                check()
                seen.append(stage)
            return {"path": "profile.pdf", "size_bytes": 3, "format": "pdf"}
        job = start_export_job("job-session", "pdf", worker)
        complete = wait_for("job-session", job.job_id)
        self.assertEqual(complete.state, "COMPLETED")
        self.assertEqual(seen, ["VALIDATION", "DRAWING_DEFINITION", "RENDERING", "WRITING", "FINALIZATION"])
        self.assertEqual(complete.output_filename, "profile.pdf")

    def test_cancel_at_each_stage_is_cooperative_and_never_reports_success(self):
        for stage in ("VALIDATION", "DRAWING_DEFINITION", "RENDERING", "WRITING", "FINALIZATION"):
            with self.subTest(stage=stage):
                entered = Event()
                def worker(update, check, current_stage=stage):
                    update(current_stage, current_stage.lower())
                    entered.set()
                    while True:
                        check()
                        time.sleep(.005)
                sid = "cancel-" + stage.lower()
                job = start_export_job(sid, "dxf", worker)
                self.assertTrue(entered.wait(1))
                cancel_export_job(sid, job.job_id)
                cancelled = wait_for(sid, job.job_id)
                self.assertEqual(cancelled.state, "CANCELLED")
                self.assertIsNone(cancelled.result)

    def test_jobs_are_session_owned(self):
        job = start_export_job("owner", "pdf", lambda update, check: {"path": "x.pdf"})
        with self.assertRaises(KeyError):
            get_export_job("other", job.job_id)

    def test_pdf_and_dxf_renderers_report_real_stages_and_honor_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            for renderer, suffix in ((render_profile_pdf_from_db, ".pdf"), (export_dxf, ".dxf")):
                stages = []
                path = Path(directory) / ("drawing" + suffix)
                renderer(path, sample(), status_callback=lambda stage, message: stages.append(stage))
                self.assertTrue(path.is_file())
                self.assertEqual(stages[:2], ["DRAWING_DEFINITION", "RENDERING"])
                self.assertIn("WRITING", stages)
                def cancelled():
                    raise ExportCancelled("cancel")
                with self.assertRaises(ExportCancelled):
                    renderer(Path(directory) / ("cancel" + suffix), sample(), cancel_check=cancelled)

    def test_background_route_uses_snapshot_and_registers_only_final_artifact(self):
        sid = "background-route"
        db = sample()
        hecras_api._SESSIONS[sid] = {"db": db, "last_access": time.time()}
        self.addCleanup(lambda: hecras_api._SESSIONS.pop(sid, None))
        started = report.start_profile_export(report.ExportOptions(
            session_id=sid, format="pdf", use_current_view=True))
        db.header.stream_name = "mutated after snapshot"
        completed = wait_for(sid, started["job_id"], timeout=10)
        self.assertEqual(completed.state, "COMPLETED", completed.error)
        artifact = Path(completed.result["path"])
        self.addCleanup(lambda: artifact.unlink(missing_ok=True))
        self.addCleanup(lambda: artifact.with_name(artifact.name + ".rasplot-artifact.json").unlink(missing_ok=True))
        self.assertTrue(artifact.is_file())
        self.assertFalse(Path(str(artifact) + ".partial").exists())


if __name__ == "__main__":
    unittest.main()
