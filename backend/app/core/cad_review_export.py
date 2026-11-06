"""Editor-compatible profile package in raw engineering coordinates.

This is intentionally separate from the validated paper-inch engineering DXF.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import math
import tempfile
import zipfile

import ezdxf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ezdxf.enums import TextEntityAlignment

from .engineering_drawing import (
    DrawingBuildConfig, annotation_text_anchor, build_engineering_drawing,
)
from .dxf_export import _hex_to_aci


CAD_REVIEW_DPI = 300
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CadReviewBounds:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def raster_size(self, h_scale: float, v_scale: float, dpi: int = CAD_REVIEW_DPI) -> tuple[int, int]:
        width = max(1, round((self.x_max - self.x_min) / h_scale * dpi))
        height = max(1, round((self.y_max - self.y_min) / v_scale * dpi))
        return width, height

    def world_file(self, width: int, height: int) -> str:
        x_pixel = (self.x_max - self.x_min) / width
        y_pixel = (self.y_max - self.y_min) / height
        values = (x_pixel, 0.0, 0.0, -y_pixel,
                  self.x_min + x_pixel / 2.0, self.y_max - y_pixel / 2.0)
        return "".join(f"{value:.15g}\n" for value in values)


def _bounds(panel, revision: tuple[float, float]) -> CadReviewBounds:
    lo = max(float(panel.start_station), float(revision[0]))
    hi = min(float(panel.end_station), float(revision[1]))
    values = (lo, hi, panel.start_elevation, panel.end_elevation)
    if any(not math.isfinite(float(value)) for value in values) or lo >= hi or panel.start_elevation >= panel.end_elevation:
        raise ValueError(f"Panel {panel.panel}: CAD Review bounds must be finite and increasing.")
    return CadReviewBounds(lo, hi, float(panel.start_elevation), float(panel.end_elevation))


def _geometry(drawing_panel):
    polylines = []
    for polyline in drawing_panel.polylines:
        if len(polyline.points) < 2:
            continue
        if polyline.kind == "STREAM_BED":
            layer, color = "STREAM_BED", 7
        elif polyline.kind == "PROFILE":
            layer, color = polyline.layer, _hex_to_aci(polyline.color)
        else:
            layer, color = "STRUCTURE", 7
        points = [(point.x, point.y) for point in polyline.points]
        polylines.append((polyline.identity, polyline.kind, layer, color, points))
    return polylines, drawing_panel.annotations


def _add_polyline(msp, points, layer: str, color: int = 7) -> None:
    if len(points) >= 2:
        msp.add_polyline2d(points, dxfattribs={"layer": layer, "color": color})


def _add_grid_dxf(msp, drawing_panel, bounds: CadReviewBounds) -> None:
    for grid_line in drawing_panel.grid_lines:
        if grid_line.axis == "STATION":
            if not bounds.x_min <= grid_line.coordinate <= bounds.x_max:
                continue
            points = [(grid_line.coordinate, bounds.y_min), (grid_line.coordinate, bounds.y_max)]
        else:
            if not bounds.y_min <= grid_line.coordinate <= bounds.y_max:
                continue
            points = [(bounds.x_min, grid_line.coordinate), (bounds.x_max, grid_line.coordinate)]
        layer = "GRID_MAJOR" if grid_line.major else "GRID_MINOR"
        color = 8 if grid_line.major else 9
        _add_polyline(msp, points, layer, color)


def _profile_labels(drawing, drawing_panel):
    labels = {profile.profile_id: profile.legend_text for profile in drawing.profiles}
    result = []
    for profile_id, label in labels.items():
        runs = [polyline for polyline in drawing_panel.polylines
                if polyline.kind == "PROFILE" and polyline.profile_id == profile_id]
        if label and runs:
            point = runs[-1].points[-1]
            result.append((label, point.x, point.y,
                           point.x >= drawing_panel.clip_bounds.x_max))
    return result


def _write_dxf(path: Path, drawing, drawing_panel, bounds: CadReviewBounds) -> None:
    doc = ezdxf.new("R12")
    msp = doc.modelspace()
    layers = {
        "GRID_MAJOR": 8,
        "GRID_MINOR": 9,
        "STREAM_BED": 7,
        "STRUCTURE": 7,
        "CROSS_SECTIONS": 7,
        "XS_LABEL": 7,
        "LANDMARK": 7,
        "BACKWATER_USER": 1,
        "STRUCTURE_LABEL": 7,
        "PROFILE_LABELS": 7,
        "PANEL_METADATA": 7,
        "ANNOTATION_LEADERS": 8,
    }
    for layer, color in layers.items():
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)

    polylines, annotations = _geometry(drawing_panel)
    for _identity, _kind, layer, color, points in polylines:
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
        _add_polyline(msp, points, layer, color)

    _add_grid_dxf(msp, drawing_panel, bounds)
    text_height = max((bounds.y_max - bounds.y_min) / 90.0, 0.01)
    for annotation in annotations:
        x, y = annotation.anchor.x, annotation.anchor.y
        text_anchor = annotation_text_anchor(annotation)
        text_x, text_y = text_anchor.x, text_anchor.y
        if annotation.kind == "CROSS_SECTION":
            msp.add_point((x, y), dxfattribs={"layer": "CROSS_SECTIONS"})
            layer = "XS_LABEL"
            alignment = TextEntityAlignment.MIDDLE_CENTER
        elif annotation.kind == "LANDMARK":
            layer = "LANDMARK"
            alignment = TextEntityAlignment.RIGHT if x >= bounds.x_max else TextEntityAlignment.LEFT
        elif annotation.kind == "BACKWATER":
            if bounds.y_min <= y <= bounds.y_max:
                _add_polyline(msp, [(bounds.x_min, y), (bounds.x_max, y)], "BACKWATER_USER", 1)
            layer = "BACKWATER_USER"
            alignment = TextEntityAlignment.LEFT
        elif annotation.kind == "STRUCTURE":
            layer = "STRUCTURE_LABEL"
            alignment = TextEntityAlignment.RIGHT if x >= bounds.x_max else TextEntityAlignment.LEFT
        else:
            layer = "ENGINEERING_ANNOTATIONS"
            alignment = TextEntityAlignment.RIGHT if x >= bounds.x_max else TextEntityAlignment.LEFT
            if layer not in doc.layers:
                doc.layers.add(layer, color=7)
        if not annotation.text:
            continue
        if annotation.leader_required:
            _add_polyline(msp, [(x, y), (text_x, text_y)], "ANNOTATION_LEADERS", 8)
        msp.add_text(
            str(annotation.text),
            dxfattribs={"layer": layer, "height": text_height,
                        "rotation": annotation.rotation_degrees},
        ).set_placement((text_x, text_y), align=alignment)

    for label, x, y, right_aligned in _profile_labels(drawing, drawing_panel):
        alignment = TextEntityAlignment.RIGHT if right_aligned else TextEntityAlignment.LEFT
        msp.add_text(label, dxfattribs={"layer": "PROFILE_LABELS", "height": text_height}
                     ).set_placement((x, y), align=alignment)

    datum = drawing.vertical_datum or "UNKNOWN"
    provenance = drawing.datum_provenance or "UNKNOWN"
    msp.add_text(
        f"PANEL {drawing_panel.display_id} | VERTICAL DATUM: {datum} ({provenance})",
        dxfattribs={"layer": "PANEL_METADATA", "height": text_height,
                    "insert": (bounds.x_min, bounds.y_max)},
    )
    doc.saveas(path)


def _write_png(path: Path, drawing, drawing_panel, bounds: CadReviewBounds,
               width: int, height: int) -> None:
    polylines, annotations = _geometry(drawing_panel)
    fig = plt.figure(figsize=(width / CAD_REVIEW_DPI, height / CAD_REVIEW_DPI), dpi=CAD_REVIEW_DPI, layout="none")
    ax = fig.add_axes([0, 0, 1, 1])
    profile_colors = {profile.profile_id: profile.color or "#196bd6"
                      for profile in drawing.profiles}
    for grid_line in drawing_panel.grid_lines:
        if grid_line.axis == "STATION":
            if not bounds.x_min <= grid_line.coordinate <= bounds.x_max:
                continue
            x_values = [grid_line.coordinate, grid_line.coordinate]
            y_values = [bounds.y_min, bounds.y_max]
        else:
            if not bounds.y_min <= grid_line.coordinate <= bounds.y_max:
                continue
            x_values = [bounds.x_min, bounds.x_max]
            y_values = [grid_line.coordinate, grid_line.coordinate]
        ax.plot(x_values, y_values,
                color="#858b91" if grid_line.major else "#d5d9dc",
                linewidth=0.45 if grid_line.major else 0.2,
                zorder=0, clip_on=True)

    for _identity, kind, _layer, _aci, points in polylines:
        profile_id = next((item.profile_id for item in drawing_panel.polylines
                           if item.kind == "PROFILE" and item.identity == _identity), "")
        color = profile_colors.get(profile_id, "black") if kind == "PROFILE" else "black"
        ax.plot([point[0] for point in points], [point[1] for point in points],
                color=color, linewidth=1.2 if kind == "PROFILE" else 0.9,
                zorder=2, clip_on=True)

    for annotation in annotations:
        x, y = annotation.anchor.x, annotation.anchor.y
        text_anchor = annotation_text_anchor(annotation)
        text_x, text_y = text_anchor.x, text_anchor.y
        if annotation.kind == "BACKWATER" and bounds.y_min <= y <= bounds.y_max:
            ax.plot([bounds.x_min, bounds.x_max], [y, y], color="red",
                    linewidth=0.8, zorder=2, clip_on=True)
        if not bounds.x_min <= x <= bounds.x_max or not bounds.y_min <= y <= bounds.y_max:
            continue
        if annotation.kind == "CROSS_SECTION":
            ax.plot([x], [y], marker="o", color="black", markersize=2,
                    linestyle="none", zorder=3, clip_on=True)
        if annotation.leader_required:
            ax.plot([x, text_x], [y, text_y], color="#555555", linewidth=0.45,
                    zorder=3, clip_on=True)
        ax.text(text_x, text_y, annotation.text, fontsize=6,
                rotation=annotation.rotation_degrees,
                ha="center" if annotation.kind == "CROSS_SECTION" else "left",
                va="bottom", color="red" if annotation.kind == "BACKWATER" else "black",
                zorder=4, clip_on=True)

    for label, x, y, right_aligned in _profile_labels(drawing, drawing_panel):
        ax.text(x, y, label, fontsize=6, ha="right" if right_aligned else "left", va="bottom",
                zorder=4, clip_on=True)

    ax.set_xlim(bounds.x_min, bounds.x_max)
    ax.set_ylim(bounds.y_min, bounds.y_max)
    ax.axis("off")
    fig.savefig(path, dpi=CAD_REVIEW_DPI, bbox_inches=None, pad_inches=0)
    plt.close(fig)


def export_cad_review_package(
    out_path: str | Path,
    db,
    selected,
    panel,
    revision: tuple[float, float],
    drawing_config: DrawingBuildConfig | None = None,
    status_callback=None,
    cancel_check=None,
) -> dict:
    """Write aligned R12 DXF, PNG and PGW from one already-validated snapshot."""
    status_callback = status_callback or (lambda _stage, _message: None)
    cancel_check = cancel_check or (lambda: None)
    out_path = Path(out_path)
    status_callback("DRAWING_DEFINITION", "Building the immutable engineering drawing definition.")
    cancel_check()
    drawing = build_engineering_drawing(
        db, [panel], [profile.profile_id for profile in selected],
        revision=revision, page_size=db.header.page_size, config=drawing_config,
        cancel_check=cancel_check)
    drawing_panel = drawing.panels[0]
    bounds = CadReviewBounds(
        drawing_panel.clip_bounds.x_min, drawing_panel.clip_bounds.x_max,
        drawing_panel.clip_bounds.y_min, drawing_panel.clip_bounds.y_max)
    width, height = bounds.raster_size(panel.h_scale, panel.v_scale)
    stem = f"CAD_Review_Panel_{panel.panel}"
    with tempfile.TemporaryDirectory(prefix="rasplot-cad-review-") as directory:
        status_callback("RENDERING", "Rendering aligned CAD Review DXF and PNG files.")
        cancel_check()
        root = Path(directory)
        dxf_path, png_path, pgw_path = (root / f"{stem}.{suffix}" for suffix in ("dxf", "png", "pgw"))
        _write_dxf(dxf_path, drawing, drawing_panel, bounds)
        cancel_check()
        _write_png(png_path, drawing, drawing_panel, bounds, width, height)
        cancel_check()
        pgw_path.write_text(bounds.world_file(width, height), encoding="ascii", newline="\n")
        status_callback("WRITING", "Writing the CAD Review package.")
        cancel_check()
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in (dxf_path, png_path, pgw_path):
                archive.write(path, path.name)
        warnings = tuple(drawing.warnings) + drawing.presentation.annotation_warnings
        for warning in drawing.presentation.annotation_warnings:
            _LOGGER.warning("CAD Review annotation warning: %s", warning)
        return {"bounds": bounds, "width_px": width, "height_px": height,
            "files": [f"{stem}.dxf", f"{stem}.png", f"{stem}.pgw"],
            "warnings": warnings}
