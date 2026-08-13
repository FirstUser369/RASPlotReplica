"""DXF flood-profile exporter (RASPLOT v3 §7 Create DXF equivalent).

Produces AutoCAD DXF with true print scale:
  modelspace X = station / h_scale   (inches along paper)
  modelspace Y = elevation / v_scale (inches along paper)

Each panel is placed with its own origin offset (panel_x, panel_y inches).
Styling (colors, linetypes, legend) comes from ProfileMeta / Frequency editor
and the current view options.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Optional

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf.enums import TextEntityAlignment

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
) -> str:
    """Write a multi-panel FIS flood profile DXF and return the path."""
    out_path = Path(out_path)
    header = db.header
    page_size = page_size or header.page_size or "11x17"
    h_scale = float(h_scale if h_scale is not None else header.h_scale)
    v_scale = float(v_scale if v_scale is not None else header.v_scale)
    if custom_page:
        page_w, page_h = custom_page
    else:
        page_w, page_h = PAGE_SIZES.get(page_size, (17.0, 11.0))

    panels = panels if panels is not None else db.panels
    if not panels:
        from .rasplot_db import auto_panels
        panels = auto_panels(db.working, h_scale=h_scale, v_scale=v_scale, page_size=page_size)

    # Resolve selected profiles
    meta_by_name = {p.name: p for p in db.profiles}
    if profile_names:
        selected = [meta_by_name[n] for n in profile_names if n in meta_by_name]
    else:
        selected = [p for p in db.profiles if p.selected] or list(db.profiles[:1])

    unit_factor = distance_unit_factor(header.unit)

    try:
        doc = ezdxf.new(dxf_version)
    except Exception:
        doc = ezdxf.new("R2013")
    _ensure_linetypes(doc)
    msp = doc.modelspace()

    # Layers
    _layer(doc, "INVERT", color=0, linetype="Continuous")
    _layer(doc, "GRID", color=8, linetype="DOTTED")
    _layer(doc, "PANEL_BORDER", color=7)
    _layer(doc, "TITLE_BLOCK", color=7)
    _layer(doc, "XS_LABEL", color=7)
    _layer(doc, "LANDMARK", color=7)
    _layer(doc, "BACKWATER", color=1)
    _layer(doc, "STRUCT_BRIDGE", color=7)
    _layer(doc, "STRUCT_CULVERT", color=7)
    _layer(doc, "STRUCT_WEIR", color=7)
    _layer(doc, "STRUCT_LATERAL", color=7)
    _layer(doc, "STRUCT_OTHER", color=7)
    for p in selected:
        lt = _LTYPE_MAP.get((p.linetype or "CONTINUOUS").upper(), "Continuous")
        _layer(doc, p.layer or f"PROF_{p.name}", color=_hex_to_aci(p.color), linetype=lt)

    xs_rows = [r for r in db.working if r.kind == "xs"]
    struct_rows = [r for r in db.working if r.kind == "struct"]

    # Lay out panels horizontally with 0.5" gap between
    gap = 0.5
    cursor_x = 0.0
    for pi, panel in enumerate(panels):
        ox = panel.panel_x if panel.panel_x else cursor_x
        oy = panel.panel_y if panel.panel_y else 0.0
        ph = panel.h_scale or h_scale
        pv = panel.v_scale or v_scale
        # plot area inside sheet (must match auto_panels usable width)
        margin_l, margin_r = PLOT_MARGIN_L, PLOT_MARGIN_R
        margin_b, margin_t = PLOT_MARGIN_B, PLOT_MARGIN_T
        plot_w = page_w - margin_l - margin_r
        plot_h = page_h - margin_b - margin_t
        # origin of data axes in modelspace inches
        ax0 = ox + margin_l
        ay0 = oy + margin_b

        def sx(station: float) -> float:
            return ax0 + (station - panel.start_station) / ph

        def sy(elev: float) -> float:
            return ay0 + (elev - panel.start_elevation) / pv

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
            _draw_grid(msp, ax0, ay0, plot_w, plot_h, panel, ph, pv)

        # Invert
        inv_pts = []
        for r in xs_rows:
            if r.minchel is None:
                continue
            if panel.start_station - 1 <= r.cumdist <= panel.end_station + 1:
                inv_pts.append((sx(r.cumdist), sy(r.minchel)))
        if len(inv_pts) >= 2:
            msp.add_lwpolyline(inv_pts, dxfattribs={"layer": "INVERT", "lineweight": 25})

        # Profiles
        for pmeta in selected:
            pts = []
            for r in xs_rows:
                v = r.profiles.get(pmeta.name)
                if v is None:
                    continue
                if panel.start_station - 1 <= r.cumdist <= panel.end_station + 1:
                    pts.append((sx(r.cumdist), sy(v)))
            if len(pts) >= 2:
                layer = pmeta.layer or f"PROF_{pmeta.name}"
                lw = int((pmeta.lineweight or 0.3) * 100)
                msp.add_lwpolyline(pts, dxfattribs={"layer": layer, "lineweight": lw})

        # Structures
        for s in struct_rows:
            if not (panel.start_station - 50 <= s.cumdist <= panel.end_station + 50):
                continue
            _draw_structure(msp, s, sx, sy)

        # XS labels
        for r in xs_rows:
            if not r.label_letter:
                continue
            if not (panel.start_station <= r.cumdist <= panel.end_station):
                continue
            y = r.minchel if r.minchel is not None else panel.start_elevation
            # prefer sit above primary profile
            if selected:
                pv_val = r.profiles.get(selected[0].name)
                if pv_val is not None:
                    y = pv_val
            msp.add_text(
                r.label_letter,
                dxfattribs={"layer": "XS_LABEL", "height": 0.14,
                            "style": "Standard"},
            ).set_placement((sx(r.cumdist), sy(y) + 0.08), align=TextEntityAlignment.BOTTOM_CENTER)

            if r.label_text:
                msp.add_text(
                    r.label_text,
                    dxfattribs={"layer": "XS_LABEL", "height": 0.10},
                ).set_placement((sx(r.cumdist), sy(y) + 0.28), align=TextEntityAlignment.BOTTOM_CENTER)

        # Landmarks
        for lm in db.landmarks:
            if panel.start_station <= lm.distance <= panel.end_station:
                rot = 90.0 if (lm.orientation or "").lower().startswith("v") else 0.0
                msp.add_text(
                    lm.text,
                    dxfattribs={"layer": "LANDMARK", "height": 0.10, "rotation": rot},
                ).set_placement((sx(lm.distance), sy(lm.elevation)),
                                align=TextEntityAlignment.LEFT)

        # Backwater (use first selected profile with backwater)
        for pmeta in selected:
            if pmeta.backwater_elev is None:
                continue
            y = sy(pmeta.backwater_elev)
            # indicator line across panel where profile is below backwater
            msp.add_line((ax0, y), (ax0 + plot_w, y),
                         dxfattribs={"layer": "BACKWATER", "linetype": "DASHED"})
            if pmeta.backwater_text:
                msp.add_text(
                    pmeta.backwater_text,
                    dxfattribs={"layer": "BACKWATER", "height": 0.10},
                ).set_placement((ax0 + 0.1, y + 0.05), align=TextEntityAlignment.LEFT)
            break

        # Title block
        if include_title_block:
            _title_block(msp, ox, oy, page_w, page_h, panel, header, pi, len(panels), selected)

        # Axis labels
        msp.add_text(
            f"DISTANCE ({header.unit.upper()}) ABOVE {header.stream_distance_label.upper()}",
            dxfattribs={"layer": "TITLE_BLOCK", "height": 0.10},
        ).set_placement((ax0 + plot_w / 2, oy + 0.35), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(
            f"ELEVATION ({header.vertical_datum})",
            dxfattribs={"layer": "TITLE_BLOCK", "height": 0.10, "rotation": 90},
        ).set_placement((ox + 0.35, ay0 + plot_h / 2), align=TextEntityAlignment.MIDDLE_CENTER)

        # Tick labels on axes
        _axis_labels(msp, panel, sx, sy, ax0, ay0, plot_w, plot_h, unit_factor, header.unit)

        cursor_x = ox + page_w + gap

    doc.saveas(str(out_path))
    return str(out_path)


def _draw_grid(msp, ax0, ay0, plot_w, plot_h, panel, ph, pv) -> None:
    # vertical grid every inch of paper (= ph feet of station)
    n_v = max(1, int(round(plot_w)))
    for i in range(n_v + 1):
        x = ax0 + i
        msp.add_line((x, ay0), (x, ay0 + plot_h), dxfattribs={"layer": "GRID"})
    n_h = max(1, int(round(plot_h)))
    for j in range(n_h + 1):
        y = ay0 + j
        msp.add_line((ax0, y), (ax0 + plot_w, y), dxfattribs={"layer": "GRID"})


def _draw_structure(msp, s: WorkingRow, sx, sy) -> None:
    t = (s.struc or "").upper()
    x = sx(s.cumdist)
    yb = sy(s.minchel) if s.minchel is not None else sy(0)
    if "BRIDGE" in t and s.low_chord is not None and s.high_chord is not None:
        y0, y1 = sy(s.low_chord), sy(s.high_chord)
        w = 0.12
        msp.add_lwpolyline(
            [(x - w, y0), (x + w, y0), (x + w, y1), (x - w, y1), (x - w, y0)],
            dxfattribs={"layer": "STRUCT_BRIDGE", "closed": True},
        )
        msp.add_line((x, yb), (x, y1), dxfattribs={"layer": "STRUCT_BRIDGE"})
    elif "CULVERT" in t and s.low_chord is not None and s.high_chord is not None:
        y0, y1 = sy(s.low_chord), sy(s.high_chord)
        w = 0.10
        msp.add_lwpolyline(
            [(x - w, y0), (x + w, y0), (x + w, y1), (x - w, y1), (x - w, y0)],
            dxfattribs={"layer": "STRUCT_CULVERT", "closed": True},
        )
        msp.add_line((x, yb), (x, y1), dxfattribs={"layer": "STRUCT_CULVERT"})
    elif any(k in t for k in ("WEIR", "INLINE", "LATERAL")) and (s.high_chord is not None or s.low_chord is not None):
        crest = s.high_chord if s.high_chord is not None else s.low_chord
        yc = sy(crest)
        msp.add_line((x - 0.15, yc), (x + 0.15, yc), dxfattribs={"layer": "STRUCT_WEIR", "lineweight": 50})
        msp.add_line((x, yb), (x, yc), dxfattribs={"layer": "STRUCT_WEIR"})
    else:
        msp.add_line((x, yb), (x, yb + 0.3), dxfattribs={"layer": "STRUCT_OTHER"})
    # label
    lab = (s.struc or "STR")[:12]
    msp.add_text(lab, dxfattribs={"layer": "STRUCT_OTHER", "height": 0.08, "rotation": 90}
                 ).set_placement((x + 0.04, yb + 0.05), align=TextEntityAlignment.LEFT)


def _title_block(msp, ox, oy, page_w, page_h, panel, header, pi, n_panels, selected) -> None:
    xn = ox + 0.25
    y = oy + page_h - 0.4
    lines = [
        "FLOOD PROFILE",
        (header.stream_name or "").upper(),
        f"{(header.community or '').upper()}, {(header.county or '').upper()}, {(header.state or '').upper()}",
        f"PANEL {panel.panel + header.start_panel_no - 1} OF {n_panels + header.start_panel_no - 1}",
        f"H SCALE 1\" = {panel.h_scale:g} {header.unit}",
        f"V SCALE 1\" = {panel.v_scale:g} FT",
    ]
    for line in lines:
        msp.add_text(line, dxfattribs={"layer": "TITLE_BLOCK", "height": 0.12}
                     ).set_placement((xn, y), align=TextEntityAlignment.LEFT)
        y -= 0.22
    # Legend
    y -= 0.15
    msp.add_text("LEGEND", dxfattribs={"layer": "TITLE_BLOCK", "height": 0.10}
                 ).set_placement((xn, y), align=TextEntityAlignment.LEFT)
    y -= 0.18
    for p in selected:
        layer = p.layer or f"PROF_{p.name}"
        msp.add_line((xn, y + 0.04), (xn + 0.35, y + 0.04),
                     dxfattribs={"layer": layer, "lineweight": 35})
        msp.add_text(p.legend_text or p.name,
                     dxfattribs={"layer": "TITLE_BLOCK", "height": 0.09}
                     ).set_placement((xn + 0.42, y), align=TextEntityAlignment.LEFT)
        y -= 0.16
    msp.add_line((xn, y + 0.04), (xn + 0.35, y + 0.04),
                 dxfattribs={"layer": "INVERT", "lineweight": 25})
    msp.add_text("STREAM BED", dxfattribs={"layer": "TITLE_BLOCK", "height": 0.09}
                 ).set_placement((xn + 0.42, y), align=TextEntityAlignment.LEFT)


def _axis_labels(msp, panel, sx, sy, ax0, ay0, plot_w, plot_h, unit_factor, unit) -> None:
    # station ticks every inch
    n = max(1, int(round(plot_w)))
    for i in range(n + 1):
        stn = panel.start_station + i * panel.h_scale
        label = f"{stn * unit_factor:.0f}" if unit_factor >= 1 else f"{stn * unit_factor:.2f}"
        msp.add_text(label, dxfattribs={"layer": "TITLE_BLOCK", "height": 0.08}
                     ).set_placement((sx(stn), ay0 - 0.18), align=TextEntityAlignment.TOP_CENTER)
    n2 = max(1, int(round(plot_h)))
    for j in range(n2 + 1):
        elev = panel.start_elevation + j * panel.v_scale
        msp.add_text(f"{elev:.0f}", dxfattribs={"layer": "TITLE_BLOCK", "height": 0.08}
                     ).set_placement((ax0 - 0.08, sy(elev)), align=TextEntityAlignment.MIDDLE_RIGHT)
