from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

from backend.app.core.dxf_export import export_dxf
from backend.app.core.hdf_reader import HdfReader
from backend.app.core.rasplot_db import build_db_from_plan, recalc_cumdist

LEGACY = ROOT / "RAS Plot Database and Model Linework" / "Model Exported Linework.dxf"
HDF = ROOT / "RAS Plot Database and Model Linework" / "Model Data" / "1033_North LOMR As-Built.p04.hdf"
OUT = ROOT / "diagnostics" / "current_export_benchmark.dxf"
REPORT = ROOT / "diagnostics" / "dxf_benchmark_report.json"


def coords(entity):
    kind = entity.dxftype()
    if kind == "LINE":
        return [tuple(entity.dxf.start), tuple(entity.dxf.end)]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        if kind == "LWPOLYLINE":
            return [(float(x), float(y), float(z or 0.0)) for x, y, *rest in entity.get_points("xyb") for z in [0.0]]
        return [tuple(v.dxf.location) for v in entity.vertices]
    if kind in {"TEXT", "MTEXT", "POINT", "INSERT"}:
        attr = "insert" if kind in {"TEXT", "MTEXT", "INSERT"} else "point"
        value = getattr(entity.dxf, attr, None)
        return [tuple(value)] if value is not None else []
    return []


def entity_summary(doc):
    msp = doc.modelspace()
    type_counts = Counter(e.dxftype() for e in msp)
    layers = defaultdict(lambda: Counter())
    layer_entities = defaultdict(list)
    texts = []
    for e in msp:
        layer = e.dxf.layer
        layers[layer][e.dxftype()] += 1
        layer_entities[layer].append(e)
        if e.dxftype() in {"TEXT", "MTEXT"}:
            text = e.dxf.text if e.dxftype() == "TEXT" else e.text
            insert = getattr(e.dxf, "insert", None)
            texts.append({
                "layer": layer,
                "type": e.dxftype(),
                "text": text,
                "insert": tuple(insert) if insert is not None else None,
                "height": getattr(e.dxf, "height", None),
                "rotation": getattr(e.dxf, "rotation", 0.0),
            })
    polylines = []
    for layer, entities in layer_entities.items():
        for e in entities:
            if e.dxftype() not in {"LWPOLYLINE", "POLYLINE"}:
                continue
            points = coords(e)
            if not points:
                continue
            xy = [(p[0], p[1]) for p in points]
            polylines.append({
                "layer": layer,
                "type": e.dxftype(),
                "vertices": len(points),
                "min_x": min(p[0] for p in xy),
                "max_x": max(p[0] for p in xy),
                "min_y": min(p[1] for p in xy),
                "max_y": max(p[1] for p in xy),
                "first": points[:5],
                "last": points[-5:],
                "closed": bool(getattr(e.dxf, "flags", 0) & 1) if e.dxftype() == "POLYLINE" else bool(getattr(e.dxf, "closed", False)),
            })
    return {
        "version": doc.dxfversion,
        "modelspace_counts": dict(type_counts),
        "layers": {k: dict(v) for k, v in sorted(layers.items())},
        "polylines": polylines,
        "texts": texts,
        "paperspace_entities": sum(1 for _ in doc.paperspace()),
    }


def main():
    legacy = entity_summary(ezdxf.readfile(str(LEGACY)))
    plan = HdfReader(str(HDF)).read_plan()
    db = build_db_from_plan(plan, reach_idx=0)
    recalc_cumdist(db.working, start=2775.0)
    db.header.h_scale = 500.0
    db.header.v_scale = 5.0
    db.header.page_size = "11x17"
    db.header.stream_distance_label = ""
    export_dxf(OUT, db, include_grid=True, include_title_block=True)
    current = entity_summary(ezdxf.readfile(str(OUT)))

    report = {
        "legacy": legacy,
        "current": current,
        "benchmark_working": [
            {"rs": r.rs, "cumdist": r.cumdist, "struc": r.struc, "minchel": r.minchel, "low": r.low_chord, "high": r.high_chord, "pf1": r.profiles.get("PF 1")}
            for r in db.working
            if r.rs in {"0.447", "0.928", "0.943", "0.948", "1.047"} or r.kind == "struct"
        ],
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"legacy": {k: legacy[k] for k in ("version", "modelspace_counts", "paperspace_entities")}, "current": {k: current[k] for k in ("version", "modelspace_counts", "paperspace_entities")}, "output": str(OUT), "report": str(REPORT)}, indent=2))


if __name__ == "__main__":
    main()
