"""Repeatable synthetic performance probe; not evidence of real-model support."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import tracemalloc

from backend.app.core.dxf_export import export_dxf
from backend.app.core.hdf_reader import CrossSection, Plan, ProfileResult, ReachGeometry
from backend.app.core.rasplot_db import PanelDef, auto_panels, build_db_from_plan, recalc_cumdist
from backend.app.core.state_contract import review_panels
from backend.app.routers.report import render_profile_pdf_from_db


def timed(operation):
    start = time.perf_counter()
    value = operation()
    return value, time.perf_counter() - start


def fixture(count: int, profile_count: int = 3) -> Plan:
    cross_sections = [
        CrossSection("Synthetic River", "Synthetic Reach", str(count - index), "",
                     10.0 if index < count - 1 else 0.0, 0.0, 10.0,
                     station_elevation=[(0.0, 100.0), (10.0, 101.0)],
                     minchel=100.0 + index / max(1, count), index=index,
                     ground_provenance={"state": "VALID", "method": "SYNTHETIC_BENCHMARK"})
        for index in range(count)
    ]
    identities = [item.identity for item in cross_sections]
    profiles = []
    for profile_index in range(profile_count):
        values = [101.0 + profile_index + index / max(1, count) for index in range(count)]
        profiles.append(ProfileResult(
            f"Synthetic {profile_index + 1}", values, xs_ids=identities,
            profile_id=f"synthetic-{profile_index + 1}", key=f"synthetic-{profile_index + 1}",
            source_path="synthetic-benchmark", source_dataset="synthetic-benchmark",
            values=[{"value": value, "state": "VALID", "provenance": "SYNTHETIC_BENCHMARK"}
                    for value in values]))
    return Plan("Synthetic benchmark", Path("synthetic.p01.hdf"), None, None, None, None,
                profiles=profiles,
                reaches=[ReachGeometry("Synthetic River", "Synthetic Reach", [], cross_sections, [],
                                       source_order_status="SOURCE_UPSTREAM_FIRST")],
                source_units="US Customary", model_id=f"synthetic-{count}")


def run(count: int) -> dict:
    tracemalloc.start()
    plan = fixture(count)
    db, build_seconds = timed(lambda: build_db_from_plan(plan))
    _, recalc_seconds = timed(lambda: recalc_cumdist(db.working))
    extent = max(row.cumdist for row in db.working if row.kind == "xs")
    db.header.page_size = "ANSI_C"
    panels, panel_seconds = timed(lambda: auto_panels(
        db.working, h_scale=max(500.0, extent / 17.0), v_scale=5.0,
        page_size="ANSI_C", station_range=(0.0, extent)))
    # The export benchmark intentionally uses one physical-scale-valid panel.
    panel = PanelDef(1, 0.0, extent, 95.0, 110.0,
                     h_scale=max(500.0, extent / 17.0), v_scale=5.0)
    db.panels = [panel]
    review_panels(db)
    with tempfile.TemporaryDirectory(prefix="rasplot-benchmark-") as directory:
        pdf = Path(directory) / "benchmark.pdf"
        dxf = Path(directory) / "benchmark.dxf"
        _, pdf_seconds = timed(lambda: render_profile_pdf_from_db(str(pdf), db, panels=[panel]))
        _, dxf_seconds = timed(lambda: export_dxf(dxf, db, panels=[panel]))
        sizes = {"pdf_bytes": pdf.stat().st_size, "dxf_bytes": dxf.stat().st_size}
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"cross_sections": count, "profiles": len(plan.profiles),
            "working_build_seconds": round(build_seconds, 4),
            "recalculate_seconds": round(recalc_seconds, 4),
            "auto_panel_seconds": round(panel_seconds, 4),
            "generated_panel_count": len(panels),
            "pdf_seconds": round(pdf_seconds, 4), "dxf_seconds": round(dxf_seconds, 4),
            "peak_mib": round(peak / 1024 / 1024, 2), **sizes}


if __name__ == "__main__":
    print(json.dumps({"synthetic_only": True,
                      "results": [run(size) for size in (100, 500, 1000)]}, indent=2))
