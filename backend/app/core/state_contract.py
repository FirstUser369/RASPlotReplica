"""Dependency evidence for explicit stationing and saved-panel review."""
import hashlib
import json
import math

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
                        db.source_units, db.structure_inventory,
                        [(lm.distance, lm.elevation) for lm in db.landmarks]])


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
