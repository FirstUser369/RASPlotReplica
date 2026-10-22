"""Shared physical-inch contract for public PDF and DXF profile exports."""
import math
from .hydraulics import segments, hydraulic_preflight
from .rasplot_db import PAGE_SIZES

LEFT, BOTTOM, RIGHT, TOP = 1.75, 1.0, 2.25, 1.0
DXF_VERSIONS = {"R2013": "AC1027", "R2010": "AC1024", "R2007": "AC1021", "R2000": "AC1015"}


class ExportGeometryError(ValueError):
    pass


def validate_scales(h_scale, v_scale):
    if any(not math.isfinite(v) or v <= 0 for v in (h_scale,v_scale)):
        raise ExportGeometryError("Horizontal and vertical scales must be finite positive feet per inch.")


def page_dimensions(page_size, custom_page=None):
    if custom_page is None and page_size not in PAGE_SIZES:
        raise ExportGeometryError(f"Unknown page size: {page_size}")
    dimensions = custom_page if custom_page is not None else PAGE_SIZES[page_size]
    if len(dimensions) != 2 or any(not math.isfinite(v) or v <= 0 for v in dimensions):
        raise ExportGeometryError("Page width and height must be finite positive inches.")
    return dimensions


def plot_dimensions(panel, page):
    values = [panel.start_station, panel.end_station, panel.start_elevation,
              panel.end_elevation, panel.h_scale, panel.v_scale]
    if any(not math.isfinite(v) for v in values) or panel.h_scale <= 0 or panel.v_scale <= 0:
        raise ExportGeometryError(f"Panel {panel.panel}: bounds and positive engineering scales must be finite.")
    width = (panel.end_station-panel.start_station)/panel.h_scale
    height = (panel.end_elevation-panel.start_elevation)/panel.v_scale
    if width <= 0 or height <= 0:
        raise ExportGeometryError(f"Panel {panel.panel}: station and elevation spans must be positive.")
    if width+LEFT+RIGHT > page[0]+1e-9 or height+BOTTOM+TOP > page[1]+1e-9:
        raise ExportGeometryError(f"Panel {panel.panel}: plotting area requires {width:g} x {height:g} inches at the saved scales. Select a page at least {width+LEFT+RIGHT:g} x {height+BOTTOM+TOP:g} inches or explicitly revise Panel Setup.")
    for origin in (panel.panel_x, panel.panel_y):
        if origin is not None and not math.isfinite(origin):
            raise ExportGeometryError(f"Panel {panel.panel}: drawing origins must be finite inches.")
    return width, height


def validate_export(db, panels, selected, page, revision=None):
    from .dxf_export import clip_station_elevation_polyline
    from .structure_model import export_faces
    from .state_contract import require_stationing, validate_collection, panels_stale
    require_stationing(db)
    from .rasplot_db import model_station_extent
    saved_subset = (
        bool(db.panels)
        and len(panels) < len(db.panels)
        and all(panel in db.panels for panel in panels)
    )
    required_extent = None if saved_subset else (revision or model_station_extent(db.working))
    validate_collection(panels, required_extent)
    if any(p in db.panels for p in panels) and panels_stale(db):
        raise ExportGeometryError("Saved panels need review after engineering changes. Review and Apply in Panel Setup.")
    xs = [r for r in db.working if r.kind == "xs"]
    for panel in panels:
        plot_dimensions(panel, page)
        lo, hi = panel.start_station, panel.end_station
        if revision is not None:
            if len(revision) != 2 or any(not math.isfinite(v) for v in revision) or revision[0] >= revision[1]:
                raise ExportGeometryError("Revision Limits must be finite and increasing.")
            lo, hi = max(lo, revision[0]), min(hi, revision[1])
        if lo >= hi:
            raise ExportGeometryError(f"Panel {panel.panel}: no overlap with Revision Limits.")
        hydraulic=hydraulic_preflight(db,selected,lo,hi)
        if hydraulic["errors"]:
            raise ExportGeometryError("Hydraulic export blocked: "+"; ".join(hydraulic["errors"]))
        data = [("stream bed", [(r.cumdist, r.minchel) for r in xs])]
        data += [(p.name, [(r.cumdist,r.profiles.get(p.profile_id)) for r in xs]) for p in selected]
        elevations = []
        for name, points in data:
            if any(not math.isfinite(x) or y is not None and not math.isfinite(y) for x,y in points):
                raise ExportGeometryError(f"Panel {panel.panel}: nonfinite {name} data cannot be exported.")
            for run in segments(points):
                elevations.extend((name,y) for x,y in clip_station_elevation_polyline(run,lo,hi))
            elevations.extend((name,y) for x,y in points if y is not None and lo <= x <= hi)
        try:
            structures = export_faces(db.working,lo,hi)
        except ValueError as exc:
            raise ExportGeometryError(str(exc)) from exc
        for r in structures:
            elevations.extend((r.struc, v) for v in (r.minchel,r.low_chord,r.high_chord) if v is not None)
        elevations.extend(("landmark",lm.elevation) for lm in db.landmarks if lo <= lm.distance <= hi)
        for p in selected:
            if p.backwater_elev is not None:
                elevations.append(("backwater",p.backwater_elev))
                break
        for name, value in elevations:
            if not math.isfinite(value) or value < panel.start_elevation-1e-8 or value > panel.end_elevation+1e-8:
                raise ExportGeometryError(f"Panel {panel.panel}: {name} elevation {value:g} is outside saved range {panel.start_elevation:g}?{panel.end_elevation:g}. Adjust the elevation range in Panel Setup; export will not silently clip or change scale.")
