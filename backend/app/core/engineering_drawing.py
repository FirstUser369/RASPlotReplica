"""Immutable, renderer-neutral engineering drawing contract.

All coordinates stored here are engineering station/elevation values. Renderers
must apply an explicit target transform rather than changing source geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

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


def _annotation_collision_warnings(annotations: list[DrawingAnnotation], panel: PanelDef) -> tuple[str, ...]:
    boxes = []
    for annotation in annotations:
        font_size = 9.0 if annotation.kind == "CROSS_SECTION" else 7.0
        width = max(font_size / 72.0, len(annotation.text) * font_size * 0.52 / 72.0)
        height = font_size / 72.0
        if abs(annotation.rotation_degrees) % 180 == 90:
            width, height = height, width
        center_x = (annotation.anchor.x - panel.start_station) / panel.h_scale
        center_y = (annotation.anchor.y - panel.start_elevation) / panel.v_scale
        boxes.append((annotation, center_x - width / 2, center_y - height / 2,
                      center_x + width / 2, center_y + height / 2))
    warnings = []
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            if (first[1] < second[3] and second[1] < first[3]
                    and first[2] < second[4] and second[2] < first[4]):
                warnings.append(
                    f"Potential annotation collision in panel {panel.panel}: "
                    f"{first[0].identity} and {second[0].identity}.")
    return tuple(warnings)


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
) -> EngineeringDrawingDefinition:
    """Build one immutable definition after the existing authoritative preflight."""
    config = config or DrawingBuildConfig()
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
    drawing_panels = []
    all_annotation_warnings = []
    for panel in panels:
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
            polylines.append(DrawingPolyline(
                f"structure-{face.structure_id}-{face.struc}", "STRUCTURE_FACE", "STRUCTURE",
                _points(((face.cumdist, face.minchel), (face.cumdist, face.high_chord))),
                provenance="VALIDATED_STRUCTURE_FACE"))
            annotations.append(DrawingAnnotation(
                f"structure-label-{face.structure_id}-{face.struc}", "STRUCTURE",
                face.struc, EngineeringPoint(face.cumdist, face.high_chord), 90.0,
                "VALIDATED_STRUCTURE_FACE", "REVIEWED"))

        faces_by_structure: dict[str, dict[str, object]] = {}
        for face in structure_faces:
            face_name = str(face.struc).rsplit("-", 1)[-1]
            faces_by_structure.setdefault(face.structure_id, {})[face_name] = face
        for structure_id, structure in faces_by_structure.items():
            upstream = structure.get("UP")
            downstream = structure.get("DN")
            if upstream is None or downstream is None:
                continue
            # The validated single-box rule establishes both face stations,
            # inverts and soffits. This is a longitudinal opening outline only;
            # it does not claim barrel orientation or cross-sectional shape.
            outline = (
                (downstream.cumdist, downstream.minchel),
                (upstream.cumdist, upstream.minchel),
                (upstream.cumdist, upstream.low_chord),
                (downstream.cumdist, downstream.low_chord),
            )
            if all(math.isfinite(float(value)) for point in outline for value in point):
                polylines.append(DrawingPolyline(
                    f"culvert-outline-{structure_id}-{panel.panel}",
                    "CULVERT_OUTLINE", "STRUCT_CULVERT_OUTLINE", _points(outline),
                    lineweight=0.45, provenance="VALIDATED_SINGLE_BOX_LONGITUDINAL_RULE",
                    closed=True))

        for row in xs:
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
    presentation = DrawingPresentation(
        "FLOOD PROFILES",
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
