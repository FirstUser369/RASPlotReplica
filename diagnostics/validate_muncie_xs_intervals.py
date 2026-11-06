"""Validate ordinary-XS reach-length ownership in the Muncie geometry.

Read-only. Outputs CSV/JSON under diagnostics/. Run from repository root with:
  .venv/Scripts/python.exe diagnostics/validate_muncie_xs_intervals.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import h5py

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.hdf_reader import HdfReader
from backend.app.core.merge import compute_cumdist

HDF = ROOT.parent / "Muncie" / "Muncie.p04.hdf"
GEOM = ROOT.parent / "Muncie" / "Muncie.g04"
SEGMENT_RS = [
    "3268.276", "2920.440", "2582.948", "2290.221", "1980.776",
    "1743.103", "1469.294", "1174.213", "865.5413", "570.1187",
    "237.6455",
]


def decode(value):
    return value.decode().strip() if isinstance(value, bytes) else value


pattern = re.compile(
    r"^Type RM Length L Ch R\s*=\s*1\s*,\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+)"
)
text_rows = {}
for line_number, line in enumerate(GEOM.read_text(errors="replace").splitlines(), 1):
    match = pattern.match(line)
    if match:
        rs, left, channel, right = (part.strip() for part in match.groups())
        text_rows[rs] = {
            "line": line_number,
            "left": float(left),
            "channel": float(channel),
            "right": float(right),
            "record": line,
        }

with h5py.File(HDF, "r") as hdf:
    attrs = hdf["/Geometry/Cross Sections/Attributes"][()]
    hdf_rows = {
        decode(row["RS"]): {
            "left": float(row["Len Left"]),
            "channel": float(row["Len Channel"]),
            "right": float(row["Len Right"]),
        }
        for row in attrs
        if decode(row["River"]) == "White" and decode(row["Reach"]) == "Muncie"
    }

plan = HdfReader(HDF).read_plan()
reach = plan.reaches[0]
xs = reach.cross_sections
by_rs = {row.rs: row for row in xs}
initial = dict(zip((row.rs for row in xs), compute_cumdist(reach)))

# Canonical ordinary-XS reconstruction: the current/upstream XS owns the
# LOB/channel/ROB reach lengths to the next downstream XS.
canonical = {xs[-1].rs: 0.0}
running = 0.0
for row in reversed(xs[:-1]):
    running += row.length_channel
    canonical[row.rs] = running

segment = []
for up_rs, down_rs in zip(SEGMENT_RS, SEGMENT_RS[1:]):
    up_hdf = hdf_rows[up_rs]
    down_hdf = hdf_rows[down_rs]
    up_text = text_rows[up_rs]
    down_text = text_rows[down_rs]
    # Geometry text is decimal-rounded; compare at its displayed precision.
    assert round(up_hdf["channel"], 2) == up_text["channel"]
    assert round(down_hdf["channel"], 2) == down_text["channel"]
    segment.append({
        "upstream_rs": up_rs,
        "downstream_rs": down_rs,
        "upstream_hdf_lob": up_hdf["left"],
        "upstream_hdf_channel": up_hdf["channel"],
        "upstream_hdf_rob": up_hdf["right"],
        "downstream_hdf_lob": down_hdf["left"],
        "downstream_hdf_channel": down_hdf["channel"],
        "downstream_hdf_rob": down_hdf["right"],
        "upstream_text_lob_channel_rob": f"{up_text['left']:.2f}/{up_text['channel']:.2f}/{up_text['right']:.2f}",
        "upstream_text_line": up_text["line"],
        "downstream_text_lob_channel_rob": f"{down_text['left']:.2f}/{down_text['channel']:.2f}/{down_text['right']:.2f}",
        "downstream_text_line": down_text["line"],
        "rs_difference": float(up_rs) - float(down_rs),
        "initial_interval": initial[up_rs] - initial[down_rs],
        "recalc_xs_interval": canonical[up_rs] - canonical[down_rs],
        "initial_cumdist_upstream": initial[up_rs],
        "canonical_cumdist_upstream": canonical[up_rs],
        "interval_owner": "upstream XS Len Channel",
    })

csv_path = ROOT / "diagnostics" / "muncie_xs_interval_handcheck.csv"
with csv_path.open("w", newline="") as output:
    writer = csv.DictWriter(output, fieldnames=list(segment[0]))
    writer.writeheader()
    writer.writerows(segment)

all_pairs = []
for i in range(len(xs) - 1):
    upstream, downstream = xs[i], xs[i + 1]
    all_pairs.append({
        "upstream_rs": upstream.rs,
        "downstream_rs": downstream.rs,
        "upstream_len_channel": upstream.length_channel,
        "downstream_len_channel": downstream.length_channel,
        "initial_interval": initial[upstream.rs] - initial[downstream.rs],
        "canonical_interval": canonical[upstream.rs] - canonical[downstream.rs],
        "hdf_text_upstream_match_2dp": round(hdf_rows[upstream.rs]["channel"], 2)
        == text_rows[upstream.rs]["channel"],
    })

evidence = {
    "hdf": str(HDF),
    "geometry_text": str(GEOM),
    "cross_section_count": len(xs),
    "pair_count": len(all_pairs),
    "segment_rs_upstream_to_downstream": SEGMENT_RS,
    "segment_crosses_structure": False,
    "all_hdf_text_channel_lengths_match_at_text_precision": all(
        pair["hdf_text_upstream_match_2dp"] for pair in all_pairs
    ),
    "all_initial_intervals_equal_downstream_hdf_length": all(
        abs(pair["initial_interval"] - pair["downstream_len_channel"]) < 1e-8
        for pair in all_pairs
    ),
    "all_canonical_intervals_equal_upstream_hdf_length": all(
        abs(pair["canonical_interval"] - pair["upstream_len_channel"]) < 1e-8
        for pair in all_pairs
    ),
    "initial_full_extent": max(initial.values()) - min(initial.values()),
    "canonical_ordinary_xs_full_extent": max(canonical.values()) - min(canonical.values()),
    "sum_upstream_xs_lengths": sum(row.length_channel for row in xs[:-1]),
    "sum_downstream_xs_lengths": sum(row.length_channel for row in xs[1:]),
    "upstream_endpoint": {
        "rs": xs[0].rs,
        "length_channel": xs[0].length_channel,
        "initial_cumdist": initial[xs[0].rs],
        "canonical_cumdist": canonical[xs[0].rs],
    },
    "downstream_endpoint": {
        "rs": xs[-1].rs,
        "length_channel": xs[-1].length_channel,
        "initial_cumdist": initial[xs[-1].rs],
        "canonical_cumdist": canonical[xs[-1].rs],
    },
    "handcheck": segment,
}
(ROOT / "diagnostics" / "muncie_xs_interval_evidence.json").write_text(
    json.dumps(evidence, indent=2)
)
print(json.dumps({key: value for key, value in evidence.items() if key != "handcheck"}, indent=2))
