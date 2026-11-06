from __future__ import annotations

from pathlib import Path
import sys
import ezdxf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.dxf_export import export_dxf
from backend.app.core.hdf_reader import HdfReader
from backend.app.core.rasplot_db import PanelDef, build_db_from_plan, recalc_cumdist

LEGACY_PATH = ROOT / "RAS Plot Database and Model Linework" / "Model Exported Linework.dxf"
HDF_PATH = ROOT / "RAS Plot Database and Model Linework" / "Model Data" / "1033_North LOMR As-Built.p04.hdf"
OUTPUT_PATH = ROOT / "diagnostics" / "phase1_legacy_profile.dxf"


def longest_polyline(doc, layer):
    candidates = [e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == layer]
    return max(candidates, key=lambda e: len(e.get_points()))


def points(entity):
    return [(float(p[0]), float(p[1])) for p in entity.get_points()]


def compare(expected, actual, tolerance=0.01):
    matched = []
    for x, y in expected:
        candidate = min(actual, key=lambda p: abs(p[0] - x), default=None)
        if candidate is not None and abs(candidate[0] - x) <= tolerance:
            matched.append((x, y, candidate[0], candidate[1]))
    x_error = max((abs(x - ax) for x, _y, ax, _ay in matched), default=None)
    y_error = max((abs(y - ay) for _x, y, _ax, ay in matched), default=None)
    return matched, x_error, y_error


def main():
    legacy = ezdxf.readfile(str(LEGACY_PATH))
    plan = HdfReader(str(HDF_PATH)).read_plan()
    db = build_db_from_plan(plan, reach_idx=0)
    recalc_cumdist(db.working, start=2775.0)
    db.panels = [PanelDef(
        panel=1,
        start_station=0.0,
        end_station=6500.0,
        start_elevation=1070.0,
        end_elevation=1115.0,
        h_scale=500.0,
        v_scale=5.0,
    )]
    export_dxf(
        OUTPUT_PATH,
        db,
        profile_names=["PF 1"],
        panels=db.panels,
        page_size="11x17",
        h_scale=500.0,
        v_scale=5.0,
        legacy_modelspace=True,
    )
    current = ezdxf.readfile(str(OUTPUT_PATH))
    legacy_ws = points(longest_polyline(legacy, "WS100"))
    legacy_stream = points(longest_polyline(legacy, "STREAM"))
    current_ws_entity = longest_polyline(current, "WS100")
    current_stream_entity = longest_polyline(current, "STREAM")
    current_ws = points(current_ws_entity)
    current_stream = points(current_stream_entity)
    ws_matches, ws_x_error, ws_y_error = compare(legacy_ws, current_ws)
    stream_matches, stream_x_error, stream_y_error = compare(legacy_stream, current_stream)

    sample_xs = [2775.0, 5276.82, 5409.291, 5451.84, 6004.061]
    print("OUTPUT", OUTPUT_PATH)
    print("WS100_LAYER", current_ws_entity.dxf.layer)
    print("WS100_VERTICES", len(current_ws))
    print("STREAM_LAYER", current_stream_entity.dxf.layer)
    print("STREAM_VERTICES", len(current_stream))
    print("WS100_SAMPLES")
    for target in sample_xs:
        point = min(current_ws, key=lambda p: abs(p[0] - target))
        print(target, point)
    print("STREAM_SAMPLES")
    for target in [2775.0, 5276.82, 5409.291, 5451.84, 6004.061]:
        point = min(current_stream, key=lambda p: abs(p[0] - target))
        print(target, point)
    print("WS100_MATCHED", len(ws_matches), "OF", len(legacy_ws), "X_ERROR", ws_x_error, "Y_ERROR", ws_y_error)
    print("STREAM_MATCHED", len(stream_matches), "OF", len(legacy_stream), "X_ERROR", stream_x_error, "Y_ERROR", stream_y_error)
    print("REGRESSION_HDF_XS", len(plan.reaches[0].cross_sections))
    print("REGRESSION_HDF_STRUCTURES", len(plan.reaches[0].structures))
    print("REGRESSION_WORKING_ROWS", len(db.working))


if __name__ == "__main__":
    main()
