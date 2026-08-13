"""FIRM Database shapefile export/import (RASPLOT v3 §10–11).

Writes the L_* non-spatial tables as shapefile (point geometry at cumdist,0)
for GIS joinability. Uses pure-Python pyshp to avoid the GDAL/geopandas weight
in the portable install bundle.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import shapefile  # pyshp

from .rasplot_db import RasplotDatabase


def export_firm_shapefiles(out_dir: str | Path, db: RasplotDatabase, dfirm_id: str = "") -> list[str]:
    """Write L_* shapefiles into out_dir; return list of .shp paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    header = db.header

    # ---- L_XS ------------------------------------------------------------ #
    xs_path = out_dir / "L_XS"
    w = shapefile.Writer(str(xs_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("XS_LN_ID", "N", decimal=0)
    w.field("WTR_NM", "C", size=100)
    w.field("STREAM_STN", "N", decimal=4)
    w.field("XS_LTR", "C", size=8)
    w.field("STRMBED_EL", "N", decimal=3)
    w.field("WSEL_REG", "N", decimal=3)
    w.field("PROFXS_TXT", "C", size=100)
    w.field("LEN_UNIT", "C", size=20)
    w.field("V_DATUM", "C", size=20)
    primary = next((p.name for p in db.profiles if p.selected), None)
    if primary is None and db.profiles:
        primary = db.profiles[0].name
    for r in db.working:
        if r.kind != "xs":
            continue
        reg = r.profiles.get(primary) if primary else None
        w.point(r.cumdist, r.minchel or 0.0)
        w.record(
            dfirm_id, r.id, f"{r.river} {r.reach}".strip(),
            r.cumdist, r.label_letter or "", r.minchel or 0.0,
            reg if reg is not None else 0.0, r.label_text or "",
            header.unit, header.vertical_datum,
        )
    w.close()
    written.append(str(xs_path) + ".shp")

    # ---- L_XS_Struct ----------------------------------------------------- #
    st_path = out_dir / "L_XS_Struct"
    w = shapefile.Writer(str(st_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("XS_STR_ID", "N", decimal=0)
    w.field("STRUCT_TYP", "C", size=40)
    w.field("WTR_NM", "C", size=100)
    w.field("STR_STN", "N", decimal=4)
    w.field("LO_CHRD_EL", "N", decimal=3)
    w.field("HI_CHRD_EL", "N", decimal=3)
    w.field("STRMBED_EL", "N", decimal=3)
    w.field("LEN_UNIT", "C", size=20)
    w.field("V_DATUM", "C", size=20)
    for r in db.working:
        if r.kind != "struct":
            continue
        w.point(r.cumdist, r.minchel or 0.0)
        w.record(
            dfirm_id, r.id, r.struc, f"{r.river} {r.reach}".strip(),
            r.cumdist, r.low_chord or 0.0, r.high_chord or 0.0,
            r.minchel or 0.0, header.unit, header.vertical_datum,
        )
    w.close()
    written.append(str(st_path) + ".shp")

    # ---- L_Profil_Panel -------------------------------------------------- #
    pn_path = out_dir / "L_Profil_Panel"
    w = shapefile.Writer(str(pn_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("WTR_NM", "C", size=100)
    w.field("PANEL_NO", "N", decimal=0)
    w.field("START_STN", "N", decimal=4)
    w.field("END_STN", "N", decimal=4)
    w.field("START_ELEV", "N", decimal=3)
    w.field("END_ELEV", "N", decimal=3)
    w.field("ORIGIN_X", "N", decimal=4)
    w.field("ORIGIN_Y", "N", decimal=4)
    w.field("H_SCALE", "N", decimal=2)
    w.field("V_SCALE", "N", decimal=2)
    for p in db.panels:
        w.point(p.start_station, p.start_elevation)
        w.record(
            dfirm_id, header.stream_name, p.panel,
            p.start_station, p.end_station, p.start_elevation, p.end_elevation,
            p.panel_x, p.panel_y, p.h_scale, p.v_scale,
        )
    w.close()
    written.append(str(pn_path) + ".shp")

    # ---- L_Profil_Label (landmarks) -------------------------------------- #
    lm_path = out_dir / "L_Profil_Label"
    w = shapefile.Writer(str(lm_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("WTR_NM", "C", size=100)
    w.field("STREAM_STN", "N", decimal=4)
    w.field("ELEV", "N", decimal=3)
    w.field("DESCR", "C", size=50)
    w.field("ORIENT", "C", size=12)
    w.field("UNDERLINE", "C", size=1)
    for lm in db.landmarks:
        w.point(lm.distance, lm.elevation)
        w.record(
            dfirm_id, header.stream_name, lm.distance, lm.elevation,
            lm.text[:50], lm.orientation, "T" if lm.underline else "F",
        )
    w.close()
    written.append(str(lm_path) + ".shp")

    # ---- L_Profil_Bkwtr_El ----------------------------------------------- #
    bw_path = out_dir / "L_Profil_Bkwtr_El"
    w = shapefile.Writer(str(bw_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("WTR_NM", "C", size=100)
    w.field("EVENT_TYP", "C", size=40)
    w.field("BKWTR_WSEL", "N", decimal=3)
    w.field("LEN_UNIT", "C", size=20)
    w.field("V_DATUM", "C", size=20)
    i = 0
    for p in db.profiles:
        if p.backwater_elev is None:
            continue
        w.point(float(i), p.backwater_elev)
        w.record(
            dfirm_id, header.stream_name, p.frequency or p.name,
            p.backwater_elev, header.unit, header.vertical_datum,
        )
        i += 1
    if i == 0:
        # empty shapefile still needs one dummy? pyshp allows 0 records.
        pass
    w.close()
    written.append(str(bw_path) + ".shp")

    # ---- L_XS_Elev (multi-profile WSEL) ---------------------------------- #
    el_path = out_dir / "L_XS_Elev"
    w = shapefile.Writer(str(el_path), shapeType=shapefile.POINT)
    w.field("DFIRM_ID", "C", size=20)
    w.field("XS_LN_ID", "N", decimal=0)
    w.field("EVENT_TYP", "C", size=40)
    w.field("WSEL", "N", decimal=3)
    w.field("FW_WIDTH", "N", decimal=3)
    w.field("XS_AREA", "N", decimal=3)
    w.field("VELOCITY", "N", decimal=3)
    w.field("WSEL_WOFWY", "N", decimal=3)
    w.field("WSEL_FLDWY", "N", decimal=3)
    w.field("WSEL_INCRS", "N", decimal=3)
    fw_by_rs = {f.rs: f for f in db.floodway}
    for r in db.working:
        if r.kind != "xs":
            continue
        fw = fw_by_rs.get(r.rs)
        for pname, wsel in r.profiles.items():
            if wsel is None:
                continue
            meta = next((p for p in db.profiles if p.name == pname), None)
            event = (meta.frequency if meta and meta.frequency else pname)
            w.point(r.cumdist, wsel)
            w.record(
                dfirm_id, r.id, event, wsel,
                fw.width if fw and fw.width is not None else 0.0,
                fw.area if fw and fw.area is not None else 0.0,
                fw.velocity if fw and fw.velocity is not None else 0.0,
                fw.elev_without_fw if fw and fw.elev_without_fw is not None else 0.0,
                fw.elev_with_fw if fw and fw.elev_with_fw is not None else 0.0,
                fw.elev_diff if fw and fw.elev_diff is not None else 0.0,
            )
    w.close()
    written.append(str(el_path) + ".shp")

    # Sidecar JSON for non-spatial header
    (out_dir / "workheader.json").write_text(
        json.dumps({
            "stream_name": header.stream_name,
            "community": header.community,
            "county": header.county,
            "state": header.state,
            "unit": header.unit,
            "vertical_datum": header.vertical_datum,
            "stream_distance_label": header.stream_distance_label,
        }, indent=2),
        encoding="utf-8",
    )
    return written


def zip_firm_export(out_dir: str | Path, zip_path: str | Path) -> str:
    """Zip a firm-export directory into zip_path."""
    out_dir = Path(out_dir)
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir).as_posix())
    return str(zip_path)
