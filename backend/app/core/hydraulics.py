"""Hydraulic meaning: explicit state, source identity and gap-preserving geometry."""
import math
import hashlib
from collections import Counter


def normalize_value(value, dry=(), missing=()):
    raw = value
    if value is None:
        return {"value":None,"state":"MISSING","raw":None}
    try:
        value = float(value)
    except (ValueError,TypeError):
        return {"value":None,"state":"INVALID","raw":str(raw)}
    if not math.isfinite(value):
        return {"value":None,"state":"NONFINITE","raw":str(value)}
    if value in dry:
        return {"value":None,"state":"DRY","raw":value}
    if value in missing:
        return {"value":None,"state":"MISSING","raw":value}
    # Do not claim DRY from an undocumented sentinel. Preserve ambiguity.
    if value in (-9999.,-99999.) or value <= -1e30:
        return {"value":None,"state":"UNKNOWN_SENTINEL","raw":value}
    return {"value":value,"state":"VALID","raw":value}


def prepare_profiles(profiles):
    counts=Counter(p.name for p in profiles)
    for i,p in enumerate(profiles):
        if not p.profile_id:
            material=f"{p.source_path}|{p.source_dataset}|{p.result_type}|{i}"
            p.profile_id="profile-"+hashlib.sha256(material.encode()).hexdigest()[:20]
        p.key=p.profile_id
        if not p.values:
            p.values=[normalize_value(v) for v in p.wsel]
        if len(p.values)!=len(p.wsel):
            raise ValueError(f"{p.name}: hydraulic state/result count mismatch.")
        p.wsel=[v["value"] for v in p.values]
    if len({p.profile_id for p in profiles})!=len(profiles):
        raise ValueError("Duplicate hydraulic profile identity; reopen a verified result dataset.")
    if len({p.key for p in profiles})!=len(profiles):
        for p in profiles: p.key=p.profile_id


def profile_label(profile, rows):
    label=profile.legend_text or profile.display_name or profile.name
    if profile.result_type=="DERIVED_MAXIMUM_ENVELOPE" and "derived saved-timestep envelope" not in label:
        label+=" (derived saved-timestep envelope)"
    if any(any(a.get("profile_id")==profile.profile_id or a.get("profile_key")== (profile.profile_id) for a in row.adjustments) for row in rows):
        label+=" (user-adjusted WSEL)"
    return label


def resolve_profiles(profiles, selections):
    result=[]
    for selection in selections:
        matches=[p for p in profiles if selection==p.profile_id]
        if len(matches)!=1:
            raise ValueError(f"Profile {selection!r} is missing or ambiguous; select its stable profile ID.")
        if matches[0] not in result:
            result.append(matches[0])
    return result


def segments(points):
    """Never connect across a missing/nonfinite ordinate."""
    run=[]
    for x,y in points:
        if y is None or not math.isfinite(float(y)):
            if run: yield run
            run=[]
        else:
            run.append((x,y))
    if run: yield run


def ground_value(points, left, right):
    if not points:
        return None,{"state":"MISSING","method":"NONE","reason":"Missing station/elevation geometry"}
    if any(len(p)!=2 or any(v is None or not math.isfinite(float(v)) for v in p) for p in points):
        return None,{"state":"INVALID","method":"NONE","reason":"Nonfinite or malformed station/elevation geometry"}
    if left is None or right is None or not all(math.isfinite(float(v)) for v in (left,right)) or left>right:
        return None,{"state":"INVALID","method":"NONE","reason":"Invalid bank stations"}
    if any(a[0]>b[0] for a,b in zip(points,points[1:])) or left<points[0][0] or right>points[-1][0]:
        return None,{"state":"INVALID","method":"NONE","reason":"Bank stations outside geometry or unordered station/elevation array"}
    values=[e for s,e in points if left<=s<=right]
    if not values:
        return None,{"state":"INVALID","method":"NONE","reason":"Empty bank-station slice; full-section fallback is not authorized"}
    return min(values),{"state":"VALID","method":"MINIMUM_BETWEEN_BANK_STATIONS","banks":[left,right]}


def hydraulic_preflight(db,selected,start=None,end=None):
    errors=[];warnings=[]
    from .state_contract import stationing_dirty
    if stationing_dirty(db):
        errors.append("CUMDIST is DIRTY; explicitly recalculate stationing before export.")
    if db.structure_inventory.get("state") not in ("VERIFIED_NONE","VERIFIED_PARSED"):
        errors.append("Structure inventory is incomplete/unsupported: " + db.structure_inventory.get("reason", "source verification required"))
    if db.working_units != "INTERNAL_US_CUSTOMARY":
        errors.append(f"Working units {db.working_units!r} are unsupported; canonical engineering feet are required.")
    if db.source_units == "SI":
        manifest = db.unit_contract or {}
        if (db.unit_conversion_status != "COMPLETE"
                or manifest.get("contract_version") != "rasplot-length-units-v1"
                or manifest.get("source_length_unit") != "m"
                or manifest.get("working_length_unit") != "ft"):
            errors.append("SI source conversion metadata is missing or inconsistent; reopen the source with a verified unit adapter.")
    elif db.source_units not in ("US Customary","INTERNAL_US_CUSTOMARY"):
        errors.append(f"Source units {db.source_units!r} are unsupported or unknown.")
    if not selected:
        errors.append("No hydraulic profiles selected.")
    if any(not p.profile_id for p in db.profiles) or len({p.profile_id for p in db.profiles})!=len(db.profiles):
        errors.append("Hydraulic profile identities are ambiguous.")
    if not db.header.vertical_datum or db.header.vertical_datum.upper()=="UNKNOWN":
        warnings.append("Vertical datum UNKNOWN; output explicitly labels UNKNOWN. Confirm datum before a deliverable requiring a known datum.")
        if db.header.require_known_datum:
            errors.append("This deliverable requires a known datum; enter a verified datum or supply explicit source metadata. No datum transformation is performed.")
    for p in selected:
        if p.frequency and p.mapping_provenance not in ("USER_ENTERED","SOURCE"):
            errors.append(f"{p.name}: frequency claim has no source or deliberate user mapping.")
        if p.result_type=="DERIVED_MAXIMUM_ENVELOPE" and p.frequency:
            errors.append(f"{p.name}: a saved-timestep envelope cannot be classified as annual-chance frequency.")
        if p.backwater_elev is not None and not math.isfinite(p.backwater_elev):
            errors.append(f"{p.name}: user-entered backwater must be finite or cleared.")
        for r in db.working:
            if r.kind!="xs":
                if r.profiles.get(p.profile_id) is not None:
                    errors.append(f"{r.river}/{r.reach}/RS {r.rs}: structure hydraulic points lack a validated result adapter and cannot enter authoritative WSEL lines.")
                continue
            if start is not None and not start<=r.cumdist<=end: continue
            value=r.profiles.get(p.profile_id)
            if value is not None and not math.isfinite(value):
                errors.append(f"{r.river}/{r.reach}/RS {r.rs}/{p.name}: nonfinite working WSEL; clear or correct it.")
            provenance=r.hydraulic_values.get(p.profile_id,{})
            if provenance.get("state") in ("MISSING","DRY","NONFINITE","PARTIAL_ENVELOPE"):
                warnings.append(f"{r.river}/{r.reach}/RS {r.rs}/{p.name}: {provenance['state']}; source state retained.")
            if provenance.get("state") in ("UNKNOWN_SENTINEL","INVALID"):
                errors.append(f"{r.river}/{r.reach}/RS {r.rs}/{p.name}: {provenance['state']} {provenance.get('raw')}; verified source value/dry/missing semantics are required.")
            if r.ground_provenance.get("method")=="USER_ENTERED":
                warnings.append(f"{r.river}/{r.reach}/RS {r.rs}: ground elevation was entered by the user; retained source provenance is available in the working record.")
            if r.ground_provenance.get("state") in ("INVALID","MISSING"):
                errors.append(f"{r.river}/{r.reach}/RS {r.rs}: {r.ground_provenance.get('reason')}")
    return {"can_export":not errors,"errors":list(dict.fromkeys(errors)),"warnings":warnings}
