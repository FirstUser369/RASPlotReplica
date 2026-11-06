"""Source-backed structure normalization, placement and export preflight.

Validated culvert groups are represented as longitudinal opening envelopes.  A
group's transverse shape and barrel arrangement remain explicit metadata; no
cross-sectional shape is manufactured on the longitudinal profile.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import math
from pathlib import Path

TOLERANCE_FT = 0.01


class HydraulicKind(str, Enum):
    EXTRACTED = "EXTRACTED"
    INTERPOLATED = "INTERPOLATED"
    COPIED_XS = "COPIED_XS"
    DISPLAY_ONLY = "DISPLAY_ONLY"
    MISSING = "MISSING"


@dataclass
class HydraulicValue:
    value: float | None = None
    kind: str = HydraulicKind.MISSING.value
    source: str = "No structure-face/opening hydraulic result adapter is validated."


@dataclass
class StructureModel:
    identity: str
    river: str
    reach: str
    rs: str
    type: str
    source: dict
    upstream: tuple[str, str, str] | None = None
    downstream: tuple[str, str, str] | None = None
    receiving_connection: dict = field(default_factory=dict)
    openings: list[dict] = field(default_factory=list)
    text_geometry: dict | None = None
    status: str = "UNSUPPORTED"
    reason: str = ""
    anchor: float | None = None
    local_profile_extent: tuple[float,float] | None = None
    bounds: tuple[float, float] | None = None  # proven containing interval, not guessed faces
    occupied_interval: tuple[float, float] | None = None
    upstream_gap: float | None = None
    occupied_distance: float | None = None
    downstream_residual: float | None = None
    partition_valid: bool = False
    faces: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    hydraulic_values: dict = field(default_factory=dict)
    capability_id: str = "UNRESOLVED"
    capability_level: str = "INVENTORY_ONLY"
    missing_source_fields: list[str] = field(default_factory=list)
    affected_workflows: list[str] = field(default_factory=list)
    capability_evidence: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def kind_of(source):
    label = source.type.lower()
    raw = source.source_attributes
    if "gate" in label or any((finite(raw.get(k)) or 0) > 0 for k in ("Gates", "Gate Groups", "Culverts Flap Gates")):
        return "GATED"
    if "multiple" in label or len(source.openings) > 1:
        return "MULTIPLE_OPENING"
    for token, kind in [("lateral", "LATERAL"), ("inline", "INLINE"), ("culvert", "CULVERT"), ("bridge", "BRIDGE"), ("weir", "WEIR")]:
        if token in label:
            return kind
    return "UNKNOWN"


def connection(source, side):
    raw = source.source_attributes
    rs = source.us_rs if side == "US" else source.ds_rs
    if not rs or raw.get(f"{side} Type", "XS") != "XS":
        return None
    river = raw.get(f"{side} River", source.river)
    reach = raw.get(f"{side} Reach", source.reach)
    return (river, reach, rs) if river and reach else None


def linked_text(plan):
    """Only the exact plan-linked text file; retain duplicate candidates."""
    import h5py
    from .hdf_reader import _s
    from .engineering_units import EngineeringUnitContract, UnitContractError
    from .geometry_text_reader import parse_geometry_text_structures
    path = getattr(plan, "plan_path", None)
    if path is None or not Path(path).is_file():
        return {}
    try:
        with h5py.File(path, "r") as f:
            info = f.get("Plan Data/Plan Information")
            name = _s(info.attrs.get("Geometry Filename", "")) if info is not None else ""
    except OSError:
        return {}
    if not name:
        return {}
    text_path = Path(name)
    if not text_path.is_absolute():
        text_path = Path(path).parent / text_path
    if text_path.suffix.lower() == ".hdf":
        text_path = text_path.with_suffix("")
    result = {}
    try:
        contract = EngineeringUnitContract.from_source_system(getattr(plan, "source_units", "UNKNOWN"))
    except UnitContractError:
        return {}
    for entry in parse_geometry_text_structures(text_path):
        raw = asdict(entry)
        converted = dict(raw)
        converted["source_engineering"] = {
            "unit": contract.source_length_unit.value,
            "deck_up": raw["deck_up"], "deck_dn": raw["deck_dn"],
            "low_chord_up": raw["low_chord_up"],
            "low_chord_dn": raw["low_chord_dn"],
            "culvert_groups": raw["culvert_groups"],
        }
        for profile_key in ("deck_up", "deck_dn", "low_chord_up", "low_chord_dn"):
            converted[profile_key] = list(contract.source_points_to_working(raw[profile_key]))
        for key in ("rise", "length", "us_distance", "invert_us", "invert_ds",
                    "barrel_us_station", "barrel_ds_station"):
            if raw.get(key) is not None:
                converted[key] = contract.source_to_working(raw[key])
        converted_groups = []
        for group in raw["culvert_groups"]:
            item = dict(group)
            for key in ("rise", "span", "length", "invert_us", "invert_ds", "us_distance"):
                if item.get(key) is not None:
                    item[key] = contract.source_to_working(item[key])
            item["barrel_stations"] = [
                (contract.source_to_working(us), contract.source_to_working(ds))
                for us, ds in item.get("barrel_stations", [])
                if us is not None and ds is not None
            ]
            converted_groups.append(item)
        converted["culvert_groups"] = converted_groups
        result.setdefault((entry.river, entry.reach, entry.rs), []).append(converted)
    return result


def normalize(sources, xs_rows, profile_names, text=None):
    from .hdf_reader import source_value
    models = []
    text = text or {}
    for ordinal, source in enumerate(sources):
        identity = json.dumps([source.river, source.reach, source.rs, source.type,
                               source.source_id or "provided-structure", ordinal], ensure_ascii=False)
        model = StructureModel(identity, source.river, source.reach, source.rs,
                               kind_of(source), source_value(asdict(source)))
        model.upstream = connection(source, "US")
        model.downstream = connection(source, "DS")
        model.receiving_connection = {k: v for k, v in source.source_attributes.items() if k.startswith("DS ")}
        model.openings = [{**source_value(g),"identity":json.dumps([identity,g.get("source_id",i)])}
                          for i,g in enumerate(source.openings)]
        local_stations = [finite(point[0]) for point in source.profile_data if point]
        if local_stations and all(v is not None for v in local_stations):
            model.local_profile_extent = (min(local_stations),max(local_stations))
        candidates = text.get((source.river, source.reach, source.rs), [])
        model.text_geometry = candidates[0] if len(candidates) == 1 else None
        model.provenance = {
            "identity": {"kind": "SOURCE", "path": source.source_path, "record": source.source_id or ordinal},
            "connections": {"kind": "SOURCE", "fields": "US/DS Type, River, Reach, RS, SA/2D"},
            "source_profiles": {"kind": "SOURCE", "note": "Cross-structure station/crest arrays are retained; they are not assumed to be main-channel CUMDIST."},
            "legacy_chord_summaries": {"kind":"UNVALIDATED_DERIVATION", "note":"Source adapter chord summaries are retained for diagnostics only; approved culvert elevations come from identified group/text inputs."},
            "hydraulics": {"kind": "MISSING", "note": "Ordinary XS WSEL is never copied to structure faces."},
        }
        model.hydraulic_values = {face: {name: asdict(HydraulicValue()) for name in profile_names} for face in ("UP", "DN")}
        model.hydraulic_values["OPENINGS"] = {g.get("source_id",str(i)): {name:asdict(HydraulicValue()) for name in profile_names} for i,g in enumerate(model.openings)}
        models.append(model)
    refresh(models, xs_rows)
    return models


def interpolate_deck(points, station):
    points = [(float(x), float(y)) for x,y in points]
    if len(points) < 2 or any(not math.isfinite(v) for pair in points for v in pair):
        raise ValueError("At least two finite source deck points are required.")
    if any(points[index][0] > points[index + 1][0] for index in range(len(points) - 1)):
        raise ValueError("Source deck stations must be nondecreasing in source order.")
    if station < points[0][0] or station > points[-1][0]:
        raise ValueError(
            f"Barrel station {station:g} is outside source deck range "
            f"{points[0][0]:g}..{points[-1][0]:g}; extrapolation is unsupported.")
    exact = {y for x, y in points if abs(x - station) <= TOLERANCE_FT}
    if len(exact) == 1:
        return next(iter(exact))
    if len(exact) > 1:
        raise ValueError(
            f"Barrel station {station:g} coincides with a vertical source deck segment "
            "having multiple elevations.")
    for (x0,y0),(x1,y1) in zip(points,points[1:]):
        if x0 < station < x1:
            return y0 + (station-x0)/(x1-x0)*(y1-y0)
    raise ValueError("Barrel station could not be located on the source deck.")


def _same_number(left, right):
    left, right = finite(left), finite(right)
    return left is not None and right is not None and abs(left-right) <= TOLERANCE_FT


def _finite_profile(source, key):
    """Return a verified source station/elevation profile or raise fail-closed."""
    values = source.get("source_profiles", {}).get(key, [])
    result = []
    for point in values:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError(f"{key} contains a malformed source point.")
        station, elevation = finite(point[0]), finite(point[1])
        # HEC-RAS profile tables retain blank lid cells as NaN. They are not
        # chord vertices and must not invalidate the finite vertices that
        # establish the source chord.
        if station is None or elevation is None:
            continue
        result.append((station, elevation))
    # In HEC-RAS BR Lid profiles, zero-valued outer points are placeholders
    # for stations without a lid (the populated chord elevations remain in
    # the same source slice). Do not turn those placeholders into elevation 0.
    if "BR Lid Profile" in key and any(elevation != 0 for _, elevation in result):
        result = [(station, elevation) for station, elevation in result if elevation != 0]
    if not result:
        raise ValueError(f"{key} is required for source-backed bridge chords.")
    return result


def _legacy_text_groups(text):
    groups = list(text.get("culvert_groups", [])) if text else []
    if groups or not text or len(text.get("culvert_records", [])) != 1:
        return groups
    stations = [(text.get("barrel_us_station"),text.get("barrel_ds_station"))]
    return [{"record_kind":"LEGACY_SINGLE_BARREL", "rise":text.get("rise"),
             "length":text.get("length"), "invert_us":text.get("invert_us"),
             "invert_ds":text.get("invert_ds"), "barrels":1,
             "name":text.get("name", ""), "us_distance":text.get("us_distance"),
             "barrel_stations":stations, "source_line":text.get("source_line")}]


def _match_text_group(group, candidates, used):
    expected_name = " ".join(str(group.get("Name", "")).lower().split())
    matches = []
    for index,candidate in enumerate(candidates):
        if index in used:
            continue
        actual_name = " ".join(str(candidate.get("name", "")).lower().split())
        if expected_name and actual_name and expected_name != actual_name:
            continue
        comparisons = (("rise","Rise"),("length","Length"),("us_distance","US Distance"),
                       ("invert_us","Invert US"),("invert_ds","Invert DS"),("barrels","Barrels"))
        if not all(_same_number(candidate.get(text_key),group.get(hdf_key))
                   for text_key,hdf_key in comparisons):
            continue
        if candidate.get("span") is not None and not _same_number(candidate.get("span"),group.get("Span")):
            continue
        shape_code = candidate.get("shape_code")
        if shape_code is not None and not _same_number(shape_code,group.get("Shape")):
            continue
        matches.append((index,candidate))
    if len(matches) != 1:
        raise ValueError("Plan-linked geometry text/HDF group identity or dimensions disagree; each HDF culvert group must match exactly one source record.")
    return matches[0]


def refresh(models, xs_rows):
    """Same placement rule at initial load, edits, recalculation and export."""
    from .structure_capabilities import STRUCTURE_CAPABILITIES
    xs = {(r.river,r.reach,r.rs): r for r in xs_rows}
    order = {(r.river,r.reach,r.rs): i for i,r in enumerate(xs_rows)}
    for m in models:
        decision = STRUCTURE_CAPABILITIES.evaluate(m)
        m.capability_id = decision.capability_id
        declaration = STRUCTURE_CAPABILITIES.declaration(str(m.type))
        m.capability_level = declaration.support_level if declaration else "UNSUPPORTED"
        m.missing_source_fields = list(decision.missing_source_fields)
        m.affected_workflows = list(decision.affected_workflows)
        m.capability_evidence = list(decision.evidence)
        m.status, m.reason = "UNSUPPORTED", decision.reason
        m.anchor = m.bounds = m.occupied_interval = None
        m.faces = []
        m.partition_valid = False
        m.upstream_gap = m.occupied_distance = m.downstream_residual = None
        m.provenance.pop("geometry", None)
        us, ds = xs.get(m.upstream), xs.get(m.downstream)
        if us is not None:
            m.anchor = us.cumdist
            m.provenance["anchor"] = {"kind":"DISPLAY_ASSOCIATION","source_xs":m.upstream,
                                      "note":"Connected upstream XS CUMDIST; not a structure face or lateral start station."}
        adjacent = us is not None and ds is not None and m.upstream[:2] == m.downstream[:2] == (m.river,m.reach) and order[m.downstream] == order[m.upstream]+1
        if adjacent and us.cumdist >= ds.cumdist:
            m.bounds = (ds.cumdist, us.cumdist)
            m.provenance["bounds"] = {"kind":"SOURCE_TOPOLOGY","rule":"adjacent explicit bounding XS",
                                      "upstream":m.upstream,"downstream":m.downstream}
        if m.type == "LATERAL":
            # Anchor is a display association, never a longitudinal weir extent.
            m.bounds = None
            m.status = "IMPLEMENTED_NEEDS_VALIDATION"
            m.reason = "Upstream anchor/zero added distance only are validated. Main-channel start/end extent and receiving-side semantics need a long lateral/receiving-area fixture; source crest and connectivity are retained."
            continue
        if m.type == "BRIDGE":
            if decision.missing_source_fields:
                m.status = "INVALID"
                continue
            if not decision.export_eligible:
                continue
            if not adjacent:
                m.status = "INVALID"
                m.reason = "Explicit upstream/downstream connections must identify adjacent ordinary XS in this reach; numeric nearest-RS placement is forbidden."
                continue
            source = m.source
            gap = finite(source.get("upstream_distance"))
            width = finite(source.get("weir_width"))
            if gap is None or width is None or gap < 0 or width <= 0:
                m.status, m.reason = "INVALID", "Bridge upstream distance and width must be finite source values with positive width."
                continue
            residual = us.length_chnl-gap-width
            if not math.isfinite(residual) or residual < -TOLERANCE_FT:
                m.status, m.reason = "INVALID", "Bridge upstream distance + width exceeds the owning upstream XS working channel interval."
                continue
            if not math.isclose(us.cumdist-ds.cumdist, us.length_chnl, rel_tol=0, abs_tol=TOLERANCE_FT):
                m.status, m.reason = "INVALID", "Working channel length and CUMDIST disagree; recalculate CUMDIST before export."
                continue
            up, dn = us.cumdist-gap, us.cumdist-gap-width
            if dn < ds.cumdist-TOLERANCE_FT or up > us.cumdist+TOLERANCE_FT:
                m.status, m.reason = "INVALID", "Bridge faces lie outside their connected XS interval."
                continue
            try:
                profiles = {key: _finite_profile(source, key) for key in (
                    "US BR Weir Profile", "US BR Lid Profile",
                    "DS BR Weir Profile", "DS BR Lid Profile")}
                faces = []
                for label, station, top_key, low_key in (
                    ("UP", up, "US BR Weir Profile", "US BR Lid Profile"),
                    ("DN", dn, "DS BR Weir Profile", "DS BR Lid Profile")):
                    low_chord = min(elevation for _, elevation in profiles[low_key])
                    high_chord = max(elevation for _, elevation in profiles[top_key])
                    if high_chord < low_chord:
                        raise ValueError(f"{label} bridge high chord is below its low chord.")
                    faces.append({"face": label, "station": station,
                                  "invert": low_chord, "low_chord": low_chord,
                                  "high_chord": high_chord,
                                  "opening_id": source.get("source_id", m.identity),
                                  "opening_name": str(source.get("name", "")).strip(),
                                  "shape": "Bridge chord envelope", "barrels": 0,
                                  "rise": high_chord-low_chord, "span": width,
                                  "provenance": {
                                      "low_chord": f"MINIMUM SOURCE HDF {low_key}",
                                      "high_chord": f"MAXIMUM SOURCE HDF {top_key}",
                                      "longitudinal_position": "SOURCE upstream distance and bridge width within explicit bounding XS",
                                      "hydraulics": "MISSING; adjacent XS WSEL is not copied"}})
            except (ValueError, KeyError, TypeError) as exc:
                m.status, m.reason = "INVALID", str(exc)
                continue
            m.faces = faces
            m.upstream_gap, m.occupied_distance, m.downstream_residual = gap, width, max(0., residual)
            m.occupied_interval = (dn, up)
            m.partition_valid = True
            m.provenance["geometry"] = {"kind": "SOURCE_AND_DERIVED_RULE",
                "rule": "bridge-chord-envelope-v1",
                "position": "source upstream distance and width",
                "elevations": "source US/DS BR Lid and BR Weir profiles"}
            m.status = "VALIDATED"
            m.reason = "Bridge faces use explicit XS connectivity, source longitudinal placement, and source HDF low/high chord profiles; no structure hydraulic result is claimed."
            continue
        if m.type not in {"CULVERT", "MULTIPLE_OPENING"}:
            if m.type in {"INLINE", "WEIR"}:
                m.status = "IMPLEMENTED_NEEDS_VALIDATION"
            if m.type == "GATED":
                m.reason = "Gate geometry and operating states require a real gated-structure fixture; generic structure fields cannot establish them."
            continue
        if decision.missing_source_fields:
            m.status = "INVALID"
            continue
        if not decision.export_eligible:
            continue
        if not adjacent:
            m.status = "INVALID"
            m.reason = "Explicit upstream/downstream connections must identify adjacent ordinary XS in this reach; numeric nearest-RS placement is forbidden."
            continue
        source = m.source
        groups = m.openings
        declared = finite(source.get("source_attributes",{}).get("Culvert Groups",source.get("source_attributes",{}).get("Culverts",len(groups))))
        if not groups or declared != len(groups) or source.get("source_issues"):
            m.reason = "Every declared culvert group must have one unique, explicitly associated HDF source record."
            continue
        gaps = [finite(group.get("US Distance")) for group in groups]
        lengths = [finite(group.get("Length")) for group in groups]
        if any(value is None for value in gaps+lengths) or any(value < 0 for value in gaps+lengths if value is not None):
            m.bounds = None
            m.status, m.reason = "INVALID", "Culvert upstream gaps and barrel lengths must be finite nonnegative source values."
            continue
        if any(not _same_number(value,gaps[0]) for value in gaps[1:]) or any(not _same_number(value,lengths[0]) for value in lengths[1:]):
            m.status,m.reason = "UNSUPPORTED","Culvert groups with different longitudinal distances cannot be combined; per-group placement needs a validated fixture."
            continue
        gap,length = gaps[0],lengths[0]
        if gap is None or length is None or gap < 0 or length < 0:
            m.bounds = None
            m.status, m.reason = "INVALID", "Culvert upstream gap and barrel length must be finite nonnegative source values."
            continue
        residual = us.length_chnl-gap-length
        if not math.isfinite(residual) or residual < -TOLERANCE_FT:
            m.bounds = None
            m.status, m.reason = "INVALID", "Culvert gap + barrel exceeds the owning upstream XS working channel interval."
            continue
        # Use the working interval; source length remains immutable. A stale axis
        # must not pass preflight after a working-length edit.
        if not math.isclose(us.cumdist-ds.cumdist, us.length_chnl, rel_tol=0, abs_tol=TOLERANCE_FT):
            m.bounds = None
            m.status, m.reason = "INVALID", "Working channel length and CUMDIST disagree; recalculate CUMDIST before export."
            continue
        up, dn = us.cumdist-gap, us.cumdist-gap-length
        if dn < ds.cumdist-TOLERANCE_FT or up > us.cumdist+TOLERANCE_FT:
            m.bounds = None
            m.status, m.reason = "INVALID", "Culvert faces lie outside their connected XS interval."
            continue
        m.upstream_gap, m.occupied_distance, m.downstream_residual = gap, length, max(0.,residual)
        m.occupied_interval = (dn,up)
        m.partition_valid = True
        m.provenance["geometry"] = {"kind": "DERIVED_RULE", "rule": "single-culvert-interval-v1", "up_face": "US XS CUMDIST - source US Distance", "down_face": "UP face - source Length", "tolerance_ft": TOLERANCE_FT}
        text = m.text_geometry
        if not text or len(text.get("deck_header",[])) < 6:
            m.reason = "Unique plan-linked geometry text with verified culvert and deck records is required for authoritative face elevations."
            continue
        try:
            if int(finite(text["deck_header"][4]) or 0) != len(text.get("deck_up",[])) or int(finite(text["deck_header"][5]) or 0) != len(text.get("deck_dn",[])):
                raise ValueError("Plan-linked deck point counts do not match the parsed upstream/downstream deck geometry.")
            text_groups = _legacy_text_groups(text)
            used = set()
            faces = []
            supported_shapes = {"box","circular","ellipse"}
            for opening_index,group in enumerate(groups):
                shape = str(group.get("Shape Name", "")).strip()
                if shape.lower() not in supported_shapes:
                    raise ValueError(f"Culvert shape {shape or 'UNKNOWN'} has no validated longitudinal-envelope rule.")
                rise = finite(group.get("Rise")); span = finite(group.get("Span"))
                inv_up,inv_dn = finite(group.get("Invert US")),finite(group.get("Invert DS"))
                barrels = finite(group.get("Barrels"))
                if rise is None or rise <= 0 or span is None or span <= 0 or inv_up is None or inv_dn is None or barrels is None or barrels < 1 or int(barrels) != barrels:
                    raise ValueError("Finite positive source rise/span, integral barrel count, and invert elevations are required.")
                text_index,text_group = _match_text_group(group,text_groups,used);used.add(text_index)
                stations = text_group.get("barrel_stations",[])
                if len(stations) != int(barrels):
                    raise ValueError("Plan-linked barrel-station count disagrees with the HDF group barrel count.")
                source_barrels = group.get("barrel_records", [])
                if source_barrels:
                    if len(source_barrels) != int(barrels):
                        raise ValueError("HDF barrel-record count disagrees with the HDF group barrel count.")
                    hdf_stations = [(finite(record.get("US Station")), finite(record.get("DS Station")))
                                    for record in source_barrels]
                    if any(value is None for pair in hdf_stations for value in pair):
                        raise ValueError("HDF barrel records require finite upstream/downstream stations.")
                    if any(not _same_number(actual, expected)
                           for pair, expected_pair in zip(hdf_stations, stations)
                           for actual, expected in zip(pair, expected_pair)):
                        raise ValueError("Plan-linked geometry text and HDF barrel stations disagree.")
                opening_id = str(group.get("source_id",opening_index))
                for label,station,inv,deck,station_side in [
                        ("UP",up,inv_up,text["deck_up"],0),("DN",dn,inv_dn,text["deck_dn"],1)]:
                    transverse = [finite(pair[station_side]) for pair in stations]
                    if any(value is None for value in transverse):
                        raise ValueError("Missing source barrel station; deck interpolation is unsupported.")
                    deck_values = [interpolate_deck(deck,float(value)) for value in transverse]
                    face = {"face":label,"station":station,"invert":inv,"low_chord":inv+rise,
                            "high_chord":max(deck_values),"opening_id":opening_id,
                            "opening_name":str(group.get("Name", "")).strip(),"shape":shape,
                            "barrels":int(barrels),"rise":rise,"span":span}
                    if face["high_chord"] < face["low_chord"]:
                        raise ValueError("Deck elevation is below the opening soffit.")
                    face["provenance"] = {"invert":"SOURCE HDF culvert group",
                        "low_chord":"DERIVED: source invert + source opening rise",
                        "high_chord":"MAXIMUM INTERPOLATED: plan-linked deck at every source barrel centerline",
                        "shape":"SOURCE HDF culvert group; retained as metadata, not redrawn as a cross section",
                        "text_path":text["source_path"],"text_line":text_group.get("source_line",text["source_line"])}
                    faces.append(face)
            if len(used) != len(text_groups):
                raise ValueError("Plan-linked geometry text contains unmatched culvert groups.")
            m.faces = faces
        except (ValueError, KeyError, TypeError) as exc:
            m.faces = []
            m.status, m.reason = "INVALID", str(exc)
            continue
        m.status = "VALIDATED"
        m.reason = "Each culvert group is source-matched and rendered as a longitudinal opening envelope; transverse shape is retained as metadata and no structure-face hydraulic result is claimed."
    # Never collapse or independently certify configurations sharing an interval.
    identities = {}
    source_identities = {}
    intervals = {}
    owners = {}
    for m in models:
        identities.setdefault(m.identity,[]).append(m)
        if m.source.get("source_id"):
            source_identities.setdefault((m.source.get("source_path"),m.source["source_id"]),[]).append(m)
        if m.upstream and m.downstream:
            intervals.setdefault((m.upstream,m.downstream),[]).append(m)
        for g in m.openings:
            if g.get("source_id"):
                owners.setdefault((m.source.get("source_path"),g["source_id"]),[]).append(m)
    for group in source_identities.values():
        if len(group)>1:
            for m in group:
                m.status,m.reason = "INVALID","Duplicate source structure identity: verify the source record association; separate normalized records were retained."
    for group in list(identities.values()) + list(intervals.values()) + list(owners.values()):
        if len(group) > 1:
            for m in group:
                if m.status != "INVALID":
                    m.status = "UNSUPPORTED"
                    m.reason = "Distinct structures/openings share an identity, source group or XS interval. All records are retained; combined ownership/placement needs a real multiple-structure fixture."


def models_from_rows(rows):
    models = {}
    for row in rows:
        model = getattr(row,"structure_model",None)
        if model is not None:
            models.setdefault(model.identity,model)
    return list(models.values())


def sync_rows(rows):
    models = models_from_rows(rows)
    refresh(models,[r for r in rows if r.kind == "xs"])
    for row in rows:
        m = getattr(row,"structure_model",None)
        if m is None:
            continue
        face = next((f for f in m.faces if row.struc.endswith("-"+f["face"])),None)
        row.cumdist = face["station"] if face else (m.anchor if m.type == "LATERAL" else None)
        row.minchel = face["invert"] if face else None
        row.low_chord = face["low_chord"] if face else None
        row.high_chord = face["high_chord"] if face else None
        row.structure_partition_valid = m.partition_valid
        row.structure_partition_warning = "" if m.status == "VALIDATED" else m.reason
        row.culvert_us_gap, row.culvert_barrel_length, row.culvert_ds_residual = m.upstream_gap,m.occupied_distance,m.downstream_residual
    return models


def inclusion(model, start, end):
    bounds = model.occupied_interval if model.status == "VALIDATED" else model.bounds
    affects = bounds is None or bounds[1] >= start and bounds[0] <= end
    faces = [f for f in model.faces if start <= f["station"] <= end] if model.status == "VALIDATED" else []
    return affects, faces


def preflight(rows, start, end):
    models = sync_rows(rows)
    records = []
    for m in models:
        affects, faces = inclusion(m,start,end)
        detail = m.reason
        if m.missing_source_fields and "Missing source fields:" not in detail:
            detail += " Missing source fields: " + ", ".join(m.missing_source_fields) + "."
        if m.affected_workflows:
            detail += " Affected workflows: " + ", ".join(m.affected_workflows) + "."
        records.append({"identity":m.identity,"river":m.river,"reach":m.reach,"rs":m.rs,"type":m.type,
                        "status":m.status,"reason":m.reason,"affects_range":affects,"faces":faces,
                        "occupied_interval":m.occupied_interval,"bounds":m.bounds,
                        "capability_id":m.capability_id,
                        "capability_level":m.capability_level,
                        "missing_source_fields":list(m.missing_source_fields),
                        "affected_workflows":list(m.affected_workflows),
                        "capability_evidence":list(m.capability_evidence),
                        "message":f"{m.river} / {m.reach} / RS {m.rs} / {m.type}: {detail}"})
    for r in rows:
        if r.kind == "struct" and getattr(r,"structure_model",None) is None:
            records.append({"identity":f"untracked-row-{r.id}","river":r.river,"reach":r.reach,"rs":r.rs,"type":r.struc,"status":"INVALID","reason":"Missing normalized source structure.","affects_range":True,"faces":[],"message":f"{r.river} / {r.reach} / RS {r.rs} / {r.struc}: normalized source identity/provenance missing; reopen the HEC-RAS plan."})
    return records


def export_faces(rows,start,end):
    from types import SimpleNamespace
    records = preflight(rows,start,end)
    blockers = [record["message"] for record in records
                if record["affects_range"] and record["status"] != "VALIDATED"]
    if blockers:
        raise ValueError("Structure export blocked: " + " | ".join(blockers))
    return [SimpleNamespace(structure_id=r["identity"], river=r["river"],reach=r["reach"],rs=r["rs"],
                struc=r["type"]+"-"+f["face"],cumdist=f["station"],minchel=f["invert"],
                low_chord=f["low_chord"],high_chord=f["high_chord"],
                opening_id=f.get("opening_id","opening"),opening_name=f.get("opening_name",""),
                shape=f.get("shape",""),barrels=f.get("barrels",1),rise=f.get("rise"),span=f.get("span"))
            for r in records for f in r["faces"]]
