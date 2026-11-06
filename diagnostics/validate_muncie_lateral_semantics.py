"""Read-only extraction and chainage mapping for Muncie's lateral structures."""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.hdf_reader import HdfReader

HDF = ROOT.parent / "Muncie" / "Muncie.p04.hdf"
GEOM = ROOT.parent / "Muncie" / "Muncie.g04"


def clean(value):
    if isinstance(value, bytes):
        return value.decode(errors="replace").rstrip("\x00").strip()
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def polyline_length(points):
    return sum(math.hypot(x1-x0, y1-y0) for (x0,y0),(x1,y1) in zip(points, points[1:]))


def interpolate_chainage(mapping, lateral_station, canonical):
    """Map a lateral station to main-channel C using adjacent explicit HW ties."""
    ordered = sorted(mapping, key=lambda item: item["station"])
    for first, second in zip(ordered, ordered[1:]):
        if first["station"] <= lateral_station <= second["station"]:
            t = (lateral_station-first["station"]) / (second["station"]-first["station"])
            c0, c1 = canonical[first["rs"]], canonical[second["rs"]]
            return {
                "lateral_station": lateral_station,
                "upstream_tie": first,
                "downstream_tie": second,
                "fraction_from_upstream_tie": t,
                "main_channel_cumdist": c0 + t*(c1-c0),
                "bracket_cumdist": [c0, c1],
            }
    raise ValueError(f"Station {lateral_station} not bracketed")


plan = HdfReader(HDF).read_plan()
reach = plan.reaches[0]
xs = reach.cross_sections
canonical = {xs[-1].rs: 0.0}
running = 0.0
for row in reversed(xs[:-1]):
    running += row.length_channel
    canonical[row.rs] = running

lines = GEOM.read_text(errors="replace").splitlines()
blocks = {}
for index, line in enumerate(lines):
    match = re.match(r"Type RM Length L Ch R\s*=\s*6\s*,\s*([^, ]+)", line)
    if not match:
        continue
    rs = match.group(1)
    end = next((j for j in range(index+1, len(lines)) if lines[j].startswith("Type RM Length L Ch R")), len(lines))
    blocks[rs] = {"start_line": index+1, "end_line": end, "lines": lines[index:end]}

with h5py.File(HDF, "r") as hdf:
    group = hdf["/Geometry/Structures"]
    attributes = group["Attributes"][()]
    table_info = group["Table Info"][()]
    profile_data = group["Profile Data"][()]
    center_info = group["Centerline Info"][()]
    center_points = group["Centerline Points"][()]
    connections = group["User Defined Weir Connectivity"][()]
    structures = []
    for sid, record in enumerate(attributes):
        attrs = {name: clean(record[name]) for name in record.dtype.names}
        if attrs["Type"] != "Lateral":
            continue
        ti = {name: clean(table_info[sid][name]) for name in table_info.dtype.names}
        profile_index = ti["Centerline Profile (Index)"]
        profile_count = ti["Centerline Profile (Count)"]
        profile = [[float(x), float(y)] for x,y in profile_data[profile_index:profile_index+profile_count]]
        point_index, point_count = int(center_info[sid][0]), int(center_info[sid][1])
        points = [[float(x),float(y)] for x,y in center_points[point_index:point_index+point_count]]
        conn = [
            {"side": clean(row["HW/TW"]), "rs": clean(row["RS/FP"]), "station": float(row["Station"])}
            for row in connections if int(row["SID"]) == sid
        ]
        hw = [row for row in conn if row["side"] == "HW"]
        tw = [row for row in conn if row["side"] == "TW"]
        block = blocks[attrs["RS"]]
        key_lines = [
            {"line": block["start_line"]+offset, "text": text}
            for offset,text in enumerate(block["lines"])
            if text.startswith(("Type RM", "Lateral Weir", "LW "))
        ]
        start = interpolate_chainage(hw, 0.0, canonical)
        end = interpolate_chainage(hw, profile[-1][0], canonical)
        structure = {
            "sid": sid,
            "attributes_all_fields": attrs,
            "attributes_populated_fields": {k:v for k,v in attrs.items() if v not in (None,"",0,0.0)},
            "table_info": ti,
            "profile_station_elevation": profile,
            "profile_station_extent": [profile[0][0], profile[-1][0]],
            "centerline_point_count": len(points),
            "centerline_points": points,
            "centerline_gis_length": polyline_length(points),
            "hw_connectivity": hw,
            "tw_connectivity": tw,
            "geometry_text_block": [block["start_line"], block["end_line"]],
            "geometry_text_key_lines": key_lines,
            "start_mapping": start,
            "end_mapping": end,
        }
        structures.append(structure)

summary_rows = []
for item in structures:
    attrs = item["attributes_all_fields"]
    start, end = item["start_mapping"], item["end_mapping"]
    summary_rows.append({
        "structure_rs": attrs["RS"],
        "hdf_us_rs": attrs["US RS"],
        "hdf_ds_rs": attrs["DS RS"],
        "lw_hw_distance": attrs["LW HW Distance"],
        "upstream_distance": attrs["Upstream Distance"],
        "weir_width": attrs["Weir Width"],
        "profile_length": item["profile_station_extent"][1],
        "gis_centerline_length": item["centerline_gis_length"],
        "start_bracket_up_rs": start["upstream_tie"]["rs"],
        "start_bracket_down_rs": start["downstream_tie"]["rs"],
        "start_bracket_up_c": start["bracket_cumdist"][0],
        "start_bracket_down_c": start["bracket_cumdist"][1],
        "start_fraction": start["fraction_from_upstream_tie"],
        "candidate_start_c": start["main_channel_cumdist"],
        "end_bracket_up_rs": end["upstream_tie"]["rs"],
        "end_bracket_down_rs": end["downstream_tie"]["rs"],
        "end_bracket_up_c": end["bracket_cumdist"][0],
        "end_bracket_down_c": end["bracket_cumdist"][1],
        "end_fraction": end["fraction_from_upstream_tie"],
        "candidate_end_c": end["main_channel_cumdist"],
    })

with (ROOT/"diagnostics/muncie_lateral_summary.csv").open("w",newline="") as output:
    writer=csv.DictWriter(output,fieldnames=list(summary_rows[0]))
    writer.writeheader(); writer.writerows(summary_rows)
(ROOT/"diagnostics/muncie_lateral_raw_evidence.json").write_text(json.dumps({
    "hdf": str(HDF), "geometry_text": str(GEOM),
    "canonical_xs_cumdist": canonical, "structures": structures,
}, indent=2))
print(json.dumps(summary_rows, indent=2))
