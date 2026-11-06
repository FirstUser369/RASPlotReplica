"""Generate disposable Cherry Creek acceptance artifacts from current source."""
from __future__ import annotations

import json
import os
from pathlib import Path

import ezdxf
from pypdf import PdfReader

from backend.app.core.dxf_export import export_dxf
from backend.app.core.engineering_drawing import DrawingBuildConfig, build_engineering_drawing
from backend.app.core.project_file import read_project_file
from backend.app.core.project_restore import restore_candidate_session
from backend.app.core.state_contract import review_panels
from backend.app.routers.hecras_api import OpenRequest, _SESSIONS, open_source
from backend.app.routers.report import render_profile_pdf_from_db


HDF = Path(os.environ["RAS_PLOT_CHERRY_HDF"])
PROJECT = Path(os.environ["RAS_PLOT_CHERRY_PROJECT"])
OUTPUT = Path(os.environ["RAS_PLOT_CHERRY_OUTPUT"])


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    opened = open_source(OpenRequest(path=str(HDF), model_generation=9001))
    session_id = opened["session_id"]
    try:
        document = read_project_file(PROJECT)
        restore_warnings = restore_candidate_session(session_id, document)
        db = _SESSIONS[session_id]["db"]
        # Acceptance rendering uses the approved physical ANSI C sheet without
        # modifying the disposable project document on disk.
        db.header.page_size = "ANSI_C"
        review_panels(db)
        config = DrawingBuildConfig(
            review_notice="PRELIMINARY — ENGINEERING REVIEW REQUIRED")
        drawing = build_engineering_drawing(db, db.panels, config=config)
        pdf = OUTPUT / "Cherry-Creek-PRELIMINARY.pdf"
        dxf = OUTPUT / "Cherry-Creek-PRELIMINARY.dxf"
        render_profile_pdf_from_db(str(pdf), db, panels=db.panels, drawing_config=config)
        export_dxf(dxf, db, panels=db.panels, drawing_config=config)
        pdf_reader = PdfReader(pdf)
        dxf_document = ezdxf.readfile(dxf)
        modelspace = dxf_document.modelspace()
        placed = [annotation for panel in drawing.panels
                  for annotation in panel.annotations if annotation.leader_required]
        result = {
            "pdf": str(pdf),
            "dxf": str(dxf),
            "pages": len(drawing.panels),
            "collisions": len(drawing.presentation.annotation_warnings),
            "collision_warnings": list(drawing.presentation.annotation_warnings),
            "offset_labels": len(placed),
            "offset_identities": [item.identity for item in placed],
            "restore_warnings": restore_warnings,
            "panel_ids": [panel.display_id for panel in drawing.panels],
            "datum": drawing.vertical_datum,
            "datum_provenance": drawing.datum_provenance,
            "page_inches": [
                [round(float(page.mediabox.width) / 72.0, 3),
                 round(float(page.mediabox.height) / 72.0, 3)]
                for page in pdf_reader.pages
            ],
            "preliminary_notice_pages": sum(
                "PRELIMINARY" in (page.extract_text() or "")
                for page in pdf_reader.pages),
            "pdf_collision_warning_pages": sum(
                "Potential annotation collision" in (page.extract_text() or "")
                for page in pdf_reader.pages),
            "dxf_leaders": len(modelspace.query(
                'LINE[layer=="ANNOTATION_LEADERS"]')),
        }
        (OUTPUT / "acceptance-evidence.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        _SESSIONS.pop(session_id, None)


if __name__ == "__main__":
    main()
