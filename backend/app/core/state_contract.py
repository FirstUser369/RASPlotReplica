"""Dependency evidence for explicit stationing and saved-panel review."""
import hashlib
import json
import math
from copy import deepcopy

MAX_PANELS = 500


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def station_inputs(db):
    return fingerprint([(r.id, r.river, r.reach, r.rs, r.length_chnl,
                         r.connected_us_rs, r.connected_ds_rs,
                         r.structure_model.source if r.structure_model else None) for r in db.working]
                       + [("start", db.stationing_start)])


def seal_stationing(db):
    db.stationing_signature = station_inputs(db)


def stationing_dirty(db):
    return bool(db.stationing_signature and db.stationing_signature != station_inputs(db))


def require_stationing(db):
    if stationing_dirty(db):
        raise ValueError("CUMDIST is DIRTY. Recalculate CUMDIST before Preview, Panel Setup or export; saved panels will be preserved for review.")


def panel_inputs(db):
    return fingerprint([station_inputs(db), [(r.id, r.cumdist, r.minchel, r.profiles, r.low_chord, r.high_chord,
                                               r.label_letter, r.label_text) for r in db.working],
                        [(p.profile_id, p.selected, p.backwater_elev) for p in db.profiles],
                        db.header.revision_start_station, db.header.revision_end_station,
                        db.source_units, db.working_units, db.unit_contract,
                        db.unit_conversion_status, db.structure_inventory,
                        [(lm.distance, lm.elevation) for lm in db.landmarks],
                        [(p.panel, p.panel_suffix, p.start_station, p.end_station,
                          p.start_elevation, p.end_elevation, p.h_scale, p.v_scale,
                          p.panel_x, p.panel_y) for p in db.panels]])


def panel_dependency_categories(db):
    """Category-level evidence used only when it can be compared reliably."""
    return {
        "stationing": station_inputs(db),
        "working geometry and hydraulics": fingerprint([(r.id, r.cumdist, r.minchel, r.profiles, r.low_chord, r.high_chord) for r in db.working]),
        "cross-section annotations": fingerprint([(r.id, r.label_letter, r.label_text) for r in db.working]),
        "profile mapping and backwater": fingerprint([(p.profile_id, p.selected, p.backwater_elev, p.backwater_text, p.frequency, p.display_name) for p in db.profiles]),
        "revision limits": fingerprint([db.header.revision_start_station, db.header.revision_end_station]),
        "structures and source units": fingerprint([db.source_units, db.working_units,
                                                       db.unit_contract, db.unit_conversion_status,
                                                       db.structure_inventory]),
        "landmarks": fingerprint([(lm.id, lm.distance, lm.elevation, lm.text, lm.orientation, lm.adjusted) for lm in db.landmarks]),
        "panel definitions and scales": fingerprint([
            [(p.panel, p.panel_suffix, p.start_station, p.end_station,
              p.start_elevation, p.end_elevation, p.h_scale, p.v_scale,
              p.panel_x, p.panel_y) for p in db.panels]]),
    }


def panel_dependency_snapshots(db):
    """Small review-time snapshots used only for defensible change explanations."""
    return {
        "working geometry and hydraulics": [{
            "identity": r.row_identity or str(r.id), "river": r.river,
            "reach": r.reach, "rs": r.rs, "station": r.cumdist,
            "minchel": r.minchel, "low_chord": r.low_chord,
            "high_chord": r.high_chord, "profiles": dict(r.profiles),
        } for r in db.working],
        "cross-section annotations": [{
            "identity": r.row_identity or str(r.id), "river": r.river,
            "reach": r.reach, "rs": r.rs, "station": r.cumdist,
            "label_letter": r.label_letter, "label_text": r.label_text,
        } for r in db.working],
        "profile mapping and backwater": [{
            "identity": p.profile_id, "selected": bool(p.selected),
            "backwater_elev": p.backwater_elev, "backwater_text": p.backwater_text,
            "display_name": p.display_name, "frequency": p.frequency,
        } for p in db.profiles],
        "revision limits": {
            "start": db.header.revision_start_station,
            "end": db.header.revision_end_station,
        },
        "structures and source units": {
            "source_units": db.source_units,
            "working_units": db.working_units,
            "unit_contract": deepcopy(db.unit_contract),
            "unit_conversion_status": db.unit_conversion_status,
            "structure_inventory": deepcopy(db.structure_inventory),
        },
        "landmarks": [{
            "identity": str(lm.id), "station": lm.distance,
            "elevation": lm.elevation, "text": lm.text,
            "orientation": lm.orientation, "adjusted": bool(lm.adjusted),
        } for lm in db.landmarks],
        "panel definitions and scales": [{
            "identity": str(p.panel), "display_id": p.display_id,
            "start_station": p.start_station, "end_station": p.end_station,
            "start_elevation": p.start_elevation, "end_elevation": p.end_elevation,
            "h_scale": p.h_scale, "v_scale": p.v_scale,
            "panel_x": p.panel_x, "panel_y": p.panel_y,
        } for p in db.panels],
    }


def panel_dependency_digest(db):
    """Stable review dependency evidence, independent of runtime session IDs."""
    def number(value):
        return None if value is None else float(value)
    return fingerprint({
        "stationing": [
            [(r.id, r.river, r.reach, r.rs, number(r.length_chnl),
              r.connected_us_rs, r.connected_ds_rs,
              r.structure_model.source if r.structure_model else None)
             for r in db.working],
            number(db.stationing_start),
        ],
        "working": [(r.id, number(r.cumdist), number(r.minchel), r.profiles,
                     number(r.low_chord), number(r.high_chord), r.label_letter,
                     r.label_text) for r in db.working],
        "profiles": [(p.profile_id, bool(p.selected), number(p.backwater_elev),
                      p.backwater_text, p.display_name, p.frequency,
                      p.mapping_provenance, (p.color or "").upper(), p.linetype,
                      number(p.lineweight), p.legend_text, p.layer) for p in db.profiles],
        "revision": [number(db.header.revision_start_station), number(db.header.revision_end_station)],
        "source_units": db.source_units,
        "working_units": db.working_units,
        "unit_contract": db.unit_contract,
        "unit_conversion_status": db.unit_conversion_status,
        "structure_inventory": db.structure_inventory,
        "header": vars(db.header),
        "use_node_names": bool(db.use_node_names),
        "landmarks": [(lm.id, number(lm.distance), number(lm.elevation), lm.text,
                       lm.orientation, bool(lm.underline), bool(lm.adjusted)) for lm in db.landmarks],
    })


def ordered_panel_definition_digest(panels):
    """Digest saved panel geometry in its deliberate presentation order."""
    return fingerprint([{
        "panel": p.panel, "panel_suffix": p.panel_suffix,
        "start_station": float(p.start_station), "end_station": float(p.end_station),
        "start_elevation": float(p.start_elevation), "end_elevation": float(p.end_elevation),
        "panel_x": None if p.panel_x is None else float(p.panel_x),
        "panel_y": None if p.panel_y is None else float(p.panel_y),
        "h_scale": float(p.h_scale), "v_scale": float(p.v_scale),
    } for p in panels])


def engineering_version(db):
    """Content identity for the complete export input, not a wall-clock version."""
    from dataclasses import asdict
    return fingerprint(asdict(db))


def require_revision(db):
    from .rasplot_db import model_station_extent
    extent = model_station_extent(db.working)
    start, end = db.header.revision_start_station, db.header.revision_end_station
    if start is None and end is None:
        return extent
    if (extent is None or start is None or end is None or
            not all(math.isfinite(v) for v in (start, end)) or
            not extent[0] <= start < end <= extent[1]):
        raise ValueError("Revision Limits require review after stationing changes. Apply limits within the current model extent or explicitly Reset to Full Model.")
    return start, end


def review_panels(db):
    db.panel_signature = panel_inputs(db)
    db.panel_review_dependencies = panel_dependency_categories(db)
    db.panel_review_snapshots = panel_dependency_snapshots(db)
    db.panel_review_state = "REVIEWED" if db.panels else "REVIEW_REQUIRED"


def require_panel_review(db):
    if db.panels and getattr(db, "panel_review_state", "REVIEWED") != "REVIEWED":
        raise ValueError("Saved panels require explicit review before export.")


def mark_panels_review_required(db):
    db.panel_review_state = "REVIEW_REQUIRED"


def panels_stale(db):
    state = getattr(db, "panel_review_state", "UNSPECIFIED")
    return bool(db.panels and (
        state == "REVIEW_REQUIRED"
        or bool(db.panel_signature and db.panel_signature != panel_inputs(db))
    ))


def panel_stale_reasons(db):
    if not panels_stale(db):
        return []
    saved = getattr(db, "panel_review_dependencies", {}) or {}
    if not saved:
        return [{"category": "engineering dependencies", "affected_panels": [p.display_id for p in db.panels],
                 "detail": "Detailed prior-state evidence is unavailable; all saved panels require review."}]
    current = panel_dependency_categories(db)
    prior_snapshots = getattr(db, "panel_review_snapshots", {}) or {}
    current_snapshots = panel_dependency_snapshots(db)
    reasons = []
    for category, digest in saved.items():
        if current.get(category) == digest:
            continue
        previous = prior_snapshots.get(category)
        now = current_snapshots.get(category)
        changes = _snapshot_changes(category, previous, now)
        affected = _affected_panels(db.panels, category, changes)
        detail = ("Review evidence identifies the changes listed below."
                  if changes else
                  "This dependency category changed after panel review; exact field attribution is unavailable.")
        reasons.append({"category": category,
                        "affected_panels": affected or [p.display_id for p in db.panels],
                        "detail": detail, "changes": changes[:20],
                        "additional_change_count": max(0, len(changes)-20)})
    return reasons


def _snapshot_changes(category, previous, current):
    if previous is None:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        return [{"identity": category, "field": key,
                 "previous": previous.get(key), "current": current.get(key)}
                for key in sorted(set(previous) | set(current))
                if previous.get(key) != current.get(key)]
    if not isinstance(previous, list) or not isinstance(current, list):
        return []
    before = {str(item.get("identity")): item for item in previous}
    after = {str(item.get("identity")): item for item in current}
    changes = []
    for identity in sorted(set(before) | set(after)):
        old, new = before.get(identity), after.get(identity)
        if old is None or new is None:
            changes.append({"identity": identity, "field": "record",
                            "previous": old, "current": new,
                            "station": (old or new or {}).get("station")})
            continue
        for field in sorted((set(old) | set(new)) - {"identity", "station"}):
            old_value, new_value = old.get(field), new.get(field)
            if field == "profiles" and isinstance(old_value, dict) and isinstance(new_value, dict):
                for profile_id in sorted(set(old_value) | set(new_value)):
                    if old_value.get(profile_id) != new_value.get(profile_id):
                        changes.append({"identity": identity, "field": f"WSEL {profile_id}",
                                        "previous": old_value.get(profile_id),
                                        "current": new_value.get(profile_id),
                                        "station": new.get("station", old.get("station"))})
            elif old_value != new_value:
                changes.append({"identity": identity, "field": field,
                                "previous": old_value, "current": new_value,
                                "station": new.get("station", old.get("station"))})
    return changes


def _affected_panels(panels, category, changes):
    if category == "panel definitions and scales":
        ids = {str(change.get("identity")) for change in changes}
        return [panel.display_id for panel in panels if str(panel.panel) in ids]
    stations = [change.get("station") for change in changes
                if isinstance(change.get("station"), (int, float))
                and math.isfinite(change["station"])]
    if stations:
        return [panel.display_id for panel in panels
                if any(panel.start_station-1e-8 <= station <= panel.end_station+1e-8
                       for station in stations)]
    return [panel.display_id for panel in panels]


def validate_collection(panels, required=None):
    if not panels or len(panels) > MAX_PANELS:
        raise ValueError(f"Provide between 1 and {MAX_PANELS} panels; increase scale or reduce the requested range.")
    ids = [p.panel for p in panels]
    if any(not isinstance(n, int) or n < 1 for n in ids) or len(set(ids)) != len(ids):
        raise ValueError("Panel IDs must be unique positive integers.")
    for p in panels:
        values = [p.start_station, p.end_station, p.start_elevation, p.end_elevation, p.h_scale, p.v_scale]
        values += [v for v in (p.panel_x, p.panel_y) if v is not None]
        if any(not math.isfinite(v) for v in values):
            raise ValueError(f"Panel {p.panel}: all numeric values must be finite.")
        if p.h_scale <= 0 or p.v_scale <= 0 or p.end_station <= p.start_station or p.end_elevation <= p.start_elevation:
            raise ValueError(f"Panel {p.panel}: scales and station/elevation spans must be positive.")
    if required is None:
        return
    lo, hi = required
    if not all(math.isfinite(v) for v in (lo, hi)) or lo >= hi:
        raise ValueError("Requested station range must be finite and increasing.")
    cursor = lo
    gaps = []
    for p in sorted(panels, key=lambda p: p.start_station):
        start, end = max(lo, p.start_station), min(hi, p.end_station)
        if end <= start:
            continue
        if start > cursor + 1e-7:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < hi - 1e-7:
        gaps.append((cursor, hi))
    if gaps:
        raise ValueError("Panel coverage is incomplete; uncovered station intervals: " + "; ".join(f"{a:g} to {b:g}" for a,b in gaps) + ". Revise panels or explicitly narrow Revision Limits. Overlap is allowed.")
