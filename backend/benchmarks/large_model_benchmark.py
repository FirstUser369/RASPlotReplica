"""Reproducible Phase 6 synthetic large-model benchmark.

Run from the repository root with the portable/runtime Python::

    python -m backend.benchmarks.large_model_benchmark --sizes 1000 5000 10000 25000

The fixture uses exact composite XS identities, five hydraulic profiles, dry
gaps, and deterministic channel lengths. Timings exclude fixture construction.
"""
from __future__ import annotations

import argparse
import gc
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

import h5py
import numpy as np

from backend.app.core.dxf_export import export_dxf
from backend.app.core.hdf_reader import CrossSection, HdfReader, Plan, ProfileResult, ReachGeometry
from backend.app.core.hdf_schema_adapters import STEADY_ROOT
from backend.app.core.project_file import deserialize_project_document, fingerprint_source, serialize_project_document
from backend.app.core.project_restore import build_project_document, restore_candidate_session
from backend.app.core.rasplot_db import PanelDef, auto_panels, build_db_from_plan, recalc_cumdist
from backend.app.core.state_contract import panel_dependency_digest, review_panels, seal_stationing
from backend.app.routers.report import render_profile_pdf_from_db
from backend.app.routers import hecras_api as api


PROFILE_COUNT = 5
DRY_EVERY = 997
GEOMETRY_DTYPE = [("River", "S32"), ("Reach", "S32"), ("RS", "S32"),
                  ("Len Channel", "f8"), ("Left Bank", "f8"), ("Right Bank", "f8")]
RESULT_DTYPE = [("River", "S32"), ("Reach", "S32"), ("Station", "S32")]


def synthetic_plan(count: int) -> Plan:
    identities = [("Benchmark River", "Benchmark Reach", str(count - i)) for i in range(count)]
    sections = [CrossSection(
        river=identity[0], reach=identity[1], rs=identity[2], name="",
        length_channel=25.0 if i + 1 < count else 0.0,
        left_bank=20.0, right_bank=80.0,
        station_elevation=[(0.0, 100.0 + i * 0.001), (100.0, 101.0 + i * 0.001)],
        minchel=100.0 + i * 0.001, index=i,
        ground_provenance={"state": "VALID", "method": "BETWEEN_BANKS"},
    ) for i, identity in enumerate(identities)]
    profiles = []
    for profile_index in range(PROFILE_COUNT):
        values = [None if i and i % DRY_EVERY == 0 else 102.0 + profile_index + i * 0.001
                  for i in range(count)]
        details = [{"value": value, "state": "MISSING" if value is None else "VALID"}
                   for value in values]
        profiles.append(ProfileResult(
            name=f"Profile {profile_index + 1}", profile_id=f"p{profile_index + 1}",
            key=f"p{profile_index + 1}", wsel=values, xs_ids=identities, values=details,
            source_path="synthetic-large.p01.hdf", source_dataset="Water Surface",
        ))
    reach = ReachGeometry(
        "Benchmark River", "Benchmark Reach", [], sections, [],
        source_id="benchmark-reach", source_path="synthetic-large.p01.hdf",
        source_order_status="SOURCE_UPSTREAM_FIRST",
    )
    plan = Plan("Synthetic large model", Path("synthetic-large.p01"), None, None, None,
                Path("synthetic-large.p01.hdf"), profiles=profiles, reaches=[reach],
                source_units="US Customary", source_datum="NAVD88",
                structure_inventory={"state": "VERIFIED_NONE"}, model_id=f"large-{count}")
    plan.working_units = "INTERNAL_US_CUSTOMARY"
    plan.unit_contract = {}
    plan.unit_conversion_status = "LEGACY_US_VALIDATED"
    return plan


def write_hdf_fixture(path: Path, count: int) -> None:
    stations = [str(count - index).encode() for index in range(count)]
    attributes = np.array([
        (b"Benchmark River", b"Benchmark Reach", station,
         25.0 if index + 1 < count else 0.0, 20.0, 80.0)
        for index, station in enumerate(stations)], dtype=GEOMETRY_DTYPE)
    info = np.column_stack((np.arange(count, dtype=np.int64) * 3,
                            np.full(count, 3, dtype=np.int64)))
    elevations = np.empty((count * 3, 2), dtype=np.float64)
    indexes = np.arange(count, dtype=np.float64)
    elevations[0::3, 0], elevations[1::3, 0], elevations[2::3, 0] = 0.0, 50.0, 100.0
    elevations[0::3, 1] = 101.0 + indexes * 0.001
    elevations[1::3, 1] = 100.0 + indexes * 0.001
    elevations[2::3, 1] = 101.0 + indexes * 0.001
    identities = np.array([(b"Benchmark River", b"Benchmark Reach", station)
                           for station in stations], dtype=RESULT_DTYPE)
    wsel = np.empty((PROFILE_COUNT, count), dtype=np.float64)
    for profile_index in range(PROFILE_COUNT):
        wsel[profile_index] = 102.0 + profile_index + indexes * 0.001
        wsel[profile_index, DRY_EVERY::DRY_EVERY] = np.nan
    with h5py.File(path, "w") as handle:
        handle.attrs["Units System"] = "US Customary"
        geometry = handle.create_group("Geometry/Cross Sections")
        geometry["Attributes"] = attributes
        geometry["Station Elevation Info"] = info
        geometry["Station Elevation Values"] = elevations
        output = handle.create_group(STEADY_ROOT)
        output["Profile Names"] = np.array([f"Profile {index + 1}".encode()
                                             for index in range(PROFILE_COUNT)])
        cross_sections = output.create_group("Cross Sections")
        cross_sections["Water Surface"] = wsel
        cross_sections["Cross Section Attributes"] = identities


def measured(function, *args, **kwargs):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    value = function(*args, **kwargs)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, round(elapsed, 4), round(peak / 1024 / 1024, 2)


def reopen_project(source_path: Path, document) -> None:
    session_id = f"large-benchmark-{source_path.stem}"
    api._SESSIONS[session_id] = {
        "plan": HdfReader(source_path).read_plan(), "path": str(source_path),
        "source_fingerprint": fingerprint_source(source_path), "reach_idx": None,
        "reach_databases": {},
    }
    try:
        restore_candidate_session(session_id, document)
    finally:
        api._SESSIONS.pop(session_id, None)


def run_size(count: int, output: Path, include_exports: bool) -> dict:
    source_path = output / f"large-{count}.p01.hdf"
    write_hdf_fixture(source_path, count)
    plan, import_seconds, import_peak = measured(HdfReader(source_path).read_plan)
    db, build_seconds, build_peak = measured(build_db_from_plan, plan)
    db.profiles = [profile for profile in db.profiles]
    for profile in db.profiles:
        profile.selected = True
    _value, edit_seconds, edit_peak = measured(setattr, db.working[count // 2], "length_chnl", 26.0)
    _value, recalc_seconds, recalc_peak = measured(recalc_cumdist, db.working)
    seal_stationing(db)
    panels, panel_seconds, panel_peak = measured(
        auto_panels, db.working, 2500.0, 10.0, station_range=(0.0, db.working[0].cumdist))
    db.panels = panels
    review_panels(db)
    digest, digest_seconds, digest_peak = measured(panel_dependency_digest, db)
    payload, serialize_seconds, serialize_peak = measured(serialize_project_document,
        build_project_document({"db": db, "plan": plan, "path": str(source_path),
                                "reach_idx": 0}, output / "benchmark.rasplot.json"))
    document, parse_seconds, parse_peak = measured(deserialize_project_document, payload)
    _value, reopen_seconds, reopen_peak = measured(reopen_project, source_path, document)
    result = {
        "xs": count, "profiles": PROFILE_COUNT, "panels": len(panels),
        "working_rows": len(db.working), "project_bytes": len(payload),
        "dependency_digest": digest,
        "operations": {
            "hdf_import": [import_seconds, import_peak],
            "working_table": [build_seconds, build_peak],
            "single_edit": [edit_seconds, edit_peak],
            "cumdist": [recalc_seconds, recalc_peak],
            "auto_panels": [panel_seconds, panel_peak],
            "dependency_digest": [digest_seconds, digest_peak],
            "project_serialize": [serialize_seconds, serialize_peak],
            "project_reopen_parse": [parse_seconds, parse_peak],
            "project_reopen_restore": [reopen_seconds, reopen_peak],
        },
    }
    if include_exports:
        span = max(row.cumdist for row in db.working if row.kind == "xs")
        elevations = [value for row in db.working if row.kind == "xs"
                      for value in [row.minchel, *row.profiles.values()] if value is not None]
        panel = PanelDef(1, 0.0, span, min(elevations) - 2.0, max(elevations) + 2.0,
                         h_scale=max(1.0, span / 13.0),
                         v_scale=max(1.0, (max(elevations) - min(elevations) + 4.0) / 9.0))
        db.panels = [panel]
        review_panels(db)
        pdf_path, dxf_path = output / f"large-{count}.pdf", output / f"large-{count}.dxf"
        _value, pdf_seconds, pdf_peak = measured(render_profile_pdf_from_db, str(pdf_path), db)
        _value, dxf_seconds, dxf_peak = measured(export_dxf, dxf_path, db)
        result["operations"].update(pdf_export=[pdf_seconds, pdf_peak], dxf_export=[dxf_seconds, dxf_peak])
        result.update(pdf_bytes=pdf_path.stat().st_size, dxf_bytes=dxf_path.stat().st_size)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000, 10000, 25000])
    parser.add_argument("--exports", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rasplot-large-benchmark-") as temporary:
        output = args.output or Path(temporary)
        output.mkdir(parents=True, exist_ok=True)
        results = [run_size(size, output, args.exports) for size in args.sizes]
        print(json.dumps({"configuration": {"profiles": PROFILE_COUNT, "dry_every": DRY_EVERY,
              "structures": 0, "route": "one connected reach", "timing": "seconds, peak MiB"},
              "results": results}, indent=2))


if __name__ == "__main__":
    main()
