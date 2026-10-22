"""DXF flood-profile exporter (RASPLOT v3 §7 Create DXF equivalent).

Produces AutoCAD DXF with true print scale:
  modelspace X = station / h_scale   (inches along paper)
  modelspace Y = elevation / v_scale (inches along paper)

Each panel is placed with its own origin offset (panel_x, panel_y inches).
Styling (colors, linetypes, legend) comes from ProfileMeta / Frequency editor
and the current view options.
"""
from __future__ import annotations
from .hydraulics import segments, resolve_profiles, profile_label

import math
import textwrap
from pathlib import Path
from typing import Any, Iterable, Optional

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf.enums import TextEntityAlignment

from .export_geometry import page_dimensions, plot_dimensions, validate_scales, validate_export, ExportGeometryError, DXF_VERSIONS, LEFT, BOTTOM
from .engineering_drawing import DrawingBuildConfig, build_engineering_drawing

from .structure_model import export_faces

from .rasplot_db import (
    PAGE_SIZES, PLOT_MARGIN_L, PLOT_MARGIN_R, PLOT_MARGIN_B, PLOT_MARGIN_T,
    PanelDef, ProfileMeta, Landmark, WorkingRow, WorkHeader,
    RasplotDatabase, distance_unit_factor,
)


# ezdxf linetype name aliases
_LTYPE_MAP = {
    "CONTINUOUS": "Continuous",
    "DASHED": "DASHED",
    "DOTTED": "DOTTED",
    "DASHDOT": "DASHDOT",
    "CENTER": "CENTER",
    "HIDDEN": "HIDDEN",
    "PHANTOM": "PHANTOM",
}


def _hex_to_aci(hex_color: str) -> int:
    """Map #RRGGBB to closest AutoCAD Color Index (simple table)."""
    h = (hex_color or "#000000").lstrip("#")
    if len(h) != 6:
        return 7  # white/black
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # Common ACI picks
    if r > 200 and g < 80 and b < 80:
        return 1  # red
    if r > 200 and g > 100 and b < 80:
        return 30  # orange
    if r > 150 and g < 80 and b > 150:
        return 6  # magenta
    if r < 80 and g < 80 and b > 180:
        return 5  # blue
    if r < 80 and g > 150 and b < 80:
        return 3  # green
    if r > 180 and g > 180 and b < 80:
        return 2  # yellow
    if r > 150 and g > 150 and b > 150:
        return 7  # white
    if max(r, g, b) < 80:
        return 0  # byblock / black
    return 7


def _ensure_linetypes(doc: ezdxf.Drawing) -> None:
    for name in ("DASHED", "DOTTED", "DASHDOT", "CENTER", "HIDDEN", "PHANTOM"):
        if name not in doc.linetypes:
            try:
                doc.linetypes.new(name, dxfattribs={
                    "description": name,
                    "pattern": _ltype_pattern(name),
                })
            except Exception:
                pass


def _ltype_pattern(name: str) -> list:
    # ezdxf pattern format: [total_length, ...elements]
    patterns = {
        "DASHED": [0.5, 0.5, -0.25],
        "DOTTED": [0.25, 0.0, -0.25],
        "DASHDOT": [1.0, 0.5, -0.25, 0.0, -0.25],
        "CENTER": [2.0, 1.25, -0.25, 0.25, -0.25],
        "HIDDEN": [0.5, 0.25, -0.125],
        "PHANTOM": [2.5, 1.25, -0.25, 0.25, -0.25, 0.25, -0.25],
    }
    return patterns.get(name, [0.0])


def _layer(doc, name: str, color: int = 7, linetype: str = "Continuous") -> None:
    if name not in doc.layers:
        doc.layers.add(name, color=color, linetype=linetype)


def _profile_layer(profile: ProfileMeta, legacy_modelspace: bool) -> str:
    return profile.layer or f"PROF_{profile.profile_id}"


def _station_intervals_intersect(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> bool:
    first_min, first_max = sorted((first_start, first_end))
    second_min, second_max = sorted((second_start, second_end))
    return first_max >= second_min and first_min <= second_max


def clip_station_elevation_polyline(
    points: Iterable[tuple[float, float]],
    clip_start: float,
    clip_end: float,
) -> list[tuple[float, float]]:
    """Clip station/elevation segments without extrapolating or changing order."""
    source = list(points)
    if not source or clip_start >= clip_end:
        return []
    if len(source) == 1:
        return source[:] if clip_start <= source[0][0] <= clip_end else []

    clipped: list[tuple[float, float]] = []

    def append_unique(point: tuple[float, float]) -> None:
        if clipped and math.isclose(clipped[-1][0], point[0], rel_tol=0.0, abs_tol=1e-12) \
                and math.isclose(clipped[-1][1], point[1], rel_tol=0.0, abs_tol=1e-12):
            return
        clipped.append(point)

    for (x0, y0), (x1, y1) in zip(source, source[1:]):
        dx = x1 - x0
        if math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=1e-12):
            if clip_start <= x0 <= clip_end:
                append_unique((x0, y0))
                append_unique((x1, y1))
            continue

        t_start = (clip_start - x0) / dx
        t_end = (clip_end - x0) / dx
        t0 = max(0.0, min(t_start, t_end))
        t1 = min(1.0, max(t_start, t_end))
        if t0 > t1:
            continue

        def interpolate(t: float) -> tuple[float, float]:
            station = x0 + t * dx
            if math.isclose(station, clip_start, rel_tol=0.0, abs_tol=1e-9):
                station = clip_start
            elif math.isclose(station, clip_end, rel_tol=0.0, abs_tol=1e-9):
                station = clip_end
            return station, y0 + t * (y1 - y0)

        append_unique(interpolate(t0))
        append_unique(interpolate(t1))

    return clipped


def export_dxf(
    out_path: str | Path,
    db: RasplotDatabase,
    profile_names: list[str] | None = None,
    panels: list[PanelDef] | None = None,
    page_size: str | None = None,
    h_scale: float | None = None,
    v_scale: float | None = None,
    dxf_version: str = "R2013",
    include_grid: bool = True,
    include_title_block: bool = True,
    custom_page: tuple[float, float] | None = None,
    legacy_modelspace: bool = False,
    revision_interval: tuple[float, float] | None = None,
    drawing_config: DrawingBuildConfig | None = None,
) -> str:
    """Write a multi-panel FIS flood profile DXF and return the path."""
    out_path = Path(out_path)
    header = db.header
    page_size = page_size or header.page_size or "11x17"
    h_scale = float(h_scale if h_scale is not None else header.h_scale)
    v_scale = float(v_scale if v_scale is not None else header.v_scale)
    validate_scales(h_scale, v_scale)
    page_w, page_h = page_dimensions(page_size, custom_page)
    if legacy_modelspace:
        raise ExportGeometryError("legacy_modelspace is outside the validated public paper-inch export contract.")

    panels = panels if panels is not None else db.panels
    if not panels:
        from .rasplot_db import auto_panels
        panels = auto_panels(db.working, h_scale=h_scale, v_scale=v_scale, page_size=page_size)

    # Resolve selected profiles
    if profile_names:
        selected = resolve_profiles(db.profiles,profile_names)
    else:
        selected = [p for p in db.profiles if p.selected] or list(db.profiles[:1])

    unit_factor = distance_unit_factor(header.unit)

    if dxf_version not in DXF_VERSIONS:
        raise ExportGeometryError(f"Unsupported DXF version {dxf_version!r}; choose {', '.join(DXF_VERSIONS)}.")
    validate_export(db, panels, selected, (page_w, page_h), revision_interval)
    drawing = build_engineering_drawing(
        db, panels, [profile.profile_id for profile in selected],
        revision=revision_interval, page_size=page_size, custom_page=custom_page,
        config=drawing_config)
    doc = ezdxf.new(dxf_version)
    doc.header["$INSUNITS"] = 1
    doc.header["$MEASUREMENT"] = 0
    doc.header["$LUNITS"] = 2
    _ensure_linetypes(doc)
    msp = doc.modelspace()

    # Layers
    _layer(doc, "INVERT", color=0, linetype="Continuous")
    if legacy_modelspace:
        _layer(doc, "STREAM", color=0, linetype="Continuous")
    _layer(doc, "GRID_MAJOR", color=8, linetype="Continuous")
    _layer(doc, "GRID_MINOR", color=9, linetype="Continuous")
    _layer(doc, "PANEL_BORDER", color=7)
    _layer(doc, "TITLE_BLOCK", color=7)
    _layer(doc, "AXIS_LABELS", color=7)
    _layer(doc, "LEGEND", color=7)
    _layer(doc, "PANEL_ID", color=7)
    _layer(doc, "CROSS_SECTIONS", color=7)
    _layer(doc, "ENGINEERING_ANNOTATIONS", color=7)
    _layer(doc, "XS_LABEL", color=7)
    _layer(doc, "LANDMARK", color=7)
    _layer(doc, "BACKWATER", color=1)
    _layer(doc, "STRUCT_BRIDGE", color=7)
    _layer(doc, "STRUCT_CULVERT", color=7)
    _layer(doc, "STRUCT_CULVERT_OUTLINE", color=7)
    _layer(doc, "STRUCT_WEIR", color=7)
    _layer(doc, "STRUCT_LATERAL", color=7)
    _layer(doc, "STRUCT_OTHER", color=7)
    if legacy_modelspace:
        _layer(doc, "CULVERT", color=7)
    for p in selected:
        lt = _LTYPE_MAP.get((p.linetype or "CONTINUOUS").upper(), "Continuous")
        _layer(doc, _profile_layer(p, legacy_modelspace), color=_hex_to_aci(p.color), linetype=lt)
    profile_layers = {profile.profile_id: _profile_layer(profile, legacy_modelspace)
                      for profile in selected}

    xs_rows = [r for r in db.working if r.kind == "xs"]
    # Lay out panels horizontally with 0.5" gap between
    gap = 0.5
    cursor_x = 0.0
    for pi, (panel, drawing_panel) in enumerate(zip(panels, drawing.panels)):
        ox = panel.panel_x if panel.panel_x is not None else cursor_x
        oy = panel.panel_y if panel.panel_y is not None else 0.0
        ph = panel.h_scale or h_scale
        pv = panel.v_scale or v_scale
        # plot area inside sheet (must match auto_panels usable width)
        margin_l, margin_b = LEFT, BOTTOM
        plot_w, plot_h = plot_dimensions(panel, (page_w, page_h))
        # origin of data axes in modelspace inches
        ax0 = ox + margin_l
        ay0 = oy + margin_b

        def sx(station: float) -> float:
            return ax0 + (station - panel.start_station) / ph

        def sy(elev: float) -> float:
            return ay0 + (elev - panel.start_elevation) / pv

        def engineering_sx(station: float) -> float:
            return station if legacy_modelspace else sx(station)

        def engineering_sy(elev: float) -> float:
            if not legacy_modelspace:
                return sy(elev)
            vertical_base = panel.start_elevation - 1.0
            return 100.0 * (elev - vertical_base)

        clip_start = panel.start_station
        clip_end = panel.end_station
        if revision_interval is not None:
            clip_start = max(clip_start, revision_interval[0])
            clip_end = min(clip_end, revision_interval[1])

        # Border
        msp.add_lwpolyline(
            [(ox, oy), (ox + page_w, oy), (ox + page_w, oy + page_h), (ox, oy + page_h), (ox, oy)],
            dxfattribs={"layer": "PANEL_BORDER", "closed": True},
        )
        # Plot area border
        msp.add_lwpolyline(
            [(ax0, ay0), (ax0 + plot_w, ay0), (ax0 + plot_w, ay0 + plot_h), (ax0, ay0 + plot_h), (ax0, ay0)],
            dxfattribs={"layer": "PANEL_BORDER", "closed": True},
        )

        if include_grid:
            _draw_grid(msp, drawing_panel, sx, sy, ax0, ay0, plot_w, plot_h)

        for polyline in drawing_panel.polylines:
            points = [(sx(point.x), sy(point.y)) for point in polyline.points]
            if polyline.kind == "STREAM_BED" and len(points) >= 2:
                msp.add_lwpolyline(points, dxfattribs={"layer": "INVERT", "lineweight": 25})
            elif polyline.kind == "PROFILE" and len(points) >= 2:
                layer = profile_layers.get(polyline.profile_id, polyline.layer)
                msp.add_lwpolyline(
                    points,
                    dxfattribs={"layer": layer,
                                "lineweight": int((polyline.lineweight or .3) * 100)})
            elif polyline.kind == "STRUCTURE_FACE" and len(points) >= 2:
                msp.add_line(points[0], points[1], dxfattribs={"layer": "STRUCT_CULVERT"})
            elif polyline.kind == "CULVERT_OUTLINE" and len(points) >= 3:
                msp.add_lwpolyline(
                    points, close=True,
                    dxfattribs={"layer": "STRUCT_CULVERT_OUTLINE", "lineweight": 45})

        # Retain the existing low-chord face in addition to the shared face geometry.
        for face in export_faces(db.working,clip_start,clip_end):
            x = sx(face.cumdist)
            msp.add_lwpolyline([(x,sy(face.low_chord)),(x,sy(face.high_chord))],
                              dxfattribs={"layer":"STRUCT_CULVERT"})
        for annotation in drawing_panel.annotations:
            x, y = sx(annotation.anchor.x), sy(annotation.anchor.y)
            if annotation.kind in {"CROSS_SECTION", "CROSS_SECTION_LOCATION"}:
                half = 0.035
                msp.add_line((x - half, y), (x + half, y),
                             dxfattribs={"layer": "CROSS_SECTIONS", "lineweight": 13})
                msp.add_line((x, y - half), (x, y + half),
                             dxfattribs={"layer": "CROSS_SECTIONS", "lineweight": 13})
                layer, height, align = "XS_LABEL", 0.11, TextEntityAlignment.MIDDLE_CENTER
            elif annotation.kind == "LANDMARK":
                layer, height, align = "LANDMARK", 0.10, TextEntityAlignment.LEFT
            elif annotation.kind == "BACKWATER":
                msp.add_line((ax0, y), (ax0 + plot_w, y),
                             dxfattribs={"layer": "BACKWATER", "linetype": "DASHED"})
                layer, height, align = "BACKWATER", 0.10, TextEntityAlignment.LEFT
            elif annotation.kind == "STRUCTURE":
                layer, height, align = "STRUCT_OTHER", 0.08, TextEntityAlignment.LEFT
            else:
                layer, height, align = "ENGINEERING_ANNOTATIONS", 0.10, TextEntityAlignment.LEFT
            if annotation.text:
                msp.add_text(
                    annotation.text,
                    dxfattribs={"layer": layer, "height": height,
                                "rotation": annotation.rotation_degrees},
                ).set_placement((x, y), align=align)

        # Title block
        if include_title_block:
            _title_block(msp, ox, oy, page_w, page_h, plot_w, drawing_panel, drawing)

        # Axis labels
        msp.add_text(
            drawing.presentation.horizontal_axis_title,
            dxfattribs={"layer": "AXIS_LABELS", "height": 0.10},
        ).set_placement((ax0 + plot_w / 2, oy + 0.35), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(
            drawing.presentation.vertical_axis_title,
            dxfattribs={"layer": "AXIS_LABELS", "height": 0.10, "rotation": 90},
        ).set_placement((ox + 0.35, ay0 + plot_h / 2), align=TextEntityAlignment.MIDDLE_CENTER)

        # Tick labels on axes
        _axis_labels(msp, drawing_panel, sx, sy, ax0, ay0, unit_factor)

        cursor_x = ox + page_w + gap

    doc.saveas(str(out_path))
    return str(out_path)


def _draw_grid(msp, panel, sx, sy, ax0, ay0, plot_w, plot_h) -> None:
    for grid_line in panel.grid_lines:
        layer = "GRID_MAJOR" if grid_line.major else "GRID_MINOR"
        lineweight = 25 if grid_line.major else 9
        if grid_line.axis == "STATION":
            x = sx(grid_line.coordinate)
            start, end = (x, ay0), (x, ay0 + plot_h)
        else:
            y = sy(grid_line.coordinate)
            start, end = (ax0, y), (ax0 + plot_w, y)
        msp.add_line(start, end, dxfattribs={"layer": layer, "lineweight": lineweight})


def _title_block(msp, ox, oy, page_w, page_h, plot_w, panel, drawing) -> None:
    x = ox + LEFT + plot_w + 0.22
    y = oy + page_h - 0.35
    width = max(16, int((page_w - LEFT - plot_w - 0.45) * 12))

    def add_text(text: str, height: float = 0.10, layer: str = "TITLE_BLOCK", bold: bool = False) -> None:
        nonlocal y
        wrapped = textwrap.fill(text.upper(), width=width)
        msp.add_text(wrapped, dxfattribs={
            "layer": layer, "height": height,
            "style": "Standard",
        }).set_placement((x, y), align=TextEntityAlignment.LEFT)
        y -= max(height * 1.6, height * 1.6 * len(wrapped.splitlines()))

    identity = drawing.identity
    add_text(drawing.presentation.page_title, 0.15, "TITLE_BLOCK", True)
    for value, height in (
        (identity.flooding_source_name, 0.11),
        (identity.stream_name, 0.10),
        (", ".join(value for value in (identity.community, identity.county, identity.state) if value), 0.09),
        (identity.agency, 0.09),
    ):
        if value:
            add_text(value, height)
    add_text(f"PANEL {panel.display_id}", 0.11, "PANEL_ID", True)
    add_text(f'H 1" = {panel.horizontal_scale:g} FT  V 1" = {panel.vertical_scale:g} FT', 0.08)
    add_text(drawing.presentation.vertical_datum_disclosure, 0.09)
    y -= 0.08
    add_text("LEGEND", 0.10, "LEGEND", True)
    for entry in drawing.presentation.legend_entries:
        lineweight = 25 if entry.kind == "STREAM_BED" else int((entry.lineweight or 0.3) * 100)
        color = _hex_to_aci(entry.color)
        linetype = _LTYPE_MAP.get((entry.linetype or "CONTINUOUS").upper(), "Continuous")
        msp.add_line((x, y + 0.04), (x + 0.32, y + 0.04), dxfattribs={
            "layer": "LEGEND", "color": color, "linetype": linetype,
            "lineweight": lineweight,
        })
        label = textwrap.fill(entry.label.upper(), width=max(12, width - 8))
        msp.add_text(label, dxfattribs={"layer": "LEGEND", "height": 0.08}
                     ).set_placement((x + 0.38, y), align=TextEntityAlignment.LEFT)
        y -= max(0.13, 0.13 * len(label.splitlines()))
    notes = list(drawing.presentation.data_unavailable_footnotes)
    notes.extend(drawing.presentation.annotation_warnings)
    for note in notes:
        add_text(note, 0.07, "ENGINEERING_ANNOTATIONS")


def _axis_labels(msp, panel, sx, sy, ax0, ay0, unit_factor) -> None:
    for grid_line in panel.grid_lines:
        if not grid_line.major:
            continue
        if grid_line.axis == "STATION":
            label = f"{grid_line.coordinate * unit_factor:g}"
            placement = (sx(grid_line.coordinate), ay0 - 0.18)
            alignment = TextEntityAlignment.TOP_CENTER
        else:
            label = f"{grid_line.coordinate:g}"
            placement = (ax0 - 0.08, sy(grid_line.coordinate))
            alignment = TextEntityAlignment.MIDDLE_RIGHT
        msp.add_text(label, dxfattribs={"layer": "AXIS_LABELS", "height": 0.08}
                     ).set_placement(placement, align=alignment)
