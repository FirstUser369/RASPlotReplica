"""Source-backed structure normalization, placement and export preflight.

Only the single-box/single-barrel longitudinal culvert rule is fixture-validated.
All other classes retain evidence without manufacturing engineering faces.
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
    if "multiple" in label or len(source.openings) > 1:
        return "MULTIPLE_OPENING"
    if "gate" in label or any((finite(raw.get(k)) or 0) > 0 for k in ("Gates", "Gate Groups", "Culverts Flap Gates")):
        return "GATED"
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
    for entry in parse_geometry_text_structures(text_path):
        result.setdefault((entry.river, entry.reach, entry.rs), []).append(asdict(entry))
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
    points = sorted((float(x), float(y)) for x,y in points)
    if len(points) != 2 or any(not math.isfinite(v) for pair in points for v in pair):
        raise ValueError("Only the source-verified two-point deck schema is validated.")
    (x0,y0),(x1,y1) = points
    if x0 >= x1 or station < x0 or station > x1:
        raise ValueError("Barrel station is outside an unambiguous source deck; extrapolation is unsupported.")
    return y0 + (station-x0)/(x1-x0)*(y1-y0)


def refresh(models, xs_rows):
    """Same placement rule at initial load, edits, recalculation and export."""
    xs = {(r.river,r.reach,r.rs): r for r in xs_rows}
    order = {(r.river,r.reach,r.rs): i for i,r in enumerate(xs_rows)}
    for m in models:
        m.status, m.reason = "UNSUPPORTED", "No validated type-specific geometry rule; a real HEC-RAS fixture is required."
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
        if m.type != "CULVERT":
            if m.type in {"BRIDGE", "INLINE", "WEIR"}:
                m.status = "IMPLEMENTED_NEEDS_VALIDATION"
            if m.type == "MULTIPLE_OPENING":
                m.reason = "Multiple openings/groups are preserved individually; their combined geometry and hydraulics require a real multiple-opening fixture."
            if m.type == "GATED":
                m.reason = "Gate geometry and operating states require a real gated-structure fixture; generic structure fields cannot establish them."
            continue
        if not adjacent:
            m.status = "INVALID"
            m.reason = "Explicit upstream/downstream connections must identify adjacent ordinary XS in this reach; numeric nearest-RS placement is forbidden."
            continue
        source = m.source
        gap, length = finite(source.get("culvert_us_distance")), finite(source.get("culvert_length"))
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
        inv_up,inv_dn = finite(source.get("invert_us")),finite(source.get("invert_ds"))
        m.faces = [{"face":label,"station":station,"invert":inv,"low_chord":None,"high_chord":None}
                   for label,station,inv in [("UP",up,inv_up),("DN",dn,inv_dn)]]
        groups = m.openings
        text = m.text_geometry
        declared = finite(source.get("source_attributes",{}).get("Culvert Groups",source.get("source_attributes",{}).get("Culverts",len(groups))))
        if len(groups) != 1 or declared != 1 or source.get("source_issues"):
            m.reason = "One uniquely associated culvert group is required; missing, mismatched or ambiguous groups cannot be exported."
            continue
        group = groups[0]
        if str(group.get("Shape Name", "")).lower() != "box" or finite(group.get("Barrels")) != 1:
            m.reason = "Only single-box/single-barrel geometry is fixture-validated; provide the alternative shape/group model."
            continue
        if not text or len(text.get("culvert_records", [])) != 1 or len(text.get("deck_header",[])) < 6:
            m.reason = "Unique plan-linked geometry text with one culvert and verified deck records is required for authoritative face elevations."
            continue
        rise = finite(group.get("Rise"))
        try:
            if any(finite(text["deck_header"][i]) != 2 for i in (4,5)):
                raise ValueError("Only the observed two-point upstream/downstream deck schema is validated.")
            if rise is None or rise < 0 or inv_up is None or inv_dn is None:
                raise ValueError("Finite source rise and invert elevations are required.")
            for key, actual in [("rise",rise),("length",length),("invert_us",inv_up),("invert_ds",inv_dn)]:
                if finite(text.get(key)) is None or abs(float(text[key])-actual) > TOLERANCE_FT:
                    raise ValueError(f"Plan-linked text/HDF {key} disagree; confirm matching source files.")
            for face,deck,station in [(m.faces[0],text["deck_up"],text["barrel_us_station"]),(m.faces[1],text["deck_dn"],text["barrel_ds_station"])]:
                if finite(station) is None:
                    raise ValueError("Missing source barrel station; deck interpolation is unsupported.")
                face["low_chord"] = face["invert"]+rise
                face["high_chord"] = interpolate_deck(deck,float(station))
                if face["high_chord"] < face["low_chord"]:
                    raise ValueError("Deck elevation is below the opening soffit.")
                face["provenance"] = {"invert":"SOURCE HDF culvert group", "low_chord":"DERIVED: source invert + box rise", "high_chord":"INTERPOLATED: plan-linked deck at source barrel station", "text_path":text["source_path"], "text_line":text["source_line"]}
        except (ValueError, KeyError, TypeError) as exc:
            m.status, m.reason = "INVALID", str(exc)
            continue
        m.status = "VALIDATED"
        m.reason = "Single-box/single-barrel longitudinal face rule validated against North; no structure-face hydraulic results are claimed."
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
        records.append({"identity":m.identity,"river":m.river,"reach":m.reach,"rs":m.rs,"type":m.type,
                        "status":m.status,"reason":m.reason,"affects_range":affects,"faces":faces,
                        "occupied_interval":m.occupied_interval,"bounds":m.bounds,
                        "message":f"{m.river} / {m.reach} / RS {m.rs} / {m.type}: {m.reason}"})
    for r in rows:
        if r.kind == "struct" and getattr(r,"structure_model",None) is None:
            records.append({"identity":f"untracked-row-{r.id}","river":r.river,"reach":r.reach,"rs":r.rs,"type":r.struc,"status":"INVALID","reason":"Missing normalized source structure.","affects_range":True,"faces":[],"message":f"{r.river} / {r.reach} / RS {r.rs} / {r.struc}: normalized source identity/provenance missing; reopen the HEC-RAS plan."})
    return records


def export_faces(rows,start,end):
    from types import SimpleNamespace
    records = preflight(rows,start,end)
    for record in records:
        if record["affects_range"] and record["status"] != "VALIDATED":
            raise ValueError("Structure export blocked: "+record["message"])
    return [SimpleNamespace(structure_id=r["identity"], river=r["river"],reach=r["reach"],rs=r["rs"],
                struc=r["type"]+"-"+f["face"],cumdist=f["station"],minchel=f["invert"],
                low_chord=f["low_chord"],high_chord=f["high_chord"])
            for r in records for f in r["faces"]]
