"""PDF + DXF report generators and HTTP endpoints.

Uses the session RasplotDatabase for working table, panels, styling and header.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.merge import compute_cumdist, MergedReach
from ..core.rasplot_db import (
    PAGE_SIZES, RasplotDatabase, WorkHeader, PanelDef, ProfileMeta,
    distance_unit_factor, auto_panels,
)
from ..core.dxf_export import export_dxf
from ..core.fdt import render_fdt_pdf, STANDARD_FOOTNOTES
from ..core.firm_export import export_firm_shapefiles, zip_firm_export
from .hecras_api import _SESSIONS, _session_db


router = APIRouter(prefix="/api/report")


# --------------------------------------------------------------------------- #
# PDF renderer                                                                 #
# --------------------------------------------------------------------------- #


def render_profile_pdf_from_db(
    out_path: str,
    db: RasplotDatabase,
    profile_names: list[str] | None = None,
    panels: list[PanelDef] | None = None,
    page_size: str | None = None,
    h_scale: float | None = None,
    v_scale: float | None = None,
    custom_page: tuple[float, float] | None = None,
) -> str:
    header = db.header
    page_size = page_size or header.page_size or "11x17"
    h_scale = float(h_scale if h_scale is not None else header.h_scale)
    v_scale = float(v_scale if v_scale is not None else header.v_scale)
    if custom_page:
        page_w, page_h = custom_page
    else:
        page_w, page_h = PAGE_SIZES.get(page_size, (17.0, 11.0))

    meta_by = {p.name: p for p in db.profiles}
    if profile_names:
        selected = [meta_by[n] for n in profile_names if n in meta_by]
    else:
        selected = [p for p in db.profiles if p.selected] or list(db.profiles[:1])

    panels = panels if panels is not None else db.panels
    if not panels:
        panels = auto_panels(db.working, h_scale=h_scale, v_scale=v_scale, page_size=page_size)

    xs = [r for r in db.working if r.kind == "xs"]
    structs = [r for r in db.working if r.kind == "struct"]
    cumdist = [r.cumdist for r in xs]
    inv = [r.minchel if r.minchel is not None else float("nan") for r in xs]
    unit_factor = distance_unit_factor(header.unit)

    pdf = PdfPages(out_path)
    try:
        for i, panel in enumerate(panels):
            fig, ax = plt.subplots(figsize=(page_w, page_h))
            # invert
            ax.plot(cumdist, inv, color="black", linewidth=0.9, label="STREAM BED")
            legend_handles = [Line2D([0], [0], color="black", lw=0.9, label="STREAM BED")]
            for pmeta in selected:
                y = [r.profiles.get(pmeta.name) for r in xs]
                ax.plot(cumdist, y, color=pmeta.color or "#196bd6",
                        linewidth=(pmeta.lineweight or 0.3) * 4,
                        linestyle=_mpl_ls(pmeta.linetype),
                        label=pmeta.legend_text or pmeta.name)
                legend_handles.append(Line2D(
                    [0], [0], color=pmeta.color or "#196bd6",
                    lw=(pmeta.lineweight or 0.3) * 4,
                    linestyle=_mpl_ls(pmeta.linetype),
                    label=pmeta.legend_text or pmeta.name,
                ))

            panel_w = max(1.0, panel.end_station - panel.start_station)
            for s in structs:
                if not (panel.start_station - 50 <= s.cumdist <= panel.end_station + 50):
                    continue
                target = s.cumdist
                y_bot = s.minchel if s.minchel is not None else panel.start_elevation
                t = (s.struc or "").upper()
                if "BRIDGE" in t and s.low_chord is not None and s.high_chord is not None:
                    ax.add_patch(Rectangle(
                        (target - panel_w * 0.003, s.low_chord),
                        panel_w * 0.006, s.high_chord - s.low_chord,
                        facecolor="0.65", edgecolor="black", linewidth=0.5, zorder=3,
                    ))
                    ax.plot([target, target], [y_bot, s.high_chord], color="black", lw=0.6)
                elif s.high_chord is not None or s.low_chord is not None:
                    crest = s.high_chord if s.high_chord is not None else s.low_chord
                    ax.plot([target - panel_w * 0.004, target + panel_w * 0.004],
                            [crest, crest], color="black", lw=1.2)
                    ax.plot([target, target], [y_bot, crest], color="black", lw=0.6)
                ax.text(target, (s.high_chord or s.low_chord or y_bot) + 0.4,
                        (s.struc or "STR")[:10], rotation=90, fontsize=6, va="bottom")

            # letter labels
            for r in xs:
                if not r.label_letter:
                    continue
                if not (panel.start_station <= r.cumdist <= panel.end_station):
                    continue
                y = None
                if selected:
                    y = r.profiles.get(selected[0].name)
                if y is None:
                    y = r.minchel
                if y is None:
                    continue
                ax.text(r.cumdist, y + 0.3, r.label_letter, ha="center", va="bottom",
                        fontsize=9, fontname="serif", fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))

            # landmarks
            for lm in db.landmarks:
                if panel.start_station <= lm.distance <= panel.end_station:
                    rot = 90 if (lm.orientation or "").lower().startswith("v") else 0
                    ax.text(lm.distance, lm.elevation, lm.text, rotation=rot,
                            fontsize=7, ha="left", va="bottom")

            # backwater
            for pmeta in selected:
                if pmeta.backwater_elev is None:
                    continue
                ax.axhline(pmeta.backwater_elev, color="red", ls="--", lw=0.8)
                if pmeta.backwater_text:
                    ax.text(panel.start_station, pmeta.backwater_elev,
                            "  " + pmeta.backwater_text, color="red", fontsize=7, va="bottom")
                break

            ax.set_xlim(panel.start_station, panel.end_station)
            ax.set_ylim(panel.start_elevation, panel.end_elevation)
            ax.set_xlabel(
                f"Distance ({header.unit}) above {header.stream_distance_label}",
                fontsize=8,
            )
            ax.set_ylabel(f"Elevation ({header.vertical_datum})", fontsize=8)
            # rescale x tick labels into display unit
            if unit_factor != 1.0:
                ticks = ax.get_xticks()
                ax.set_xticklabels([f"{t * unit_factor:.0f}" if unit_factor >= 1
                                   else f"{t * unit_factor:.2f}" for t in ticks], fontsize=7)
            ax.grid(True, which="both", ls=":", lw=0.4)
            ax.legend(handles=legend_handles, loc="upper right", fontsize=6, framealpha=0.9)

            # Title block-ish
            panel_no = panel.panel + header.start_panel_no - 1
            total = len(panels) + header.start_panel_no - 1
            title = (
                f"FLOOD PROFILE\n{header.stream_name.upper()}\n"
                f"{header.community.upper()}, {header.county.upper()}, {header.state.upper()}\n"
                f"PANEL {panel_no} OF {total}    "
                f"H 1\"={panel.h_scale:g} {header.unit}   V 1\"={panel.v_scale:g} FT"
            )
            ax.set_title(title, fontsize=8, loc="left")
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)
    finally:
        pdf.close()
    return out_path


def _mpl_ls(linetype: str | None) -> str:
    t = (linetype or "CONTINUOUS").upper()
    return {
        "CONTINUOUS": "-",
        "DASHED": "--",
        "DOTTED": ":",
        "DASHDOT": "-.",
        "CENTER": "-.",
        "HIDDEN": "--",
        "PHANTOM": "-.",
    }.get(t, "-")


# --------------------------------------------------------------------------- #
# Request models                                                               #
# --------------------------------------------------------------------------- #


class ExportOptions(BaseModel):
    session_id: str
    format: str = "pdf"                 # pdf | dxf
    profile_names: list[str] | None = None
    page_size: str | None = None
    custom_page_w: float | None = None
    custom_page_h: float | None = None
    h_scale: float | None = None
    v_scale: float | None = None
    distance_unit: str | None = None
    vertical_datum: str | None = None
    stream_name: str | None = None
    community: str | None = None
    county: str | None = None
    state: str | None = None
    start_station_description: str | None = None
    panel_no: int | None = None
    dxf_version: str = "R2013"
    include_grid: bool = True
    include_title_block: bool = True
    # When true, re-panel from current view scale / page prior to export
    use_current_view: bool = True
    # Fit entire reach on one panel (adjusts H scale up as needed)
    fit_to_one_panel: bool = False
    # Force one panel at the stated H scale (may overrun paper length)
    single_panel: bool = False


class FdtRequest(BaseModel):
    session_id: str
    footnotes: list[str] | None = None
    table_name: str | None = None
    table_title: str | None = None


class FirmExportRequest(BaseModel):
    session_id: str
    dfirm_id: str = ""


class PdfMergeRequest(BaseModel):
    paths: list[str]
    out_name: str = "merged.pdf"


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #


def _apply_export_options(db: RasplotDatabase, req: ExportOptions) -> None:
    h = db.header
    if req.stream_name is not None:
        h.stream_name = req.stream_name
    if req.community is not None:
        h.community = req.community
    if req.county is not None:
        h.county = req.county
    if req.state is not None:
        h.state = req.state
    if req.distance_unit is not None:
        h.unit = req.distance_unit
    if req.vertical_datum is not None:
        h.vertical_datum = req.vertical_datum
    if req.start_station_description is not None:
        h.stream_distance_label = req.start_station_description
    if req.panel_no is not None:
        h.start_panel_no = req.panel_no
    if req.page_size is not None:
        h.page_size = req.page_size
    if req.h_scale is not None:
        h.h_scale = req.h_scale
    if req.v_scale is not None:
        h.v_scale = req.v_scale
    if req.profile_names:
        sel = set(req.profile_names)
        for p in db.profiles:
            p.selected = p.name in sel

    custom = None
    if req.custom_page_w and req.custom_page_h:
        custom = (req.custom_page_w, req.custom_page_h)

    # Recompute panels whenever scale/page/fit options drive the export
    if req.use_current_view or req.fit_to_one_panel or req.single_panel:
        panels = auto_panels(
            db.working,
            h_scale=h.h_scale,
            v_scale=h.v_scale,
            page_size=h.page_size,
            custom_page=custom,
            single_panel=req.single_panel,
            fit_to_page=req.fit_to_one_panel,
        )
        db.panels = panels
        if panels:
            h.h_scale = panels[0].h_scale
            h.v_scale = panels[0].v_scale


def _cache_dir(sid: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "rasplotreplica", sid)
    os.makedirs(d, exist_ok=True)
    return d


@router.post("/export")
def export_profile(req: ExportOptions) -> dict:
    """Export current view to PDF or DXF with scale/style/page options."""
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = _session_db(req.session_id)
    _apply_export_options(db, req)

    custom = None
    if req.custom_page_w and req.custom_page_h:
        custom = (req.custom_page_w, req.custom_page_h)

    out_dir = _cache_dir(req.session_id)
    fmt = (req.format or "pdf").lower()
    if fmt == "pdf":
        out_path = os.path.join(out_dir, f"FIS_profile_{req.session_id}.pdf")
        render_profile_pdf_from_db(
            out_path, db,
            profile_names=req.profile_names,
            panels=db.panels,
            page_size=db.header.page_size,
            h_scale=db.header.h_scale, v_scale=db.header.v_scale,
            custom_page=custom,
        )
    elif fmt == "dxf":
        out_path = os.path.join(out_dir, f"FIS_profile_{req.session_id}.dxf")
        export_dxf(
            out_path, db,
            profile_names=req.profile_names,
            panels=db.panels,
            page_size=db.header.page_size,
            h_scale=db.header.h_scale, v_scale=db.header.v_scale,
            dxf_version=req.dxf_version,
            include_grid=req.include_grid,
            include_title_block=req.include_title_block,
            custom_page=custom,
        )
    else:
        raise HTTPException(400, f"Unsupported format: {fmt}")
    return {
        "path": out_path,
        "size_bytes": os.path.getsize(out_path),
        "format": fmt,
        "panel_count": len(db.panels),
        "h_scale": db.header.h_scale,
        "v_scale": db.header.v_scale,
        "page_size": db.header.page_size,
    }


# Back-compat alias
class ReportRequest(BaseModel):
    session_id: str
    profile_names: list[str] | None = None
    stream_name: str = "STREAM NAME"
    community: str = "AND INCORPORATED AREAS"
    county: str = "COUNTY"
    state: str = "IN"
    panel_no: int = 1
    h_scale: float = 500.0
    v_scale: float = 5.0
    distance_unit: str = "Feet"
    vertical_datum: str = "NAVD88"
    start_station_description: str = ""


@router.post("/profile.pdf")
def build_profile_pdf(req: ReportRequest) -> dict:
    return export_profile(ExportOptions(
        session_id=req.session_id,
        format="pdf",
        profile_names=req.profile_names,
        stream_name=req.stream_name,
        community=req.community,
        county=req.county,
        state=req.state,
        panel_no=req.panel_no,
        h_scale=req.h_scale,
        v_scale=req.v_scale,
        distance_unit=req.distance_unit,
        vertical_datum=req.vertical_datum,
        start_station_description=req.start_station_description,
    ))


@router.post("/fdt.pdf")
def build_fdt(req: FdtRequest) -> dict:
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = _session_db(req.session_id)
    if req.table_name:
        db.header.table_name = req.table_name
    if req.table_title:
        db.header.table_title = req.table_title
    out = os.path.join(_cache_dir(req.session_id), f"FDT_{req.session_id}.pdf")
    render_fdt_pdf(out, db, footnotes=req.footnotes or STANDARD_FOOTNOTES[:1])
    return {"path": out, "size_bytes": os.path.getsize(out)}


@router.post("/firm.zip")
def build_firm(req: FirmExportRequest) -> dict:
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = _session_db(req.session_id)
    out_dir = os.path.join(_cache_dir(req.session_id), "firm")
    export_firm_shapefiles(out_dir, db, dfirm_id=req.dfirm_id)
    zpath = os.path.join(_cache_dir(req.session_id), f"FIRM_{req.session_id}.zip")
    zip_firm_export(out_dir, zpath)
    return {"path": zpath, "size_bytes": os.path.getsize(zpath)}


@router.post("/merge.pdf")
def merge_pdfs(req: PdfMergeRequest) -> dict:
    from pypdf import PdfWriter, PdfReader
    if not req.paths:
        raise HTTPException(400, "No paths provided")
    writer = PdfWriter()
    for p in req.paths:
        pp = Path(p)
        if not pp.is_file():
            raise HTTPException(404, f"Missing: {p}")
        # safety: only temp cache trees
        if "rasplotreplica" not in str(pp.resolve()).lower():
            raise HTTPException(400, "Path outside session cache")
        reader = PdfReader(str(pp))
        for page in reader.pages:
            writer.add_page(page)
    out = os.path.join(tempfile.gettempdir(), "rasplotreplica", req.out_name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        writer.write(fh)
    return {"path": out, "size_bytes": os.path.getsize(out)}


@router.get("/page-sizes")
def list_page_sizes() -> dict:
    return {
        "sizes": {k: {"width_in": v[0], "height_in": v[1]} for k, v in PAGE_SIZES.items()},
        "distance_units": ["Feet", "Hundred Feet", "Thousand Feet", "Meters", "Kilometers", "Miles"],
        "dxf_versions": ["R2013", "R2010", "R2007", "R2000"],
        "linetypes": ["CONTINUOUS", "DASHED", "DOTTED", "DASHDOT", "CENTER", "HIDDEN", "PHANTOM"],
    }


@router.get("/fdt/footnotes")
def list_footnotes() -> dict:
    return {"footnotes": STANDARD_FOOTNOTES}
