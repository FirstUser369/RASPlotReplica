"""Immutable, renderer-neutral engineering drawing contract.

All coordinates stored here are engineering station/elevation values. Renderers
must apply an explicit target transform rather than changing source geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Callable, Iterable

from .export_geometry import BOTTOM, LEFT, page_dimensions, validate_export
from .hydraulics import profile_label, resolve_profiles, segments
from .rasplot_db import PanelDef, normalize_panel_suffix
from .state_contract import engineering_version, require_revision
from .structure_model import export_faces


@dataclass(frozen=True)
class EngineeringPoint:
    x: float
    y: float


@dataclass(frozen=True)
class EngineeringBounds:
    x_min: float
    x_max: float
    y_min: float
    y_max: float


@dataclass(frozen=True)
class PageLayout:
    width_in: float
    height_in: float
    plot_left_in: float = LEFT
    plot_bottom_in: float = BOTTOM


@dataclass(frozen=True)
class GridDefinition:
    station_origin: float
    elevation_origin: float
    station_minor: float
    station_major: float
    elevation_minor: float
    elevation_major: float


@dataclass(frozen=True)
class GridLine:
    axis: str
    coordinate: float
    major: bool


@dataclass(frozen=True)
class DrawingPolyline:
    identity: str
    kind: str
    layer: str
    points: tuple[EngineeringPoint, ...]
    profile_id: str = ""
    color: str = "#000000"
    linetype: str = "CONTINUOUS"
    lineweight: float = 0.3
    provenance: str = "SOURCE"
    closed: bool = False


@dataclass(frozen=True)
class DrawingAnnotation:
    identity: str
    kind: str
    text: str
    anchor: EngineeringPoint
    rotation_degrees: float
    provenance: str
    review_status: str
    display_anchor: EngineeringPoint | None = None
    leader_required: bool = False


@dataclass(frozen=True)
class DrawingPanel:
    panel_id: int
    panel_suffix: str
    display_id: str
    bounds: EngineeringBounds
    clip_bounds: EngineeringBounds
    horizontal_scale: float
    vertical_scale: float
    drawing_origin_x_in: float
    drawing_origin_y_in: float
    grid: GridDefinition
    grid_lines: tuple[GridLine, ...]
    polylines: tuple[DrawingPolyline, ...]
    annotations: tuple[DrawingAnnotation, ...]


@dataclass(frozen=True)
class DrawingIdentity:
    stream_name: str
    flooding_source_name: str
    community: str
    county: str
    state: str
    agency: str


@dataclass(frozen=True)
class DrawingProfile:
    profile_id: str
    source_name: str
    display_name: str
    legend_text: str
    layer: str
    color: str
    linetype: str
    lineweight: float


@dataclass(frozen=True)
class DrawingLegendEntry:
    identity: str
    label: str
    color: str
    linetype: str
    lineweight: float
    kind: str


@dataclass(frozen=True)
class DrawingPresentation:
    page_title: str
    horizontal_axis_title: str
    vertical_axis_title: str
    vertical_datum_disclosure: str
    legend_entries: tuple[DrawingLegendEntry, ...]
    data_unavailable_footnotes: tuple[str, ...]
    annotation_warnings: tuple[str, ...]


@dataclass(frozen=True)
class EngineeringDrawingDefinition:
    engineering_version: str
    identity: DrawingIdentity
    page: PageLayout
    revision_start: float
    revision_end: float
    unit_system: str
    distance_unit: str
    vertical_datum: str
    datum_provenance: str
    profiles: tuple[DrawingProfile, ...]
    presentation: DrawingPresentation
    panels: tuple[DrawingPanel, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DrawingBuildConfig:
    station_origin: float | None = None
    elevation_origin: float = 0.0
    station_minor: float | None = None
    station_major: float | None = None
    elevation_minor: float | None = None
    elevation_major: float | None = None
    panel_suffix: str = ""
    panel_suffixes: tuple[tuple[int, str], ...] = ()
    agency: str = ""
    review_notice: str = ""


@dataclass(frozen=True)
class RasterAffine:
    a: float
    d: float
    b: float
    e: float
    c: float
    f: float

    def pixel_center(self, column: float, row: float) -> EngineeringPoint:
        return EngineeringPoint(
            self.a * column + self.b * row + self.c,
            self.d * column + self.e * row + self.f,
        )


def engineering_to_pdf_points(point: EngineeringPoint, panel: DrawingPanel) -> EngineeringPoint:
    """Engineering coordinates to PDF page points for this panel."""
    x_in = LEFT + (point.x - panel.bounds.x_min) / panel.horizontal_scale
    y_in = BOTTOM + (point.y - panel.bounds.y_min) / panel.vertical_scale
    return EngineeringPoint(x_in * 72.0, y_in * 72.0)


def engineering_to_dxf_inches(point: EngineeringPoint, panel: DrawingPanel) -> EngineeringPoint:
    """Engineering coordinates to the existing paper-inch DXF model space."""
    return EngineeringPoint(
        panel.drawing_origin_x_in + LEFT + (point.x - panel.bounds.x_min) / panel.horizontal_scale,
        panel.drawing_origin_y_in + BOTTOM + (point.y - panel.bounds.y_min) / panel.vertical_scale,
    )


def raster_affine(bounds: EngineeringBounds, width: int, height: int) -> RasterAffine:
    """Pixel-center world transform for a plot-only engineering raster."""
    if width <= 0 or height <= 0:
        raise ValueError("Raster width and height must be positive integers.")
    x_pixel = (bounds.x_max - bounds.x_min) / width
    y_pixel = (bounds.y_max - bounds.y_min) / height
    if not all(math.isfinite(value) and value > 0 for value in (x_pixel, y_pixel)):
        raise ValueError("Raster engineering bounds must be finite and increasing.")
    return RasterAffine(x_pixel, 0.0, 0.0, -y_pixel,
                        bounds.x_min + x_pixel / 2.0,
                        bounds.y_max - y_pixel / 2.0)


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive.")
    return value


def _grid_values(lo: float, hi: float, minor: float, major: float, origin: float) -> tuple[tuple[float, bool], ...]:
    if not math.isclose(major / minor, round(major / minor), rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("Major grid intervals must be integer multiples of minor intervals.")
    first = math.ceil((lo - origin) / minor - 1e-12)
    last = math.floor((hi - origin) / minor + 1e-12)
    ratio = round(major / minor)
    return tuple((origin + index * minor, index % ratio == 0)
                 for index in range(first, last + 1))


def _panel_grid(panel: PanelDef, config: DrawingBuildConfig, station_origin: float) -> tuple[GridDefinition, tuple[GridLine, ...]]:
    station_minor = _positive(config.station_minor or panel.h_scale / 5.0, "Station minor grid interval")
    station_major = _positive(config.station_major or panel.h_scale, "Station major grid interval")
    elevation_minor = _positive(config.elevation_minor or panel.v_scale / 5.0, "Elevation minor grid interval")
    elevation_major = _positive(config.elevation_major or panel.v_scale, "Elevation major grid interval")
    grid = GridDefinition(station_origin, float(config.elevation_origin), station_minor,
                          station_major, elevation_minor, elevation_major)
    station_lines = tuple(GridLine("STATION", value, major)
                          for value, major in _grid_values(panel.start_station, panel.end_station,
                                                          station_minor, station_major, station_origin))
    elevation_lines = tuple(GridLine("ELEVATION", value, major)
                            for value, major in _grid_values(panel.start_elevation, panel.end_elevation,
                                                            elevation_minor, elevation_major,
                                                            config.elevation_origin))
    return grid, station_lines + elevation_lines


def _points(values: Iterable[tuple[float, float]]) -> tuple[EngineeringPoint, ...]:
    return tuple(EngineeringPoint(float(x), float(y)) for x, y in values)


def annotation_text_anchor(annotation: DrawingAnnotation) -> EngineeringPoint:
    return annotation.display_anchor or annotation.anchor


def _annotation_box(annotation: DrawingAnnotation, panel: PanelDef,
                    anchor: EngineeringPoint | None = None) -> tuple[float, float, float, float]:
    font_size = 9.0 if annotation.kind == "CROSS_SECTION" else 7.0
    width = max(font_size / 72.0, len(annotation.text) * font_size * 0.52 / 72.0)
    height = font_size / 72.0
    if abs(annotation.rotation_degrees) % 180 == 90:
        width, height = height, width
    point = anchor or annotation_text_anchor(annotation)
    center_x = (point.x - panel.start_station) / panel.h_scale
    center_y = (point.y - panel.start_elevation) / panel.v_scale
    return (center_x - width / 2, center_y - height / 2,
            center_x + width / 2, center_y + height / 2)


def _boxes_intersect(first: tuple[float, float, float, float],
                     second: tuple[float, float, float, float]) -> bool:
    return (first[0] < second[2] and second[0] < first[2]
            and first[1] < second[3] and second[1] < first[3])


def _annotation_offsets(kind: str) -> tuple[tuple[float, float], ...]:
    if kind == "STRUCTURE":
        return tuple((x, y) for y in (0.0, 0.16, 0.32)
                     for x in (0.0, 0.16, -0.16, 0.32, -0.32, 0.48, -0.48))
    if kind == "CROSS_SECTION":
        return tuple((0.0, y) for y in
                     (0.0, 0.16, -0.16, 0.32, -0.32, 0.48, -0.48, 0.64, -0.64))
    return tuple((x, y) for y in (0.0, 0.16, -0.16, 0.32, -0.32)
                 for x in (0.0, 0.16, -0.16, 0.32, -0.32))


def _place_annotations(annotations: list[DrawingAnnotation], panel: PanelDef,
                       polylines: list[DrawingPolyline]) -> list[DrawingAnnotation]:
    """Deterministically offset display text without changing engineering anchors."""
    plot_width = (panel.end_station - panel.start_station) / panel.h_scale
    plot_height = (panel.end_elevation - panel.start_elevation) / panel.v_scale
    edge_clearance = 0.02
    obstacles: list[tuple[float, float, float, float]] = []
    for polyline in polylines:
        if polyline.kind not in {"STRUCTURE_FACE", "CULVERT_OUTLINE"} or not polyline.points:
            continue
        xs = [(point.x - panel.start_station) / panel.h_scale for point in polyline.points]
        ys = [(point.y - panel.start_elevation) / panel.v_scale for point in polyline.points]
        padding = 0.025
        obstacles.append((min(xs) - padding, min(ys) - padding,
                          max(xs) + padding, max(ys) + padding))

    accepted: list[tuple[float, float, float, float]] = []
    placements: dict[str, DrawingAnnotation] = {}
    visible = [annotation for annotation in annotations if annotation.text]
    priority = {"STRUCTURE": 0, "LANDMARK": 1, "BACKWATER": 2, "CROSS_SECTION": 3}
    for annotation in sorted(visible, key=lambda item: (
            priority.get(item.kind, 4), item.anchor.x, item.anchor.y, item.identity)):
        chosen = None
        for dx_in, dy_in in _annotation_offsets(annotation.kind):
            candidate = EngineeringPoint(
                annotation.anchor.x + dx_in * panel.h_scale,
                annotation.anchor.y + dy_in * panel.v_scale)
            box = _annotation_box(annotation, panel, candidate)
            inside = (box[0] >= edge_clearance and box[1] >= edge_clearance
                      and box[2] <= plot_width - edge_clearance
                      and box[3] <= plot_height - edge_clearance)
            if not inside or any(_boxes_intersect(box, other) for other in accepted):
                continue
            if any(_boxes_intersect(box, obstacle) for obstacle in obstacles):
                continue
            chosen = (candidate, box, dx_in != 0.0 or dy_in != 0.0)
            break
        if chosen is None:
            candidate = annotation.anchor
            box = _annotation_box(annotation, panel, candidate)
            chosen = (candidate, box, False)
        candidate, box, moved = chosen
        placements[annotation.identity] = replace(
            annotation, display_anchor=candidate if moved else None,
            leader_required=moved)
        accepted.append(box)
    return [placements.get(annotation.identity, annotation) for annotation in annotations]


def _annotation_collision_warnings(annotations: list[DrawingAnnotation], panel: PanelDef) -> tuple[str, ...]:
    boxes = []
    for annotation in annotations:
        # CROSS_SECTION_LOCATION records deliberately carry an empty label and
        # render only a small station marker.  Treating the empty string as a
        # minimum-sized text box produced large numbers of false text-collision
        # warnings in dense models.
        if not annotation.text:
            continue
        box = _annotation_box(annotation, panel)
        boxes.append((annotation, *box))
    warnings = []
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if _boxes_intersect(first[1:], second[1:]):
                warnings.append(
                    f"Potential annotation collision in panel {panel.panel}: "
                    f"{first[0].identity} and {second[0].identity}.")
    return tuple(warnings)


def annotation_warnings_for_panel(
    warnings: tuple[str, ...] | list[str], panel_id: int
) -> tuple[str, ...]:
    """Return only collision evidence belonging to one rendered sheet."""
    marker = f"Potential annotation collision in panel {panel_id}:"
    return tuple(warning for warning in warnings if warning.startswith(marker))


def _clip_polyline(points: Iterable[tuple[float, float]], lo: float, hi: float) -> tuple[tuple[float, float], ...]:
    """Clip one already-continuous run without extrapolation."""
    source = tuple(points)
    if not source or lo >= hi:
        return ()
    if len(source) == 1:
        return source if lo <= source[0][0] <= hi else ()
    clipped: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(source, source[1:]):
        dx = x1 - x0
        if math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=1e-12):
            candidates = ((x0, y0), (x1, y1)) if lo <= x0 <= hi else ()
        else:
            first = max(0.0, min((lo - x0) / dx, (hi - x0) / dx))
            last = min(1.0, max((lo - x0) / dx, (hi - x0) / dx))
            if first > last:
                candidates = ()
            else:
                candidates = tuple((x0 + value * dx, y0 + value * (y1 - y0))
                                   for value in (first, last))
        for x, y in candidates:
            x = lo if math.isclose(x, lo, rel_tol=0.0, abs_tol=1e-9) else x
            x = hi if math.isclose(x, hi, rel_tol=0.0, abs_tol=1e-9) else x
            point = (x, y)
            if not clipped or not (math.isclose(clipped[-1][0], x, rel_tol=0.0, abs_tol=1e-12)
                                   and math.isclose(clipped[-1][1], y, rel_tol=0.0, abs_tol=1e-12)):
                clipped.append(point)
    return tuple(clipped)


def build_engineering_drawing(
    db,
    panels: list[PanelDef] | tuple[PanelDef, ...],
    profile_ids: list[str] | tuple[str, ...] | None = None,
    *,
    revision: tuple[float, float] | None = None,
    page_size: str | None = None,
    custom_page: tuple[float, float] | None = None,
    config: DrawingBuildConfig | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> EngineeringDrawingDefinition:
    """Build one immutable definition after the existing authoritative preflight."""
    config = config or DrawingBuildConfig()
    cancel_check = cancel_check or (lambda: None)
    cancel_check()
    revision = revision or require_revision(db)
    selected = resolve_profiles(db.profiles, list(profile_ids)) if profile_ids else (
        [profile for profile in db.profiles if profile.selected] or list(db.profiles[:1]))
    page_width, page_height = page_dimensions(page_size or db.header.page_size, custom_page)
    validate_export(db, list(panels), selected, (page_width, page_height), revision)

    configured_suffixes = dict(config.panel_suffixes)
    if len(configured_suffixes) != len(config.panel_suffixes):
        raise ValueError("Panel suffix configuration contains duplicate panel identities.")
    suffixes = {panel_id: normalize_panel_suffix(suffix)
                for panel_id, suffix in configured_suffixes.items()}
    configured_suffix = normalize_panel_suffix(config.panel_suffix)
    station_origin = float(db.stationing_start if config.station_origin is None else config.station_origin)
    if not math.isfinite(station_origin) or not math.isfinite(config.elevation_origin):
        raise ValueError("Grid origins must be finite.")
    xs = [row for row in db.working if row.kind == "xs"]
    reviewed_structure_names = {
        row.structure_model.identity: row.label_text.strip()
        for row in db.working
        if (row.kind == "struct" and row.structure_model is not None
            and row.label_text and row.label_text.strip())
    }
    drawing_panels = []
    all_annotation_warnings = []
    for panel in panels:
        cancel_check()
        clip_start = max(panel.start_station, revision[0])
        clip_end = min(panel.end_station, revision[1])
        bounds = EngineeringBounds(panel.start_station, panel.end_station,
                                   panel.start_elevation, panel.end_elevation)
        clip_bounds = EngineeringBounds(clip_start, clip_end,
                                        panel.start_elevation, panel.end_elevation)
        grid, grid_lines = _panel_grid(panel, config, station_origin)
        polylines: list[DrawingPolyline] = []
        for index, run in enumerate(segments([(row.cumdist, row.minchel) for row in xs])):
            clipped = _clip_polyline(run, clip_start, clip_end)
            if len(clipped) >= 2:
                polylines.append(DrawingPolyline(
                    f"stream-bed-{panel.panel}-{index}", "STREAM_BED", "STREAM_BED", _points(clipped)))
        annotations: list[DrawingAnnotation] = []
        for profile in selected:
            cancel_check()
            for index, run in enumerate(segments(
                    [(row.cumdist, row.profiles.get(profile.profile_id)) for row in xs])):
                clipped = _clip_polyline(run, clip_start, clip_end)
                if len(clipped) >= 2:
                    polylines.append(DrawingPolyline(
                        f"profile-{profile.profile_id}-{panel.panel}-{index}", "PROFILE",
                        profile.layer or f"PROFILE_{profile.profile_id}", _points(clipped),
                        profile.profile_id, profile.color, profile.linetype,
                        profile.lineweight, profile.result_type))
        structure_faces = export_faces(db.working, clip_start, clip_end)
        for face in structure_faces:
            cancel_check()
            structure_layer = "STRUCT_BRIDGE" if str(face.struc).startswith("BRIDGE-") else "STRUCT_CULVERT"
            polylines.append(DrawingPolyline(
                f"structure-{face.structure_id}-{face.opening_id}-{face.struc}", "STRUCTURE_FACE", structure_layer,
                _points(((face.cumdist, face.minchel), (face.cumdist, face.high_chord))),
                provenance="VALIDATED_STRUCTURE_FACE"))
        faces_by_structure: dict[tuple[str,str], dict[str, object]] = {}
        for face in structure_faces:
            face_name = str(face.struc).rsplit("-", 1)[-1]
            faces_by_structure.setdefault((face.structure_id,face.opening_id), {})[face_name] = face
        for (structure_id,opening_id), structure in faces_by_structure.items():
            upstream = structure.get("UP")
            downstream = structure.get("DN")
            ordered_faces = [face for face in (downstream, upstream) if face is not None]
            if not ordered_faces:
                ordered_faces = list(structure.values())
            reviewer_name = reviewed_structure_names.get(structure_id)
            source_name = next((face.opening_name for face in ordered_faces
                                if face.opening_name), "")
            first_face = ordered_faces[0]
            opening_label = (reviewer_name or source_name
                             or f"{first_face.barrels}x {first_face.shape}")
            structure_type = str(first_face.struc).rsplit("-", 1)[0]
            label_provenance = (
                "USER_ENTERED_STRUCTURE_NAME_WITH_VALIDATED_SOURCE_GEOMETRY"
                if reviewer_name else "VALIDATED_STRUCTURE_FACE")
            annotations.append(DrawingAnnotation(
                f"structure-label-{structure_id}-{opening_id}", "STRUCTURE",
                f"{structure_type}: {opening_label}",
                EngineeringPoint(
                    sum(float(face.cumdist) for face in ordered_faces) / len(ordered_faces),
                    max(float(face.high_chord) for face in ordered_faces)),
                90.0, label_provenance, "REVIEWED"))
            if upstream is None or downstream is None:
                continue
            if not str(upstream.struc).startswith(("CULVERT-", "MULTIPLE_OPENING-")):
                continue
            # Source rise establishes the longitudinal soffit envelope. This is
            # not a cross-sectional rendering of the retained opening shape.
            outline = (
                (downstream.cumdist, downstream.minchel),
                (upstream.cumdist, upstream.minchel),
                (upstream.cumdist, upstream.low_chord),
                (downstream.cumdist, downstream.low_chord),
            )
            if all(math.isfinite(float(value)) for point in outline for value in point):
                polylines.append(DrawingPolyline(
                    f"culvert-outline-{structure_id}-{opening_id}-{panel.panel}",
                    "CULVERT_OUTLINE", "STRUCT_CULVERT_OUTLINE", _points(outline),
                    lineweight=0.45, provenance="VALIDATED_SOURCE_OPENING_LONGITUDINAL_ENVELOPE",
                    closed=True))

        for row in xs:
            cancel_check()
            if not clip_start <= row.cumdist <= clip_end:
                continue
            y = row.profiles.get(selected[0].profile_id) if selected else None
            y = row.minchel if y is None else y
            if y is not None:
                annotations.append(DrawingAnnotation(
                    row.row_identity or f"xs-{row.id}",
                    "CROSS_SECTION" if row.label_letter else "CROSS_SECTION_LOCATION",
                    row.label_letter or "",
                    EngineeringPoint(row.cumdist, y), 0.0,
                    "WORKING_TABLE_REVIEWED", "REVIEWED"))
        for landmark in db.landmarks:
            if clip_start <= landmark.distance <= clip_end:
                annotations.append(DrawingAnnotation(
                    f"landmark-{landmark.id}", "LANDMARK", landmark.text,
                    EngineeringPoint(landmark.distance, landmark.elevation),
                    90.0 if landmark.orientation.lower().startswith("v") else 0.0,
                    "USER_ENTERED", "REVIEWED" if landmark.adjusted else "UNREVIEWED"))
        for profile in selected:
            if profile.backwater_elev is not None:
                annotations.append(DrawingAnnotation(
                    f"backwater-{profile.profile_id}", "BACKWATER",
                    "User-entered backwater reference (extent not calculated): "
                    + profile.backwater_text,
                    EngineeringPoint(panel.start_station, profile.backwater_elev), 0.0,
                    profile.backwater_provenance, "USER_ENTERED"))
                break
        annotations = _place_annotations(annotations, panel, polylines)
        all_annotation_warnings.extend(_annotation_collision_warnings(annotations, panel))
        suffix = suffixes.get(panel.panel, configured_suffix or panel.panel_suffix)
        drawing_panels.append(DrawingPanel(
            panel.panel, suffix, f"{panel.panel}{suffix}", bounds, clip_bounds,
            panel.h_scale, panel.v_scale, panel.panel_x or 0.0, panel.panel_y or 0.0,
            grid, grid_lines, tuple(polylines), tuple(annotations)))

    profiles = tuple(DrawingProfile(
        profile.profile_id, profile.source_name or profile.name,
        profile.display_name or profile.name, profile_label(profile, db.working),
        profile.layer or f"PROFILE_{profile.profile_id}", profile.color,
        profile.linetype, profile.lineweight) for profile in selected)
    legend_entries = [DrawingLegendEntry(
        "stream-bed", "Stream bed", "#000000", "CONTINUOUS", 0.9, "STREAM_BED")]
    legend_entries.extend(DrawingLegendEntry(
        profile.profile_id, profile.legend_text, profile.color,
        profile.linetype, profile.lineweight, "PROFILE") for profile in profiles)
    if any(polyline.kind == "CULVERT_OUTLINE" for panel in drawing_panels for polyline in panel.polylines):
        legend_entries.append(DrawingLegendEntry(
            "verified-culvert-outline", "Verified culvert opening outline",
            "#000000", "CONTINUOUS", 0.45, "CULVERT_OUTLINE"))
    unavailable_footnotes = tuple(
        f"{profile.legend_text}: hydraulic values are missing at one or more cross sections; profile gaps are shown."
        for profile in profiles
        if any(revision[0] <= row.cumdist <= revision[1]
               and row.profiles.get(profile.profile_id) is None for row in xs))
    source = db.flooding_source or {}
    warnings = list(all_annotation_warnings)
    if not db.header.vertical_datum or db.header.vertical_datum.upper() == "UNKNOWN":
        warnings.append("Vertical datum UNKNOWN; confirm before authoritative use.")
    datum = db.header.vertical_datum or "UNKNOWN"
    distance_title = f"Distance ({db.header.unit})"
    if db.header.stream_distance_label:
        distance_title += f" above {db.header.stream_distance_label}"
    page_title = "FLOOD PROFILES"
    if config.review_notice.strip():
        page_title = f"{config.review_notice.strip()} — {page_title}"
        warnings.append(config.review_notice.strip())
    presentation = DrawingPresentation(
        page_title,
        distance_title,
        f"Elevation ({datum})",
        f"VERTICAL DATUM: {datum.upper()}",
        tuple(legend_entries), unavailable_footnotes, tuple(all_annotation_warnings))
    return EngineeringDrawingDefinition(
        engineering_version(db),
        DrawingIdentity(db.header.stream_name, source.get("display_name", ""),
                        db.header.community, db.header.county, db.header.state, config.agency),
        PageLayout(page_width, page_height), revision[0], revision[1],
        db.source_units, db.header.unit, db.header.vertical_datum,
        db.header.datum_provenance, profiles, presentation, tuple(drawing_panels), tuple(warnings))
