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


def sheet_capacity(panel, page_size="11x17", custom_page=None):
    """Return the physical sheet/plot contract for one engineering panel."""
    page_width, page_height = page_dimensions(page_size, custom_page)
    values = [panel.start_station, panel.end_station, panel.start_elevation,
              panel.end_elevation, panel.h_scale, panel.v_scale]
    if any(not math.isfinite(v) for v in values) or panel.h_scale <= 0 or panel.v_scale <= 0:
        raise ExportGeometryError(f"Panel {panel.panel}: bounds and positive engineering scales must be finite.")
    station_span = panel.end_station - panel.start_station
    elevation_span = panel.end_elevation - panel.start_elevation
    if station_span <= 0 or elevation_span <= 0:
        raise ExportGeometryError(f"Panel {panel.panel}: station and elevation spans must be positive.")
    usable_width = page_width - LEFT - RIGHT
    usable_height = page_height - BOTTOM - TOP
    if usable_width <= 0 or usable_height <= 0:
        raise ExportGeometryError("Reserved drawing regions leave no usable engineering plot area.")
    required_width = station_span / panel.h_scale
    required_height = elevation_span / panel.v_scale
    horizontal_overflow = max(0.0, required_width - usable_width)
    vertical_overflow = max(0.0, required_height - usable_height)
    return {
        "page_size": page_size if custom_page is None else "CUSTOM",
        "paper_width_inches": page_width,
        "paper_height_inches": page_height,
        "plot_left_inches": LEFT,
        "plot_right_inches": RIGHT,
        "plot_bottom_inches": BOTTOM,
        "plot_top_inches": TOP,
        "usable_plot_width_inches": usable_width,
        "usable_plot_height_inches": usable_height,
        "horizontal_station_span": station_span,
        "vertical_elevation_span": elevation_span,
        "horizontal_scale": panel.h_scale,
        "vertical_scale": panel.v_scale,
        "required_plot_width_inches": required_width,
        "required_plot_height_inches": required_height,
        "remaining_horizontal_clearance_inches": usable_width - required_width,
        "remaining_vertical_clearance_inches": usable_height - required_height,
        "horizontal_overflow_inches": horizontal_overflow,
        "vertical_overflow_inches": vertical_overflow,
        "physically_valid": horizontal_overflow <= 1e-9 and vertical_overflow <= 1e-9,
    }


def plot_dimensions(panel, page):
    capacity = sheet_capacity(panel, custom_page=page)
    width = capacity["required_plot_width_inches"]
    height = capacity["required_plot_height_inches"]
    if not capacity["physically_valid"]:
        raise ExportGeometryError(f"Panel {panel.panel}: plotting area requires {width:g} x {height:g} inches at the saved scales. Select a page at least {width+LEFT+RIGHT:g} x {height+BOTTOM+TOP:g} inches or explicitly revise Panel Setup.")
    for origin in (panel.panel_x, panel.panel_y):
        if origin is not None and not math.isfinite(origin):
            raise ExportGeometryError(f"Panel {panel.panel}: drawing origins must be finite inches.")
    return width, height


def analyze_panel_content(db, panels, selected=None, page_size=None,
                          custom_page=None, revision=None, rounding_increment=5.0):
    """Compare saved/draft panel elevations with source-backed drawing content.

    This is read-only proposal evidence. It never mutates panel geometry or
    review state. The same clipping and structure-face sources used by export
    are used here so a proposal cannot silently omit exportable content.
    """
    from .dxf_export import clip_station_elevation_polyline
    from .structure_model import export_faces
    from .state_contract import require_stationing, validate_collection, panels_stale
    require_stationing(db)
    if not math.isfinite(float(rounding_increment)) or float(rounding_increment) <= 0:
        raise ExportGeometryError("Panel-fit rounding increment must be a finite positive number.")
    increment = float(rounding_increment)
    selected = list(selected if selected is not None else
                    ([p for p in db.profiles if p.selected] or db.profiles[:1]))
    page = page_dimensions(page_size or db.header.page_size, custom_page)
    validate_collection(panels)
    xs = [row for row in db.working if row.kind == "xs"]
    results = []
    for panel in panels:
        plot_width, plot_height = plot_dimensions(panel, page)
        capacity_analysis = sheet_capacity(
            panel, page_size=page_size or db.header.page_size,
            custom_page=custom_page)
        lo, hi = panel.start_station, panel.end_station
        if revision is not None:
            lo, hi = max(lo, revision[0]), min(hi, revision[1])
        if lo >= hi:
            raise ExportGeometryError(f"Panel {panel.panel}: no overlap with Revision Limits.")
        contributors = {}

        def add(kind, identity, label, value, station=None, provenance=""):
            if value is None:
                return
            value = float(value)
            if not math.isfinite(value):
                raise ExportGeometryError(
                    f"Panel {panel.panel}: nonfinite {label} content cannot be analyzed.")
            key = (kind, identity)
            item = contributors.setdefault(key, {
                "kind": kind, "identity": identity, "label": label,
                "minimum_elevation": value, "maximum_elevation": value,
                "stations": [], "provenance": provenance,
            })
            item["minimum_elevation"] = min(item["minimum_elevation"], value)
            item["maximum_elevation"] = max(item["maximum_elevation"], value)
            if station is not None and math.isfinite(float(station)):
                item["stations"].append(float(station))

        datasets = [("GROUND", "ground", "Ground profile",
                     [(row.cumdist, row.minchel) for row in xs], "WORKING_GROUND")]
        for profile in selected:
            datasets.append(("WSEL", profile.profile_id,
                profile.legend_text or profile.display_name or profile.name,
                [(row.cumdist, row.profiles.get(profile.profile_id)) for row in xs],
                "WORKING_WSEL_WITH_ADJUSTMENT_PROVENANCE"))
        for kind, identity, label, points, provenance in datasets:
            for run in segments(points):
                for station, elevation in clip_station_elevation_polyline(run, lo, hi):
                    add(kind, identity, label, elevation, station, provenance)
            for station, elevation in points:
                if elevation is not None and lo <= station <= hi:
                    add(kind, identity, label, elevation, station, provenance)
        try:
            structures = export_faces(db.working, lo, hi)
        except ValueError as exc:
            raise ExportGeometryError(str(exc)) from exc
        for face in structures:
            label = f"{face.river} / {face.reach} / RS {face.rs} / {face.struc}"
            for field, value in (("invert", face.minchel),
                                 ("low chord", face.low_chord),
                                 ("high chord", face.high_chord)):
                add("STRUCTURE", f"{face.structure_id}:{face.struc}:{field}",
                    f"{label} {field}", value, face.cumdist,
                    "VALIDATED_SOURCE_STRUCTURE_GEOMETRY")
        for landmark in db.landmarks:
            if lo <= landmark.distance <= hi:
                add("ANNOTATION", f"landmark:{landmark.id}",
                    f"Landmark {landmark.id}", landmark.elevation,
                    landmark.distance, "USER_ENTERED")
        for profile in selected:
            if profile.backwater_elev is not None:
                add("ANNOTATION", f"backwater:{profile.profile_id}",
                    f"Backwater reference: {profile.legend_text or profile.name}",
                    profile.backwater_elev, lo, profile.backwater_provenance)

        values = [value for item in contributors.values()
                  for value in (item["minimum_elevation"], item["maximum_elevation"])]
        if not values:
            raise ExportGeometryError(f"Panel {panel.panel}: no finite engineering content is available.")
        required_min, required_max = min(values), max(values)
        suggested_min = math.floor(required_min / increment) * increment
        suggested_max = math.ceil(required_max / increment) * increment
        if suggested_max <= suggested_min:
            suggested_max = suggested_min + increment
        capacity = capacity_analysis["usable_plot_height_inches"] * panel.v_scale
        suggested_span = suggested_max - suggested_min
        required_span = required_max - required_min
        suggested_height_inches = suggested_span / panel.v_scale
        suggested_vertical_clearance = (
            capacity_analysis["usable_plot_height_inches"] - suggested_height_inches)
        outliers = []
        for item in contributors.values():
            below = item["minimum_elevation"] < panel.start_elevation - 1e-8
            above = item["maximum_elevation"] > panel.end_elevation + 1e-8
            item["outside_existing_range"] = below or above
            item["below_by"] = max(0.0, panel.start_elevation-item["minimum_elevation"])
            item["above_by"] = max(0.0, item["maximum_elevation"]-panel.end_elevation)
            item["stations"] = sorted(set(item["stations"]))
            if item["outside_existing_range"]:
                outliers.append(item)
        requires_scale = (suggested_span > capacity + 1e-8
                          or not capacity_analysis["physically_valid"])
        results.append({
            "panel": panel.panel, "panel_suffix": panel.panel_suffix,
            "display_id": panel.display_id,
            "existing_minimum_elevation": panel.start_elevation,
            "existing_maximum_elevation": panel.end_elevation,
            "required_minimum_elevation": required_min,
            "required_maximum_elevation": required_max,
            "missing_lower_clearance": max(0.0, panel.start_elevation-required_min),
            "missing_upper_clearance": max(0.0, required_max-panel.end_elevation),
            "suggested_minimum_elevation": suggested_min,
            "suggested_maximum_elevation": suggested_max,
            "rounding_increment": increment,
            "existing_panel_valid": not outliers,
            "responsible_content": outliers,
            "all_content": list(contributors.values()),
            "saved_vertical_scale": panel.v_scale,
            "saved_horizontal_scale": panel.h_scale,
            "page_size": page_size or db.header.page_size,
            "sheet_capacity": capacity_analysis,
            "usable_plot_width_inches": capacity_analysis["usable_plot_width_inches"],
            "usable_plot_height_inches": capacity_analysis["usable_plot_height_inches"],
            "physical_vertical_capacity": capacity,
            "required_content_span": required_span,
            "suggested_span": suggested_span,
            "suggested_required_plot_height_inches": suggested_height_inches,
            "suggested_vertical_clearance_inches": suggested_vertical_clearance,
            "suggested_physically_valid": (
                capacity_analysis["required_plot_width_inches"]
                <= capacity_analysis["usable_plot_width_inches"] + 1e-8
                and suggested_height_inches
                <= capacity_analysis["usable_plot_height_inches"] + 1e-8),
            "requires_scale_or_page_change": requires_scale,
            "minimum_vertical_scale_for_suggestion": (
                suggested_span/(page[1]-BOTTOM-TOP) if requires_scale else panel.v_scale),
            "plot_width_inches": plot_width,
            "plot_height_inches": plot_height,
        })
    return {
        "panels": results,
        "all_panels_valid": all(item["existing_panel_valid"] for item in results),
        "panels_stale": panels_stale(db),
        "proposal_only": True,
        "message": "Suggested limits are proposals only. Review and Apply are required; scales and page size are never changed automatically.",
    }


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
