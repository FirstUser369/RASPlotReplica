"""PDF + DXF report generators and HTTP endpoints.

Uses the session RasplotDatabase for working table, panels, styling and header.
"""
from __future__ import annotations
from ..core.hydraulics import segments, resolve_profiles, profile_label

import os
import tempfile
import textwrap
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

from ..core.export_geometry import (page_dimensions, plot_dimensions, validate_scales, validate_export, ExportGeometryError, DXF_VERSIONS, LEFT, BOTTOM)
from ..core.structure_model import export_faces
from ..core.merge import compute_cumdist, MergedReach
from ..core.rasplot_db import (
    PAGE_SIZES, RasplotDatabase, WorkHeader, PanelDef, ProfileMeta,
    distance_unit_factor, auto_panels, model_station_extent,
)
from ..core.dxf_export import export_dxf, clip_station_elevation_polyline
from ..core.cad_review_export import export_cad_review_package
from ..core.fdt import render_fdt_pdf, STANDARD_FOOTNOTES
from ..core.firm_export import export_firm_shapefiles, zip_firm_export
from .hecras_api import _SESSIONS, _session_db


from copy import deepcopy
from ..core.local_state import LocalRoute, register_artifact, owned_path, artifact_name, cache_root, require_artifact_capacity
from ..core.state_contract import require_stationing, panels_stale, require_revision, engineering_version
from ..core.errors import AppError, log
from ..core.local_state import discard_failed_artifact
from ..core.engineering_drawing import DrawingBuildConfig, build_engineering_drawing

router = APIRouter(route_class=LocalRoute, prefix="/api/report")


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
    revision_interval: tuple[float, float] | None = None,
    drawing_config: DrawingBuildConfig | None = None,
) -> str:
    header = db.header
    page_size = page_size or header.page_size or "11x17"
    h_scale = float(h_scale if h_scale is not None else header.h_scale)
    v_scale = float(v_scale if v_scale is not None else header.v_scale)
    validate_scales(h_scale, v_scale)
    page_w, page_h = page_dimensions(page_size, custom_page)

    if profile_names:
        selected = resolve_profiles(db.profiles,profile_names)
    else:
        selected = [p for p in db.profiles if p.selected] or list(db.profiles[:1])

    panels = panels if panels is not None else db.panels
    if not panels:
        panels = auto_panels(db.working, h_scale=h_scale, v_scale=v_scale, page_size=page_size)

    unit_factor = distance_unit_factor(header.unit)
    drawing = build_engineering_drawing(
        db, panels, [profile.profile_id for profile in selected],
        revision=revision_interval, page_size=page_size, custom_page=custom_page,
        config=drawing_config)
    profile_labels = {profile.profile_id: profile.legend_text for profile in drawing.profiles}
    pdf = PdfPages(out_path)
    try:
        for panel, drawing_panel in zip(panels, drawing.panels):
            plot_w, plot_h = plot_dimensions(panel, (page_w, page_h))
            fig = plt.figure(figsize=(page_w, page_h), layout="none")
            try:
                ax = fig.add_axes([LEFT / page_w, BOTTOM / page_h,
                                   plot_w / page_w, plot_h / page_h])
                ax.set_xlim(drawing_panel.bounds.x_min, drawing_panel.bounds.x_max)
                ax.set_ylim(drawing_panel.bounds.y_min, drawing_panel.bounds.y_max)
                for grid_line in drawing_panel.grid_lines:
                    color = "#7f8992" if grid_line.major else "#d7dce0"
                    width = 0.45 if grid_line.major else 0.18
                    if grid_line.axis == "STATION":
                        ax.axvline(grid_line.coordinate, color=color, linewidth=width, zorder=0)
                    else:
                        ax.axhline(grid_line.coordinate, color=color, linewidth=width, zorder=0)

                for polyline in drawing_panel.polylines:
                    if len(polyline.points) < 2:
                        continue
                    is_profile = polyline.kind == "PROFILE"
                    rendered_points = list(polyline.points)
                    if polyline.closed and rendered_points:
                        rendered_points.append(rendered_points[0])
                    ax.plot(
                        [point.x for point in rendered_points],
                        [point.y for point in rendered_points],
                        color=polyline.color if is_profile else "#111111",
                        linewidth=max(0.35, polyline.lineweight * 4) if is_profile else 0.8,
                        linestyle=_mpl_ls(polyline.linetype),
                        label=profile_labels.get(polyline.profile_id) if is_profile else "Stream bed",
                        zorder=2, clip_on=True)

                for annotation in drawing_panel.annotations:
                    if annotation.kind == "BACKWATER":
                        ax.axhline(annotation.anchor.y, color="#a33b32", linestyle="--",
                                   linewidth=0.75, zorder=3)
                    if annotation.kind in {"CROSS_SECTION", "CROSS_SECTION_LOCATION"}:
                        ax.plot(annotation.anchor.x, annotation.anchor.y, marker="|",
                                color="#111111", markersize=7, markeredgewidth=0.7,
                                linestyle="none", zorder=4)
                    ax.text(
                        annotation.anchor.x, annotation.anchor.y, annotation.text,
                        rotation=annotation.rotation_degrees,
                        fontsize=8 if annotation.kind == "CROSS_SECTION" else 6.5,
                        fontname="serif" if annotation.kind == "CROSS_SECTION" else "sans-serif",
                        fontweight="bold" if annotation.kind == "CROSS_SECTION" else "normal",
                        ha="center" if annotation.kind == "CROSS_SECTION" else "left",
                        va="bottom", color="#a33b32" if annotation.kind == "BACKWATER" else "#111111",
                        clip_on=True, zorder=5,
                        bbox=(dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.82)
                              if annotation.kind == "CROSS_SECTION" else None))

                major_stations = [line.coordinate for line in drawing_panel.grid_lines
                                  if line.axis == "STATION" and line.major]
                major_elevations = [line.coordinate for line in drawing_panel.grid_lines
                                    if line.axis == "ELEVATION" and line.major]
                ax.set_xticks(major_stations)
                ax.set_yticks(major_elevations)
                ax.set_xticklabels([f"{tick * unit_factor:g}" for tick in major_stations], fontsize=6.5)
                ax.set_yticklabels([f"{tick:g}" for tick in major_elevations], fontsize=6.5)
                ax.tick_params(direction="out", length=2.5, width=0.45, pad=2)
                for spine in ax.spines.values():
                    spine.set_linewidth(0.65)
                ax.set_xlabel(drawing.presentation.horizontal_axis_title, fontsize=8, labelpad=5)
                ax.set_ylabel(drawing.presentation.vertical_axis_title, fontsize=8, labelpad=6)

                sidebar_x = (LEFT + plot_w + 0.22) / page_w
                sidebar_y = 1.0 - 0.35 / page_h
                sidebar_width = page_w - LEFT - plot_w - 0.45
                text_width = max(18, int(sidebar_width * 72 / 4.2))
                identity = drawing.identity
                fig.text(sidebar_x, sidebar_y, drawing.presentation.page_title,
                         fontsize=10, fontweight="bold", va="top")
                sidebar_y -= 0.24 / page_h
                identification = (
                    (identity.flooding_source_name, 7.5, "bold"),
                    (identity.stream_name, 7.0, "normal"),
                    (", ".join(value for value in
                               (identity.community, identity.county, identity.state) if value), 6.2, "normal"),
                    (identity.agency, 6.2, "normal"),
                )
                for text, size, weight in identification:
                    if not text:
                        continue
                    wrapped = textwrap.fill(text.upper(), width=text_width)
                    fig.text(sidebar_x, sidebar_y, wrapped, fontsize=size,
                             fontweight=weight, va="top")
                    sidebar_y -= (0.14 * len(wrapped.splitlines()) + 0.04) / page_h
                fig.text(sidebar_x, sidebar_y, f"PANEL {drawing_panel.display_id}",
                         fontsize=7.2, fontweight="bold", va="top")
                sidebar_y -= 0.18 / page_h
                scale_caption = (
                    f'H 1" = {drawing_panel.horizontal_scale:g} FT  '
                    f'V 1" = {drawing_panel.vertical_scale:g} FT')
                fig.text(sidebar_x, sidebar_y, scale_caption, fontsize=5.8, va="top")
                sidebar_y -= 0.19 / page_h
                fig.text(sidebar_x, sidebar_y, drawing.presentation.vertical_datum_disclosure,
                         fontsize=6.0,
                         fontweight="bold" if drawing.vertical_datum.upper() == "UNKNOWN" else "normal",
                         va="top")
                sidebar_y -= 0.25 / page_h
                fig.text(sidebar_x, sidebar_y, "LEGEND", fontsize=6.5,
                         fontweight="bold", va="top")
                sidebar_y -= 0.17 / page_h
                for entry in drawing.presentation.legend_entries:
                    line_x0 = sidebar_x
                    line_x1 = sidebar_x + min(0.28, sidebar_width * 0.22) / page_w
                    line_y = sidebar_y - 0.025 / page_h
                    fig.add_artist(Line2D(
                        [line_x0, line_x1], [line_y, line_y], transform=fig.transFigure,
                        color=entry.color,
                        linewidth=max(0.5, entry.lineweight if entry.kind == "STREAM_BED"
                                      else entry.lineweight * 4),
                        linestyle=_mpl_ls(entry.linetype)))
                    label_x = sidebar_x + min(0.34, sidebar_width * 0.27) / page_w
                    wrapped = textwrap.fill(entry.label, width=max(12, text_width - 10))
                    fig.text(label_x, sidebar_y, wrapped, fontsize=5.8, va="top")
                    sidebar_y -= (0.14 * len(wrapped.splitlines()) + 0.04) / page_h

                notes = list(drawing.presentation.data_unavailable_footnotes)
                notes.extend(drawing.presentation.annotation_warnings)
                if drawing.vertical_datum.upper() == "UNKNOWN":
                    notes.append("Vertical datum is unknown.")
                for note in notes:
                    wrapped = textwrap.fill(note, width=text_width)
                    fig.text(sidebar_x, sidebar_y, wrapped, fontsize=5.2,
                             color="#8a3028", va="top")
                    sidebar_y -= (0.12 * len(wrapped.splitlines()) + 0.03) / page_h

                with matplotlib.rc_context({"savefig.bbox": None}):
                    pdf.savefig(fig, bbox_inches=None)
            finally:
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
    expected_engineering_version: str | None = None
    format: str = "pdf"                 # pdf | dxf
    profile_names: list[str] | None = None
    panel_numbers: list[int] | None = None
    page_size: str | None = None
    custom_page_w: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    custom_page_h: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    h_scale: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    v_scale: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    distance_unit: str | None = None
    vertical_datum: str | None = None
    require_known_datum: bool | None = None
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


class CadReviewPackageOptions(ExportOptions):
    """One-panel raw station/elevation package for the CAD Review Editor."""
    format: str = "cad-review-package"
    use_current_view: bool = False
    fit_to_one_panel: bool = False
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
    session_id: str
    paths: list[str]
    out_name: str = "merged.pdf"


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #


def _apply_export_options(db: RasplotDatabase, req: ExportOptions) -> list[PanelDef]:
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
    if req.require_known_datum is not None:
        h.require_known_datum=req.require_known_datum
    if req.vertical_datum is not None:
        datum = req.vertical_datum.strip() or "UNKNOWN"
        if datum.upper() == 'UNKNOWN': datum = 'UNKNOWN'
        if datum != h.vertical_datum:
            h.vertical_datum = datum
            h.datum_provenance = "USER_ENTERED" if datum != "UNKNOWN" else "UNKNOWN"
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
            p.selected = p.profile_id in sel

    custom = None
    if (req.custom_page_w is None) != (req.custom_page_h is None):
        raise HTTPException(400, "Specify both custom page Width and Height in inches.")
    if req.custom_page_w and req.custom_page_h:
        custom = (req.custom_page_w, req.custom_page_h)

    if req.panel_numbers is not None:
        if not req.panel_numbers:
            raise HTTPException(400, "At least one saved panel must be selected.")
        if len(set(req.panel_numbers)) != len(req.panel_numbers):
            raise HTTPException(400, "Panel selection contains duplicate identifiers.")
        selected_numbers = set(req.panel_numbers)
        available_numbers = {panel.panel for panel in db.panels}
        invalid_numbers = sorted(selected_numbers - available_numbers)
        if invalid_numbers:
            invalid = ", ".join(str(number) for number in invalid_numbers)
            raise HTTPException(400, f"Unknown saved panel identifier(s): {invalid}")
        selected_panels = [panel for panel in db.panels if panel.panel in selected_numbers]
        revision_start, revision_end = _resolved_revision_interval(db)
        outside = [
            panel.panel for panel in selected_panels
            if panel.end_station <= revision_start or panel.start_station >= revision_end
        ]
        if outside:
            invalid = ", ".join(str(number) for number in outside)
            raise HTTPException(
                400,
                f"Selected panel(s) {invalid} do not overlap the active revision range. Update Panel Setup first.",
            )
        return selected_panels

    # Generate panels for this artifact without replacing the saved session panels.
    if req.use_current_view or req.fit_to_one_panel or req.single_panel or not db.panels:
        return auto_panels(
            db.working,
            h_scale=h.h_scale,
            v_scale=h.v_scale,
            page_size=h.page_size,
            custom_page=custom,
            single_panel=req.single_panel,
            fit_to_page=req.fit_to_one_panel,
            station_range=_resolved_revision_interval(db),
        )
    return list(db.panels)


def _cache_dir(sid: str) -> str:
    root = cache_root()
    directory = (root / sid).resolve()
    if directory != root / sid:
        raise HTTPException(400, "Session cache must not be redirected by a filesystem link.")
    try:
        directory.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Invalid session artifact directory")
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def _resolved_revision_interval(db: RasplotDatabase) -> tuple[float, float] | None:
    return require_revision(db)


@router.post("/export")
def export_profile(req: ExportOptions) -> dict:
    """Export current view to PDF or DXF with scale/style/page options."""
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = deepcopy(_session_db(req.session_id))
    require_artifact_capacity(req.session_id)
    state_version = engineering_version(db)
    if req.expected_engineering_version is not None and req.expected_engineering_version != state_version:
        raise HTTPException(409, 'Engineering state changed since Export opened. Close and reopen Export to review the current state.')
    try:
        require_stationing(db)
        require_revision(db)
        if (req.panel_numbers is not None or not req.use_current_view) and panels_stale(db):
            raise ValueError("Saved panels need review after engineering changes. Open Panel Setup, review the preserved definitions and Apply before exporting saved panels.")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if req.format.lower() == "dxf" and req.dxf_version not in DXF_VERSIONS:
        raise HTTPException(400, f"Unsupported DXF version {req.dxf_version!r}; choose {', '.join(DXF_VERSIONS)}.")
    try:
        validate_scales(req.h_scale if req.h_scale is not None else db.header.h_scale,
                        req.v_scale if req.v_scale is not None else db.header.v_scale)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        export_panels = _apply_export_options(db, req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    custom = None
    if (req.custom_page_w is None) != (req.custom_page_h is None):
        raise HTTPException(400, "Specify both custom page Width and Height in inches.")
    if req.custom_page_w and req.custom_page_h:
        custom = (req.custom_page_w, req.custom_page_h)

    try:
        page = page_dimensions(db.header.page_size, custom)
        selected = resolve_profiles(db.profiles,req.profile_names) if req.profile_names else ([p for p in db.profiles if p.selected] or db.profiles[:1])
        validate_export(db, export_panels, selected, page, _resolved_revision_interval(db))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    out_path = None
    try:
        out_dir = _cache_dir(req.session_id)
        fmt = (req.format or "pdf").lower()
        if fmt == "pdf":
            out_path = os.path.join(out_dir, artifact_name("FIS_profile", ".pdf"))
            render_profile_pdf_from_db(
                out_path, db,
                profile_names=req.profile_names,
                panels=export_panels,
                page_size=db.header.page_size,
                h_scale=db.header.h_scale, v_scale=db.header.v_scale,
                custom_page=custom,
                revision_interval=_resolved_revision_interval(db),
            )
        elif fmt == "dxf":
            out_path = os.path.join(out_dir, artifact_name("FIS_profile", ".dxf"))
            export_dxf(
                out_path, db,
                profile_names=req.profile_names,
                panels=export_panels,
                page_size=db.header.page_size,
                h_scale=db.header.h_scale, v_scale=db.header.v_scale,
                dxf_version=req.dxf_version,
                include_grid=req.include_grid,
                include_title_block=req.include_title_block,
                custom_page=custom,
                revision_interval=_resolved_revision_interval(db),
            )
        else:
            raise HTTPException(400, f"Unsupported format: {fmt}")
        size_bytes = os.path.getsize(out_path)
        register_artifact(req.session_id, out_path)
        return {
            "artifact_id": Path(out_path).stem,
            "session_id": req.session_id,
            "engineering_version": state_version,
            "path": out_path,
            "size_bytes": size_bytes,
            "format": fmt,
            "panel_count": len(export_panels),
            "h_scale": export_panels[0].h_scale if export_panels else db.header.h_scale,
            "v_scale": export_panels[0].v_scale if export_panels else db.header.v_scale,
            "page_size": db.header.page_size,
        }
    except OSError as exc:
        discard_failed_artifact(req.session_id, out_path)
        log.exception('Artifact write/read failure')
        raise AppError(500, 'FILE_ACCESS', "Export could not write/read its artifact. Check temporary-folder access and free disk space, then retry.") from exc
    except HTTPException:
        discard_failed_artifact(req.session_id, out_path)
        raise
    except Exception as exc:
        discard_failed_artifact(req.session_id, out_path)
        log.exception('Renderer failed after preflight')
        raise AppError(500, 'EXPORT_FAILURE', "Export rendering failed after engineering preflight. No successful artifact was registered; report this software error with the source and export settings.") from exc


@router.post("/cad-review-package")
def export_cad_review(req: CadReviewPackageOptions) -> dict:
    """Export one validated panel as aligned raw-coordinate DXF/PNG/PGW."""
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = deepcopy(_session_db(req.session_id))
    require_artifact_capacity(req.session_id)
    state_version = engineering_version(db)
    if req.expected_engineering_version is not None and req.expected_engineering_version != state_version:
        raise HTTPException(409, "Engineering state changed since Export opened. Close and reopen Export to review the current state.")
    if req.panel_numbers is None or len(req.panel_numbers) != 1:
        raise HTTPException(400, "CAD Review Package requires exactly one selected saved panel.")
    try:
        require_stationing(db)
        revision = require_revision(db)
        if panels_stale(db):
            raise ValueError("Saved panels need review after engineering changes. Open Panel Setup, review the preserved definitions and Apply before exporting saved panels.")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        panels = _apply_export_options(db, req)
        selected = resolve_profiles(db.profiles, req.profile_names) if req.profile_names else ([p for p in db.profiles if p.selected] or db.profiles[:1])
        custom = (req.custom_page_w, req.custom_page_h) if req.custom_page_w and req.custom_page_h else None
        page = page_dimensions(db.header.page_size, custom)
        validate_export(db, panels, selected, page, revision)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    out_path = None
    try:
        out_path = os.path.join(_cache_dir(req.session_id), artifact_name("CAD_Review_Package", ".zip"))
        result = export_cad_review_package(out_path, db, selected, panels[0], revision)
        register_artifact(req.session_id, out_path)
        bounds = result["bounds"]
        return {
            "artifact_id": Path(out_path).stem,
            "session_id": req.session_id,
            "engineering_version": state_version,
            "path": out_path,
            "size_bytes": os.path.getsize(out_path),
            "format": "cad-review-package",
            "panel_count": 1,
            "panel_number": panels[0].panel,
            "bounds": {"x_min": bounds.x_min, "x_max": bounds.x_max,
                       "y_min": bounds.y_min, "y_max": bounds.y_max},
            "width_px": result["width_px"], "height_px": result["height_px"],
            "files": result["files"],
        }
    except OSError as exc:
        discard_failed_artifact(req.session_id, out_path)
        raise AppError(500, "FILE_ACCESS", "CAD Review Package could not be written. Check temporary-folder access and free disk space, then retry.") from exc
    except HTTPException:
        discard_failed_artifact(req.session_id, out_path)
        raise
    except Exception as exc:
        discard_failed_artifact(req.session_id, out_path)
        log.exception("CAD Review Package renderer failed after preflight")
        raise AppError(500, "EXPORT_FAILURE", "CAD Review Package rendering failed after engineering preflight. No successful artifact was registered.") from exc


# Back-compat alias
class ReportRequest(BaseModel):
    session_id: str
    profile_names: list[str] | None = None
    stream_name: str | None = None
    community: str | None = None
    county: str | None = None
    state: str | None = None
    panel_no: int = 1
    h_scale: float = 500.0
    v_scale: float = 5.0
    distance_unit: str = "Feet"
    vertical_datum: str | None = None
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
    raise AppError(403, "UNSUPPORTED_SCHEMA", "FDT is disabled: regulatory semantics and compliance are not validated.")
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
    raise AppError(403, "UNSUPPORTED_SCHEMA", "FIRM is disabled: coordinates, hydraulic semantics and compliance are not validated.")
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
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    if Path(req.out_name).name != req.out_name or any(c in req.out_name for c in ("/", "\\", ":")) or not req.out_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Output must be a simple PDF filename, without directories or traversal.")
    from pypdf.errors import PdfReadError
    require_artifact_capacity(req.session_id)
    out = None
    try:
        writer = PdfWriter()
        for path in req.paths:
            pp = owned_path(req.session_id, path)
            if pp.suffix.lower() != '.pdf':
                raise AppError(400, 'INVALID_INPUT', 'Merge inputs must be generated PDF artifacts.')
            if not pp.is_file():
                raise AppError(404, 'FILE_ACCESS', 'Merge input no longer exists; generate it again.')
            reader = PdfReader(str(pp))
            for page in reader.pages:
                writer.add_page(page)
        out = str(Path(_cache_dir(req.session_id)) / artifact_name(Path(req.out_name).stem, '.pdf'))
        with open(out, 'wb') as fh:
            writer.write(fh)
        register_artifact(req.session_id, out)
        return {'path': out, 'size_bytes': os.path.getsize(out)}
    except PdfReadError as exc:
        discard_failed_artifact(req.session_id, out)
        log.exception('Invalid PDF merge input')
        raise AppError(400, 'INVALID_INPUT', 'A merge input is not a readable PDF. Regenerate that artifact.') from exc
    except OSError as exc:
        discard_failed_artifact(req.session_id, out)
        log.exception('PDF merge file access failure')
        raise AppError(500, 'FILE_ACCESS', 'Unable to read/write PDF merge artifacts. Check temporary-folder access and free disk space.') from exc



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
