"""Exercise current profile/panel/revision/PDF/DXF paths for the 1033 model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ezdxf
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.routers.hecras_api import (
    CumdistRequest, OpenRequest, get_db, get_panels, get_profile,
    get_revision_limits, open_source, post_recalc_cumdist,
)
from backend.app.routers.report import ExportOptions, export_profile

HDF = ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"


def snapshot(sid):
    profile = get_profile(sid, profile=None, profiles="PF 1", reach_idx=0, label_every=5)
    db = get_db(sid)
    xs = [row for row in db["working"] if row["kind"] == "xs"]
    structures = [row for row in db["working"] if row["kind"] == "struct"]
    pdf = export_profile(ExportOptions(session_id=sid, format="pdf", profile_names=["PF 1"]))
    dxf = export_profile(ExportOptions(session_id=sid, format="dxf", profile_names=["PF 1"]))
    doc = ezdxf.readfile(dxf["path"])
    entity_summary = []
    for entity in doc.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        points = list(entity.get_points("xy"))
        if not points:
            continue
        layer = entity.dxf.layer
        if layer.startswith("STRUCT_") or layer in {"INVERT", "PROF_PF 1", "PROF_PF_1"}:
            entity_summary.append({
                "layer": layer,
                "x_min": min(point[0] for point in points),
                "x_max": max(point[0] for point in points),
                "vertex_count": len(points),
            })
    by_rs = {row["rs"]: row for row in xs}
    return {
        "xs_count": len(xs), "structure_count": len(structures),
        "xs_order": [row["rs"] for row in xs],
        "xs_extent": [min(row["cumdist"] for row in xs), max(row["cumdist"] for row in xs)],
        "xs_0943": by_rs["0.943"], "xs_0928": by_rs["0.928"],
        "structures": structures,
        "profile_xs_extent": [
            min(row["cumdist"] for row in profile["cross_sections"]),
            max(row["cumdist"] for row in profile["cross_sections"]),
        ],
        "profile_structures": profile["structures"],
        "panels": get_panels(sid)["panels"],
        "revision": get_revision_limits(sid),
        "pdf": {**pdf, "pages": len(PdfReader(pdf["path"]).pages)},
        "dxf": dxf,
        "dxf_entities": entity_summary,
    }


opened = open_source(OpenRequest(path=str(HDF)))
sid = opened["session_id"]
fresh = snapshot(sid)
post_recalc_cumdist(sid, CumdistRequest(start=0.0))
recalculated = snapshot(sid)
result = {
    "session": sid,
    "flow_type": opened["flow_type"],
    "profiles": [profile["name"] for profile in opened["profiles"]],
    "fresh": fresh,
    "recalculated": recalculated,
    "wsel_unchanged": [row["wsel"] for row in get_profile(sid, profile=None, profiles="PF 1", reach_idx=0, label_every=5)["cross_sections"]]
        == [row["profiles"]["PF 1"] for row in get_db(sid)["working"] if row["kind"] == "xs"],
}
(ROOT/"diagnostics/1033_application_validation.json").write_text(json.dumps(result,indent=2,allow_nan=False))
print(json.dumps({
    "session": sid,
    "fresh_extent": fresh["xs_extent"],
    "recalculated_extent": recalculated["xs_extent"],
    "fresh_0943": fresh["xs_0943"]["cumdist"],
    "recalculated_0943": recalculated["xs_0943"]["cumdist"],
    "fresh_structures": [(row["struc"],row["cumdist"]) for row in fresh["structures"]],
    "recalculated_structures": [(row["struc"],row["cumdist"]) for row in recalculated["structures"]],
    "fresh_pdf": fresh["pdf"], "fresh_dxf": fresh["dxf"],
    "recalculated_pdf": recalculated["pdf"], "recalculated_dxf": recalculated["dxf"],
    "wsel_unchanged": result["wsel_unchanged"],
},indent=2))
