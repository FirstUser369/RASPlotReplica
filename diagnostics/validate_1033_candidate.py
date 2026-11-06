"""Independent, read-only validation of the candidate CUMDIST implementation."""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from copy import deepcopy
from pathlib import Path

import h5py
import numpy as np
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.hdf_reader import HdfReader
from backend.app.core.merge import compute_cumdist
from backend.app.core.rasplot_db import build_db_from_plan, recalc_cumdist

HDF = ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"
GEOM = HDF.with_suffix("").with_suffix(".g04")


def clean(value):
    if isinstance(value, bytes):
        return value.decode(errors="replace").rstrip("\x00").strip()
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


text_xs = {}
for line_no, line in enumerate(GEOM.read_text(errors="replace").splitlines(), 1):
    match = re.match(
        r"Type RM Length L Ch R\s*=\s*1\s*,\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+)",
        line,
    )
    if match:
        rs, left, channel, right = (part.strip() for part in match.groups())
        text_xs[rs] = {
            "line": line_no, "left": float(left), "channel": float(channel),
            "right": float(right), "record": line,
        }

with h5py.File(HDF, "r") as hdf:
    xs_group = hdf["/Geometry/Cross Sections"]
    raw_xs = xs_group["Attributes"][()]
    xs_info = xs_group["Polyline Info"][()]
    xs_points = xs_group["Polyline Points"][()]
    center = hdf["/Geometry/River Centerlines"]
    center_info = center["Polyline Info"][0]
    centerline = LineString(center["Polyline Points"][
        int(center_info[0]):int(center_info[0]+center_info[1])
    ])
    projections = {}
    for index, row in enumerate(raw_xs):
        start, count = int(xs_info[index][0]), int(xs_info[index][1])
        cutline = LineString(xs_points[start:start+count])
        intersection = cutline.intersection(centerline)
        projections[clean(row["RS"])] = float(centerline.project(intersection))
    structure_attrs = [
        {name: clean(row[name]) for name in row.dtype.names}
        for row in hdf["/Geometry/Structures/Attributes"][()]
    ]
    culvert_group = hdf["/Geometry/Structures/Culvert Groups/Attributes"][()]
    culvert_attrs = [
        {name: clean(row[name]) for name in row.dtype.names}
        for row in culvert_group
    ]
    hdf_groups = []
    hdf.visititems(lambda name, obj: hdf_groups.append({
        "path": "/"+name,
        "kind": "dataset" if isinstance(obj, h5py.Dataset) else "group",
        "shape": list(obj.shape) if isinstance(obj, h5py.Dataset) else None,
        "dtype": str(obj.dtype) if isinstance(obj, h5py.Dataset) else None,
    }))

plan = HdfReader(HDF).read_plan()
reach = plan.reaches[0]
xs = reach.cross_sections
canonical_values = compute_cumdist(reach)
canonical = dict(zip((row.rs for row in xs), canonical_values))
lengths = [row.length_channel for row in xs]

pairs = []
structure_interval = {("0.943", "0.928")}
for upstream, downstream in zip(xs, xs[1:]):
    gis_interval = projections[downstream.rs] - projections[upstream.rs]
    up_error = abs(gis_interval-upstream.length_channel)
    down_error = abs(gis_interval-downstream.length_channel)
    affected = (upstream.rs, downstream.rs) in structure_interval
    pairs.append({
        "upstream_rs": upstream.rs,
        "downstream_rs": downstream.rs,
        "upstream_len_channel": upstream.length_channel,
        "downstream_len_channel": downstream.length_channel,
        "geometry_text_upstream_channel": text_xs[upstream.rs]["channel"],
        "geometry_text_line": text_xs[upstream.rs]["line"],
        "gis_centerline_interval": gis_interval,
        "upstream_ownership_error": up_error,
        "downstream_ownership_error": down_error,
        "supports_upstream_ownership": up_error < down_error,
        "structure_affected": affected,
    })

db = build_db_from_plan(plan)
fresh_rows = deepcopy(db.working)
recalc_rows = deepcopy(db.working)
recalc_cumdist(recalc_rows)
fresh_xs = [row for row in fresh_rows if row.kind == "xs"]
recalc_xs = [row for row in recalc_rows if row.kind == "xs"]
fresh_struct = [row for row in fresh_rows if row.kind == "struct"]
recalc_struct = [row for row in recalc_rows if row.kind == "struct"]

structure = reach.structures[0]
us_index = next(i for i,row in enumerate(xs) if row.rs == structure.us_rs)
ds_index = next(i for i,row in enumerate(xs) if row.rs == structure.ds_rs)
us_xs, ds_xs = xs[us_index], xs[ds_index]
residual = us_xs.length_channel - structure.culvert_us_distance - structure.culvert_length
candidate_dn = canonical[ds_xs.rs] + residual
candidate_up = candidate_dn + structure.culvert_length

structure_diagnostic = {
    "river": structure.river, "reach": structure.reach, "rs": structure.rs,
    "type": structure.type, "us_xs": structure.us_rs, "ds_xs": structure.ds_rs,
    "us_xs_len_channel": us_xs.length_channel,
    "ds_xs_len_channel": ds_xs.length_channel,
    "weir_width": structure.weir_width,
    "culvert_length": structure.culvert_length,
    "culvert_us_distance": structure.culvert_us_distance,
    "residual_ds_distance": residual,
    "partition_sum": structure.culvert_us_distance + structure.culvert_length + residual,
    "invert_us": structure.invert_us, "invert_ds": structure.invert_ds,
    "raw_structure_attributes": structure_attrs[0],
    "raw_culvert_group_attributes": culvert_attrs[0],
    "canonical_us_xs_cumdist": canonical[us_xs.rs],
    "canonical_ds_xs_cumdist": canonical[ds_xs.rs],
    "raw_supported_dn_face_cumdist": candidate_dn,
    "raw_supported_up_face_cumdist": candidate_up,
    "fresh_working_rows": [row.__dict__ for row in fresh_struct],
    "recalculated_working_rows": [row.__dict__ for row in recalc_struct],
    "fresh_main_extent": max(row.cumdist for row in fresh_xs),
    "recalculated_main_extent": max(row.cumdist for row in recalc_xs),
    "extent_change_from_recalc": max(row.cumdist for row in recalc_xs)-max(row.cumdist for row in fresh_xs),
}

structure_free = [pair for pair in pairs if not pair["structure_affected"]]
evidence = {
    "file": str(HDF),
    "file_type": "HEC-RAS Geometry 5.0.7",
    "hdf_inventory": hdf_groups,
    "rivers_reaches": [{
        "river": item.river, "reach": item.reach,
        "cross_sections": len(item.cross_sections), "structures": len(item.structures),
    } for item in plan.reaches],
    "profiles": [profile.name for profile in plan.profiles],
    "structure_types": [item.type for item in reach.structures],
    "intervals_total": len(pairs),
    "structure_free_intervals": len(structure_free),
    "structure_free_supporting_upstream": sum(pair["supports_upstream_ownership"] for pair in structure_free),
    "structure_free_disagreements": [pair for pair in structure_free if not pair["supports_upstream_ownership"]],
    "max_structure_free_upstream_error": max(pair["upstream_ownership_error"] for pair in structure_free),
    "structure_interval": next(pair for pair in pairs if pair["structure_affected"]),
    "previous_fresh_extent": sum(lengths[1:]),
    "candidate_fresh_extent": max(row.cumdist for row in fresh_xs),
    "candidate_recalc_extent": max(row.cumdist for row in recalc_xs),
    "independent_ordinary_xs_extent": sum(lengths[:-1]),
    "river_centerline_between_endpoint_xs": projections[xs[-1].rs]-projections[xs[0].rs],
    "pairs": pairs,
    "structure": structure_diagnostic,
}

with (ROOT/"diagnostics/1033_xs_intervals.csv").open("w", newline="") as output:
    writer=csv.DictWriter(output,fieldnames=list(pairs[0])); writer.writeheader();writer.writerows(pairs)
(ROOT/"diagnostics/1033_candidate_evidence.json").write_text(json.dumps(evidence,indent=2,allow_nan=False))
print(json.dumps({key:value for key,value in evidence.items() if key not in ("hdf_inventory","pairs","structure")},indent=2))
print("STRUCTURE",json.dumps(structure_diagnostic,indent=2,default=str))
