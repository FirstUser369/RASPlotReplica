"""HTTP API routes for opening HEC-RAS projects and inspecting reaches/profiles.

All routes under the /api prefix.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from ..core.hydraulics import prepare_profiles, resolve_profiles, normalize_value, hydraulic_preflight, profile_label, segments
from ..core.structure_model import sync_rows, models_from_rows, preflight
from ..core.hdf_reader import HdfReader, plan_to_dict, xs_identity, channel_length, AlignmentError, file_fingerprint
from ..core.merge import merge_reaches, compute_cumdist, MergedReach
from ..core.rasplot_db import (
    RasplotDatabase, WorkHeader, WorkingRow, Landmark, PanelDef, ProfileMeta,
    build_db_from_plan, build_working_from_plan, auto_letter, apply_node_names, reletter_from,
    recalc_cumdist, fix_drawdowns, auto_panels, panel_coverage,
    PAGE_SIZES, plot_usable_size, DEFAULT_FREQUENCIES, model_station_extent,
    normalize_panel_suffix,
)

from ..core.local_state import LocalRoute, owned_path
from ..core.errors import AppError, log
from ..core.state_contract import require_stationing, seal_stationing, review_panels, validate_collection, panels_stale
from ..core.model_topology import (
    ModelContext, create_flooding_source, continuous_distances, stable_id,
)

router = APIRouter(prefix="/api", route_class=LocalRoute)


# --- In-memory session store (single-user localhost app) ------------------- #
_SESSIONS: dict[str, dict[str, Any]] = {}
_next_id = 1


def _new_id() -> str:
    return secrets.token_urlsafe(24)


def _engineering_plan(session: dict[str, Any]):
    return session.get("engineering_plan", session["plan"])



def _session_db(sid: str) -> RasplotDatabase:
    """Return (and lazily create) the RasplotDatabase for a session."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    sess = _SESSIONS[sid]
    if "db" not in sess:
        plan = _engineering_plan(sess)
        reach_idx = sess.get("reach_idx")
        if reach_idx is None:
            raise HTTPException(409, "This model contains multiple reaches. Select a reach or construct a validated flooding source before engineering operations.")
        header = sess.get("header")
        sess["db"] = build_db_from_plan(plan, reach_idx=reach_idx, header=header)
    return sess["db"]


def _rebuild_db(sid: str, reach_idx: int = 0) -> RasplotDatabase:
    sess = _SESSIONS[sid]
    plan = _engineering_plan(sess)
    cached = sess.setdefault("reach_databases", {})
    if "db" in sess:
        cached[sess.get("reach_idx", 0)] = sess["db"]
    if reach_idx in cached:
        sess["db"] = cached[reach_idx]
        sess["reach_idx"] = reach_idx
        return cached[reach_idx]
    # A different reach owns its edits, revision limits and manual panels.
    # preserve project metadata and profile style only
    old: RasplotDatabase | None = sess.get("db")
    from copy import deepcopy
    header = deepcopy(old.header) if old else WorkHeader()
    header.revision_start_station = None
    header.revision_end_station = None
    db = build_db_from_plan(plan, reach_idx=reach_idx, header=header)
    if old:
        db.frequencies = old.frequencies
        db.landmarks = []
        db.use_node_names = old.use_node_names
        # carry profile styling / backwater by name
        old_by = {p.profile_id: p for p in old.profiles}
        for p in db.profiles:
            if p.profile_id in old_by:
                o = old_by[p.profile_id]
                p.mapping_provenance = o.mapping_provenance
                p.annual_chance_percent = o.annual_chance_percent
                p.display_name = o.display_name
                p.frequency = o.frequency or p.frequency
                p.color = o.color or p.color
                p.linetype = o.linetype or p.linetype
                p.lineweight = o.lineweight or p.lineweight
                p.legend_text = o.legend_text or p.legend_text
                p.layer = o.layer or p.layer
                p.backwater_elev = o.backwater_elev
                p.backwater_text = o.backwater_text
                p.selected = o.selected
        if old.use_node_names:
            apply_node_names(db.working, plan, reach_idx)
    review_panels(db)
    cached[reach_idx] = db
    sess["db"] = db
    sess["reach_idx"] = reach_idx
    return db


def _rs_float(rs: str) -> float | None:
    try:
        return float(str(rs).strip())
    except Exception:
        return None


def _nearest_xs(rs: str, xs_list: list) -> Any:
    target = _rs_float(rs)
    if target is None or not xs_list:
        return None
    return min(xs_list, key=lambda c: abs((_rs_float(c.rs) or 1e30) - target))


def _index_to_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA (RASPLOT-style sequential labels)."""
    if i < 0:
        return ""
    s = ""
    n = i
    while True:
        s = chr(ord("A") + (n % 26)) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _assign_label_letters(
    xs_list: list,
    struct_rs: list[str],
    every: int = 5,
) -> list[str | None]:
    """Auto-assign letter labels (downstream → upstream) on a subset of XS.

    Marks mouth, freeboard of reach, every Nth XS, and the XS nearest each structure.
    """
    n = len(xs_list)
    if n == 0:
        return []
    marked: set[int] = {0, n - 1}
    # indices every N along upstream-first list
    marked.update(range(0, n, max(1, every)))
    for srs in struct_rs:
        nearest = _nearest_xs(srs, xs_list)
        if nearest is None:
            continue
        for j, xs in enumerate(xs_list):
            if xs is nearest or xs.rs == nearest.rs:
                marked.add(j)
                break
    # Letter order: downstream (low cumdist ≈ high index if upstream-first) first
    # For HEC-RAS order upstream-first, downstream is last index.
    ordered = sorted(marked, reverse=True)
    letters: list[str | None] = [None] * n
    for k, idx in enumerate(ordered):
        letters[idx] = _index_to_letter(k)
    return letters


def _pick_profiles(plan, profile: str | None, profiles: str | None) -> list:
    prepare_profiles(plan.profiles)
    try:
        selections=json.loads(profiles) if profiles and profiles.startswith("[") else [profiles] if profiles else [profile] if profile else []
        if not isinstance(selections,list) or any(not isinstance(v,str) for v in selections):
            raise ValueError("Profile selections must be a JSON array of IDs.")
        return resolve_profiles(plan.profiles,selections) if selections else plan.profiles[:1]
    except (ValueError,TypeError) as exc:
        raise HTTPException(400,str(exc)) from exc


# --------------------------------------------------------------------------- #
# Models                                                                       #
# --------------------------------------------------------------------------- #


class OpenRequest(BaseModel):
    path: str
    model_generation: int = 0


class FloodingSourceRequest(BaseModel):
    display_name: str
    reach_ids: list[str]
    naming_provenance: str = "USER_DEFINED"
    start_cumdist: float = 0.0
    working_lengths: dict[str, float] | None = None


class MergeRequest(BaseModel):
    session_id: str
    order: list[int]
    junction_distances: list[float] | None = None
    start_cumdist: float = 0.0


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #


@router.get("/health")
def health() -> dict:
    from ..release import VERSION, BUILD_ID, RELEASE, PURPOSE
    return {"ok": True, "application": "RAS-Plot Replica", "version": VERSION,
            "release": RELEASE, "purpose": PURPOSE, "build_id": BUILD_ID}


@router.get('/support')
def support() -> dict:
    from ..release import SUPPORT
    return SUPPORT


@router.get("/settings")
def get_settings() -> dict:
    """Return optional local settings (last plan path, FIS defaults)."""
    root = Path(__file__).resolve().parents[3]  # RASPlotReplica/
    candidates: list[Path] = []
    configured_path = os.environ.get("RASPLOT_CONFIG")
    if configured_path:
        candidates.append(Path(configured_path))
    candidates.extend([
        root / "settings.local.json",
        root.parent / "settings.local.json",
    ])
    for c in candidates:
        if c.is_file():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                break
    return {
        "host": "127.0.0.1",
        "port": 8765,
        "last_plan_path": "",
        "default_fis": {},
    }


@router.post("/source/browse")
def browse_source(request: Request) -> dict:
    from urllib.parse import urlsplit
    from ..core.windows_file_picker import choose_plan_hdf
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Browse requires the local Windows application.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Browse must be requested from this application.")
    try:
        return {"path": choose_plan_hdf()}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/source")
def open_source(req: OpenRequest) -> dict:
    """Open a HEC-RAS plan (*.pXX.hdf) file and return the summary."""
    # A newer replacement attempt abandons older uncommitted candidates even
    # when the newer source later proves invalid. Active contexts are retained.
    for old_sid, old_session in list(_SESSIONS.items()):
        old_context = old_session.get("model_context")
        if old_context and old_context.status == "CANDIDATE" and old_context.generation < req.model_generation:
            _SESSIONS.pop(old_sid, None)
    p = Path(req.path)
    if not p.is_file():
        raise AppError(404, 'FILE_ACCESS', 'Plan HDF file not found. Select an existing local plan file.')
    if not re.search(r"\.p[0-9]{2}\.hdf$", p.name, re.IGNORECASE):
        raise HTTPException(400, f"Expected a HEC-RAS plan file like *.pXX.hdf; got {p.name}")
    from ..core.project_file import fingerprint_source, SourceModifiedError
    try:
        source_fingerprint = fingerprint_source(p)
    except (OSError, SourceModifiedError) as exc:
        raise AppError(400, 'FILE_ACCESS', f'Cannot obtain a stable HDF fingerprint: {exc}') from exc
    reader = HdfReader(p)
    try:
        plan = reader.read_plan()
    except OSError as exc:
        log.warning('Unable to open HDF', exc_info=True)
        raise AppError(400, 'FILE_ACCESS', 'Cannot read the HEC-RAS HDF. Check access, close any writer locking the file, and verify the file is a complete, uncorrupted HDF.') from exc
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning('Incompatible HDF source', exc_info=True)
        raise AppError(400, 'UNSUPPORTED_SCHEMA', f'Cannot validate HEC-RAS source: {exc}') from exc
    try:
        parsed_fingerprint = fingerprint_source(p)
    except (OSError, SourceModifiedError) as exc:
        raise AppError(400, 'FILE_ACCESS', f'The HDF source was not stable while opening: {exc}') from exc
    if (parsed_fingerprint.sha256 != source_fingerprint.sha256
            or parsed_fingerprint.size_bytes != source_fingerprint.size_bytes):
        raise AppError(409, 'STALE_STATE', 'The HDF source changed while it was being opened. Close the HEC-RAS writer and try again.')
    sid = _new_id()
    # seed header from settings if present
    header = WorkHeader()
    try:
        settings = get_settings()
        fis = settings.get("default_fis") or {}
        # Project identity belongs to this session, never global sample defaults.
        # Global sample datum is not source evidence for this project.
        header.unit = fis.get("distance_unit", header.unit)
        header.h_scale = float(fis.get("h_scale", header.h_scale))
        header.v_scale = float(fis.get("v_scale", header.v_scale))
        header.page_size = fis.get("page_size", header.page_size)
    except Exception:
        pass
    db = None
    if len(plan.reaches) == 1:
        try:
            db = build_db_from_plan(plan, reach_idx=0, header=header)
        except ValueError as exc:
            raise HTTPException(400, f"Cannot validate working cross sections: {exc}") from exc
    from ..core.local_state import cleanup_sessions, SESSION_LIMIT
    cleanup_sessions()
    if len(_SESSIONS) >= SESSION_LIMIT:
        raise AppError(409, 'STALE_STATE', 'Session capacity reached. Close an unused project or restart after saving required exports.')
    import time
    model_id = getattr(plan, "model_id", "") or ("model-" + parsed_fingerprint.sha256[:20])
    plan.model_id = model_id
    context = ModelContext(model_id, req.model_generation, sid, str(p))
    session = {"plan": plan, "reach_idx": 0 if db is not None else None, "header": header,
               "path": str(p), "source_fingerprint": parsed_fingerprint,
               "model_context": context, "last_access": time.time()}
    if db is not None:
        session["db"] = db
    _SESSIONS[sid] = session
    payload = plan_to_dict(plan)
    payload["session_id"] = sid
    payload["model_context"] = context.to_dict()
    payload["db"] = db.to_dict() if db is not None else None
    payload["requires_reach_selection"] = db is None
    return payload


@router.post("/source/inspect")
def inspect_source(req: OpenRequest) -> dict:
    """Return readable capabilities without claiming authoritative workflow support."""
    p = Path(req.path)
    if not p.is_file():
        raise AppError(404, 'FILE_ACCESS', 'HDF file not found.')
    try:
        capabilities = HdfReader(p).inspect()
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise AppError(400, 'UNSUPPORTED_SCHEMA', f'Cannot inspect HDF source: {exc}') from exc
    return {"path": str(p), "capabilities": capabilities.to_dict()}


@router.get("/session/{sid}")
def get_session(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    return plan_to_dict(_SESSIONS[sid]["plan"])


@router.delete('/session/{sid}')
def close_session(sid: str) -> dict:
    _SESSIONS.pop(sid, None)
    return {'closed': True}


@router.post('/session/{sid}/commit')
def commit_session(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    context = _SESSIONS[sid]["model_context"]
    context.status = "ACTIVE"
    return {"model_context": context.to_dict()}


@router.get("/reaches/{sid}")
def list_reaches(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
    topology = plan.topology
    by_name = {(r.river_name, r.reach_name): r for r in topology.reaches.values()} if topology else {}
    return {
        "model_id": plan.model_id,
        "reaches": [
            {
                "id": by_name[(r.river, r.reach)].id if (r.river, r.reach) in by_name else None,
                "river_id": by_name[(r.river, r.reach)].river_id if (r.river, r.reach) in by_name else None,
                "river": r.river, "reach": r.reach,
                "num_xs": len(r.cross_sections), "num_structures": len(r.structures),
                "xs_count": len(r.cross_sections), "structure_count": len(r.structures),
                "rs_list": [c.rs for c in r.cross_sections],
                "station_extent": [r.cross_sections[-1].rs, r.cross_sections[0].rs] if r.cross_sections else None,
                "upstream_connection": asdict(by_name[(r.river, r.reach)].upstream) if (r.river, r.reach) in by_name else None,
                "downstream_connection": asdict(by_name[(r.river, r.reach)].downstream) if (r.river, r.reach) in by_name else None,
                "topology_status": by_name[(r.river, r.reach)].topology_status if (r.river, r.reach) in by_name else "UNKNOWN",
            }
            for r in plan.reaches
        ],
        "junctions": [asdict(v) for v in topology.junctions.values()] if topology else [],
        "connections": [asdict(v) for v in topology.connections.values()] if topology else [],
    }


@router.get("/model/{sid}/inventory")
def model_inventory(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
    return {
        "model_context": _SESSIONS[sid]["model_context"].to_dict(),
        "model": plan_to_dict(plan),
        **list_reaches(sid),
    }


@router.post("/model/{sid}/flooding-source")
def build_flooding_source(sid: str, req: FloodingSourceRequest) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
    if not plan.topology:
        raise HTTPException(409, "Authoritative reach topology is unavailable for this model.")
    source = create_flooding_source(plan.model_id, req.display_name, req.reach_ids,
                                    plan.topology, req.naming_provenance)
    reaches_by_id = {}
    for reach in plan.reaches:
        match = next((item for item in plan.topology.reaches.values()
                      if item.river_name == reach.river and item.reach_name == reach.reach), None)
        if match:
            reaches_by_id[match.id] = reach
    result = continuous_distances(source, plan.topology, reaches_by_id,
                                  req.start_cumdist, req.working_lengths or {})
    _SESSIONS[sid].setdefault("flooding_sources", {})[source.id] = source
    return {"flooding_source": source.to_dict(), "continuous_distance": result.to_dict()}


@router.post("/model/{sid}/flooding-source/build")
def commit_flooding_source(sid: str, req: FloodingSourceRequest) -> dict:
    """Commit a source-validated path as one continuous engineering workspace."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    from copy import deepcopy
    from ..core.hdf_reader import ReachGeometry

    session = _SESSIONS[sid]
    plan = session["plan"]
    if not plan.topology:
        raise HTTPException(409, "Authoritative reach topology is unavailable for this model.")
    source = create_flooding_source(plan.model_id, req.display_name, req.reach_ids,
                                    plan.topology, req.naming_provenance)
    reaches_by_id = {}
    for reach in plan.reaches:
        match = next((item for item in plan.topology.reaches.values()
                      if item.river_name == reach.river and item.reach_name == reach.reach), None)
        if match:
            reaches_by_id[match.id] = reach
    result = continuous_distances(source, plan.topology, reaches_by_id,
                                  req.start_cumdist, req.working_lengths or {})
    if source.topology_validation_status != "SUPPORTED" or result.status != "SUPPORTED":
        messages = source.validation_messages + result.messages
        raise HTTPException(409, "Flooding source is blocked: " + "; ".join(messages))

    selected = [reaches_by_id[item.reach_id] for item in source.ordered_segments]
    # The domain source order is downstream-to-upstream; the established working
    # table contract is upstream-first.
    cross_sections = [xs for reach in reversed(selected) for xs in reach.cross_sections]
    structures = [item for reach in reversed(selected) for item in reach.structures]
    engineering_plan = deepcopy(plan)
    engineering_plan.reaches = [ReachGeometry(
        river=req.display_name.strip(), reach="Flooding Source", centerline=[],
        cross_sections=deepcopy(cross_sections), structures=deepcopy(structures),
        source_path=str(plan.plan_path),
        source_connectivity={"provenance": "VALIDATED_FLOODING_SOURCE", "source_id": source.id},
    )]
    for profile, source_profile in zip(engineering_plan.profiles, plan.profiles):
        wsel_by_id = source_profile.values_by_identity()
        detail_by_id = dict(zip(source_profile.xs_ids, source_profile.values))
        profile.xs_ids = [xs.identity for xs in cross_sections]
        profile.wsel = [wsel_by_id[identity] for identity in profile.xs_ids]
        profile.values = [deepcopy(detail_by_id[identity]) for identity in profile.xs_ids]
    cumdist = [result.distances[stable_id("xs", plan.model_id, xs.river, xs.reach, xs.rs)]
               for xs in cross_sections]
    header = deepcopy(session.get("header") or WorkHeader())
    db = build_db_from_plan(engineering_plan, reach_idx=0, header=header, cumdist=cumdist)
    db.header.stream_name = req.display_name.strip()
    db.flooding_source = source.to_dict()
    db.distance_ledger = result.to_dict()
    session["engineering_plan"] = engineering_plan
    session["active_flooding_source"] = source
    session["distance_ledger"] = result
    session["db"] = db
    session["reach_idx"] = 0
    session.pop("reach_databases", None)
    return {
        "flooding_source": source.to_dict(),
        "continuous_distance": result.to_dict(),
        "db": db.to_dict(),
    }


@router.get("/profile/{sid}")
def get_profile(
    sid: str,
    profile: str | None = Query(None, description="Stable primary profile ID; default = first"),
    profiles: str | None = Query(None, description="JSON array of stable profile IDs to overlay"),
    reach_idx: int = Query(0),
    label_every: int = Query(5, ge=1, le=100, description="Auto letter every Nth XS"),
) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _engineering_plan(_SESSIONS[sid])
    if reach_idx < 0 or reach_idx >= len(plan.reaches):
        raise HTTPException(400, f"reach_idx out of range (0..{len(plan.reaches) - 1})")
    reach = plan.reaches[reach_idx]
    active_reach = _SESSIONS[sid].get("reach_idx")
    if active_reach is None:
        raise HTTPException(409, "Select a reach or validated flooding source before loading a profile.")
    if not plan.profiles:
        raise HTTPException(400, "No profile results available")
    names = [p.name for p in plan.profiles]
    selected = _pick_profiles(plan, profile, profiles)
    sel = selected[0]
    xs_list = reach.cross_sections

    db: RasplotDatabase | None = None
    working_xs_by_identity: dict[tuple[str, str, str], WorkingRow] = {}
    working_struct_rows: list[WorkingRow] = []
    db = _session_db(sid)
    try:
        require_stationing(db)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    profile_rows = db.working if _SESSIONS[sid].get("reach_idx",0) == reach_idx else build_working_from_plan(plan, reach_idx)
    if _SESSIONS[sid].get("active_flooding_source") is not None:
        distance_by_identity = {xs_identity(row.river, row.reach, row.rs): row.cumdist
                                for row in profile_rows if row.kind == "xs"}
        cumdist = [distance_by_identity[xs.identity] for xs in xs_list]
    else:
        cumdist = compute_cumdist(reach)
    sync_rows(profile_rows)
    for r in profile_rows:
        if r.kind == "xs":
            working_xs_by_identity[xs_identity(r.river, r.reach, r.rs)] = r
        elif r.kind == "struct":
            working_struct_rows.append(r)

    values_by_profile = {p.key: p.values_by_identity() for p in selected}
    letter_list = _assign_label_letters(xs_list, [s.rs for s in reach.structures], every=label_every)
    out_xs = []
    for i, xs in enumerate(xs_list):
        working_row = working_xs_by_identity.get(xs.identity)
        if working_row is not None:
            length_channel = working_row.length_chnl
            cumdist_i = working_row.cumdist
            minchel = working_row.minchel
            label_letter = working_row.label_letter
            label_text = working_row.label_text
            wsel_by_profile = {p.key: working_row.profiles.get(p.key) for p in selected}
        else:
            length_channel = xs.length_channel if i < len(xs_list) - 1 else 0.0
            cumdist_i = cumdist[i]
            minchel = xs.minchel
            label_letter = letter_list[i]
            label_text = None
            wsel_by_profile = {p.key: values_by_profile[p.key][xs.identity] for p in selected}
        main_wsel = wsel_by_profile[sel.key]

        row = {
            "xs_identity": list(xs.identity),
            "xs_id": working_row.source_identity if working_row else stable_id("xs", plan.model_id, xs.river, xs.reach, xs.rs),
            "rs": xs.rs,
            "river": xs.river,
            "reach": xs.reach,
            "length_channel": length_channel,
            "cumdist": cumdist_i,
            "minchel": minchel,
            "wsel": main_wsel,
            "label_letter": label_letter,
            "label_text": label_text,
            "wsel_by_profile": wsel_by_profile,
            "hydraulic_values": working_row.hydraulic_values if working_row else {},
            "adjustments": working_row.adjustments if working_row else [],
            "ground_provenance": xs.ground_provenance,
        }
        out_xs.append(row)

    out_struct = []
    if db is not None and working_struct_rows:
        for row in working_struct_rows:
            out_struct.append({
                "structure_id": row.structure_model.identity if row.structure_model else None,
                "row_identity": row.row_identity,
                "support_status": row.structure_model.status if row.structure_model else "INVALID",
                "support_reason": row.structure_model.reason if row.structure_model else "Missing normalized source",
                "occupied_interval": row.structure_model.occupied_interval if row.structure_model else None,
                "containing_interval": row.structure_model.bounds if row.structure_model else None,
                "geometry_role": "FACE" if row.structure_model and row.structure_model.type == "CULVERT" else "ASSOCIATION_ONLY",
                "hydraulic_values": row.structure_model.hydraulic_values if row.structure_model else {},
                "river": row.river,
                "reach": row.reach,
                "rs": row.rs,
                "type": row.struc,
                "name": row.label_text or row.struc or row.rs,
                "weir_width": row.weir_width if row.weir_width is not None else row.length_chnl,
                "low_chord": row.low_chord,
                "high_chord": row.high_chord,
                "minchel": row.minchel,
                "cumdist": row.cumdist,
                "connected_us_rs": row.connected_us_rs or None,
                "profile_data": row.structure_profile_data,
                "hw_connectivity": row.hw_connectivity,
                "tw_connectivity": row.tw_connectivity,
                "lateral_hw_distance": row.lateral_hw_distance,
                "lateral_tw_distance": row.lateral_tw_distance,
                "connected_ds_rs": row.connected_ds_rs or None,
                "culvert_us_gap": row.culvert_us_gap,
                "culvert_barrel_length": row.culvert_barrel_length,
                "culvert_ds_residual": row.culvert_ds_residual,
                "structure_partition_valid": row.structure_partition_valid,
                "structure_partition_warning": row.structure_partition_warning or None,
            })
    return {
        "session_id": sid,
        "reach": {"river": reach.river, "reach": reach.reach},
        "vertical_datum": db.header.vertical_datum or "UNKNOWN",
        "datum_provenance": db.header.datum_provenance,
        "source_units": db.source_units,
        "profile": sel.key,
        "profiles_selected": [p.profile_id for p in selected],
        "profile_segments": {p.profile_id: list(segments([(r["cumdist"], r["wsel_by_profile"][p.profile_id]) for r in out_xs])) for p in selected},
        "profile_metadata": [{**vars(p),"legend_text":profile_label(p,profile_rows)} for p in db.profiles],
        "hydraulic_preflight": hydraulic_preflight(db,[p for p in db.profiles if p.profile_id in {q.profile_id for q in selected}]),
        "profile_names": names,
        "cross_sections": out_xs,
        "structure_models": [m.to_dict() for m in models_from_rows(profile_rows)],
        "structures": out_struct,
    }


@router.post("/merge")
def post_merge(req: MergeRequest) -> dict:
    if req.session_id not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[req.session_id]["plan"]
    try:
        merged = merge_reaches(plan.reaches, req.order, req.junction_distances,
                               req.start_cumdist)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # attach to session for later use
    _SESSIONS[req.session_id]["merged"] = merged
    return {
        "label": merged.label,
        "rivers": merged.rivers,
        "reaches": merged.reaches,
        "num_xs": len(merged.cross_sections),
        "num_structs": len(merged.structures),
        "junctions": merged.junctions,
        "cumdist": merged.cumdist,
    }


@router.get("/merged/{sid}")
def get_merged(sid: str, profile: str | None = Query(None)) -> dict:
    if sid not in _SESSIONS or "merged" not in _SESSIONS[sid]:
        raise HTTPException(404, "No merged reach in session")
    m: MergedReach = _SESSIONS[sid]["merged"]
    plan = _engineering_plan(_SESSIONS[sid])
    sel = plan.profiles[0]
    if profile:
        try:
            sel = resolve_profiles(plan.profiles, [profile])[0]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    out_xs = []
    for i, xs in enumerate(m.cross_sections):
        out_xs.append({
            "rs": xs.rs, "river": xs.river, "reach": xs.reach,
            "cumdist": m.cumdist[i], "minchel": xs.minchel,
            "wsel": sel.values_by_identity()[xs.identity],
            "label_letter": None, "label_text": None,
        })
    return {
        "label": m.label,
        "cumdist": m.cumdist,
        "cross_sections": out_xs,
        "structures": [
            {
                "river": s.river, "reach": s.reach, "rs": s.rs, "type": s.type, "name": s.name,
                "low_chord": s.low_chord, "high_chord": s.high_chord, "minchel": s.minchel,
            } for s in m.structures
        ],
        "junctions": m.junctions,
        "profile": sel.key,
        "profile_names": [p.name for p in plan.profiles],
    }


@router.get("/map/{sid}")
def geo(sid: str) -> dict:
    """Return GeoJSON of reaches (centerline + cross-sections + structures)."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _engineering_plan(_SESSIONS[sid])
    features: list[dict] = []
    for r in plan.reaches:
        if r.centerline:
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": r.centerline},
                "properties": {"kind": "reach", "river": r.river, "reach": r.reach},
            })
        for xs in r.cross_sections:
            if xs.polypoints:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": xs.polypoints},
                    "properties": {"kind": "cross_section", "rs": xs.rs,
                                    "river": xs.river, "reach": xs.reach,
                                    "xs_id": stable_id("xs", plan.model_id, xs.river, xs.reach, xs.rs)},
                })
        for s in r.structures:
            if s.poly_points:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": s.poly_points},
                    "properties": {"kind": "structure", "type": s.type, "rs": s.rs,
                                    "name": s.name, "river": s.river, "reach": s.reach},
                })
    return {"type": "FeatureCollection", "features": features}


@router.get("/source/{sid}/download")
def download_artifact(sid: str, path: str) -> Any:
    """Download a generated artifact written under the session temp cache."""
    import tempfile

    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    p = owned_path(sid, path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    ext = p.suffix.lower()
    media = {
        ".pdf": "application/pdf",
        ".dxf": "application/dxf",
        ".zip": "application/zip",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")
    from ..core.local_state import OwnedDownload
    return OwnedDownload(p, filename=p.name, media_type=media)


# --------------------------------------------------------------------------- #
# RASPLOT database CRUD                                                         #
# --------------------------------------------------------------------------- #


class WorkingPatch(BaseModel):
    id: int
    xs_id: str | None = None
    label_letter: str | None = None
    label_text: str | None = None
    length_chnl: float | None = None
    minchel: float | None = None
    low_chord: float | None = None
    high_chord: float | None = None
    profiles: dict[str, float | None] | None = None


class ReletterRequest(BaseModel):
    start_row_id: int
    start_letter: str = "A"


class CumdistRequest(BaseModel):
    start: float = 0.0


class NodeNamesRequest(BaseModel):
    enabled: bool = True


class HeaderUpdate(BaseModel):
    community: str | None = None
    county: str | None = None
    state: str | None = None
    stream_name: str | None = None
    stream_distance_label: str | None = None
    unit: str | None = None
    vertical_datum: str | None = None
    require_known_datum: bool | None = None
    start_panel_no: int | None = None
    h_scale: float | None = None
    v_scale: float | None = None
    page_size: str | None = None
    table_name: str | None = None
    table_title: str | None = None


class RevisionLimitsUpdate(BaseModel):
    revision_start_station: float
    revision_end_station: float


class ProfileMetaUpdate(BaseModel):
    display_name: str | None = None
    name: str = ""
    profile_id: str | None = None
    frequency: str | None = None
    color: str | None = None
    linetype: str | None = None
    lineweight: float | None = None
    legend_text: str | None = None
    layer: str | None = None
    selected: bool | None = None
    backwater_elev: float | None = None
    backwater_text: str | None = None


class LandmarkIn(BaseModel):
    distance: float
    elevation: float
    text: str
    orientation: str = "Vertical"
    underline: bool = False
    id: int | None = None


class PanelIn(BaseModel):
    panel: int
    start_station: float
    end_station: float
    start_elevation: float
    end_elevation: float
    panel_x: float | None = None
    panel_y: float | None = None
    h_scale: float = 500.0
    v_scale: float = 5.0
    panel_suffix: str | None = None

    @field_validator("panel_suffix")
    @classmethod
    def validate_panel_suffix(cls, value: str | None) -> str | None:
        return None if value is None else normalize_panel_suffix(value)


class PanelsReplace(BaseModel):
    expected_engineering_version: str | None = None
    panels: list[PanelIn]
    auto: bool = False
    draft: bool = False
    h_scale: float | None = None
    v_scale: float | None = None
    page_size: str | None = None


class ReachSelect(BaseModel):
    reach_idx: int = 0
    order: list[int] | None = None
    junction_distances: list[float] | None = None
    start_cumdist: float = 0.0


@router.get("/db/{sid}")
def get_db(sid: str) -> dict:
    return _session_db(sid).to_dict()


@router.get("/db/{sid}/working")
def get_working(sid: str) -> dict:
    db = _session_db(sid)
    from dataclasses import asdict
    return {"rows": [asdict(r) for r in db.working]}


@router.patch("/db/{sid}/working")
def patch_working(sid: str, patches: list[WorkingPatch]) -> dict:
    db = _session_db(sid)
    by_id = {r.id: r for r in db.working}
    # Validate the whole patch batch before mutating any rows.
    xs_rows = [row for row in db.working if row.kind == "xs"]
    for patch in patches:
        row = by_id.get(patch.id)
        if row is None:
            raise HTTPException(400, f"Unknown working row {patch.id}")
        if patch.xs_id is not None and (row.kind != "xs" or row.source_identity != patch.xs_id):
            raise HTTPException(409, "Working-row identity no longer matches this cross section. Reload the profile before editing.")
        if row.kind == "struct" and patch.model_fields_set - {"id","label_letter","label_text"}:
            raise HTTPException(400, "Structure geometry/hydraulics are source-derived; direct working-table edits cannot override normalized engineering evidence.")
        if "length_chnl" in patch.model_fields_set:
            try:
                value = channel_length(patch.length_chnl, f"Working row {patch.id}")
            except AlignmentError as exc:
                raise HTTPException(400, str(exc)) from exc
            if row.kind != "xs":
                raise HTTPException(400, "Structure rows do not own editable ordinary-XS intervals.")
            row_index = xs_rows.index(row)
            next_row = xs_rows[row_index + 1] if row_index + 1 < len(xs_rows) else None
            is_reach_endpoint = next_row is None or (next_row.river, next_row.reach) != (row.river, row.reach)
            if is_reach_endpoint and value != 0:
                raise HTTPException(400, "A downstream reach endpoint has no within-reach interval; its working length must remain zero. Junction distance is retained separately in the authoritative ledger.")
        for field in ("minchel","low_chord","high_chord"):
            value=getattr(patch,field)
            if value is not None and not math.isfinite(value):
                raise HTTPException(400,f"{field} must be finite or cleared.")
        if patch.profiles is not None:
            try:
                resolve_profiles(db.profiles,list(patch.profiles))
                if any(v is not None and not math.isfinite(v) for v in patch.profiles.values()):
                    raise ValueError("Working WSEL must be finite or null; no nonfinite edits are accepted.")
            except ValueError as exc:
                raise HTTPException(400,str(exc)) from exc
    for p in patches:
        r = by_id.get(p.id)
        if not r:
            continue
        if p.label_letter is not None:
            r.label_letter = p.label_letter or None
        if p.label_text is not None:
            r.label_text = p.label_text or None
        if p.length_chnl is not None:
            r.length_chnl = p.length_chnl
        if "minchel" in p.model_fields_set:
            r.ground_provenance={"state":"VALID" if p.minchel is not None else "MISSING","method":"USER_ENTERED","source":r.ground_provenance,"original_value":r.minchel,"reason":"User edited working ground"}
            r.minchel = p.minchel
        if p.low_chord is not None:
            r.low_chord = p.low_chord
        if p.high_chord is not None:
            r.high_chord = p.high_chord
        if p.profiles is not None:
            for selector,value in p.profiles.items():
                meta=resolve_profiles(db.profiles,[selector])[0]
                key=meta.profile_id
                normalized=normalize_value(value)
                r.adjustments.append({"profile_id":meta.profile_id,"original_value":r.profiles.get(key),"adjusted_value":normalized["value"],"reason":"USER_EDIT","provenance":"USER_ENTERED"})
                r.profiles[key]=normalized["value"]
                r.hydraulic_values[meta.profile_id]={**r.hydraulic_values.get(meta.profile_id,{}),"working_state":normalized["state"],"working_provenance":"USER_ENTERED"}
    return {"ok": True, "n": len(patches)}


@router.post("/db/{sid}/fix-drawdowns")
def post_fix_drawdowns(sid: str) -> dict:
    db = _session_db(sid)
    names = [p.profile_id for p in db.profiles]
    xs = [row for row in db.working if row.kind == "xs"]
    before = {(row.source_identity, name): row.profiles.get(name) for row in xs for name in names}
    fix_drawdowns(db.working, names)
    changed = [(row, name) for row in xs for name in names
               if row.profiles.get(name) != before[(row.source_identity, name)]]
    affected = {name for _row, name in changed}
    return {
        "ok": True,
        "profiles_evaluated": len(names),
        "cross_sections_evaluated": len(xs),
        "wsel_adjustments_made": len(changed),
        "affected_profiles": [name for name in names if name in affected],
        "no_op": not changed,
    }


@router.post("/db/{sid}/reletter")
def post_reletter(sid: str, req: ReletterRequest) -> dict:
    db = _session_db(sid)
    reletter_from(db.working, req.start_row_id, req.start_letter)
    return {"ok": True}


@router.post("/db/{sid}/recalc-cumdist")
def post_recalc_cumdist(sid: str, req: CumdistRequest) -> dict:
    db = _session_db(sid)
    try:
        session = _SESSIONS[sid]
        source = session.get("active_flooding_source")
        if source is None:
            recalc_cumdist(db.working, start=req.start)
        else:
            plan = session["plan"]
            if not plan.topology:
                raise AlignmentError("Validated flooding-source topology is unavailable; CUMDIST was not changed.")
            reaches_by_id = {}
            for reach in plan.reaches:
                normalized = next((item for item in plan.topology.reaches.values()
                                   if item.river_name == reach.river and item.reach_name == reach.reach), None)
                if normalized:
                    reaches_by_id[normalized.id] = reach
            working_lengths = {row.source_identity: row.length_chnl for row in db.working
                               if row.kind == "xs" and row.source_identity}
            result = continuous_distances(source, plan.topology, reaches_by_id,
                                          req.start, working_lengths)
            if result.status != "SUPPORTED":
                raise AlignmentError("Continuous flooding-source stationing is blocked: " + "; ".join(result.messages))
            expected = {row.source_identity for row in db.working if row.kind == "xs"}
            if not expected or set(result.distances) != expected:
                raise AlignmentError("Continuous flooding-source stationing identities do not match the working table; CUMDIST was not changed.")
            for row in db.working:
                if row.kind == "xs":
                    row.cumdist = result.distances[row.source_identity]
            sync_rows(db.working)
            session["distance_ledger"] = result
            db.distance_ledger = result.to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    revision = _reconcile_revision_limits(db)
    station_range = (
        (revision["revision_start_station"], revision["revision_end_station"])
        if revision["explicit"] else None
    )
    db.stationing_start = req.start
    seal_stationing(db)
    return {"ok": True, "panels": len(db.panels), "revision_limits": revision}


@router.post("/db/{sid}/auto-letter")
def post_auto_letter(sid: str, every: int = Query(5, ge=1, le=100)) -> dict:
    db = _session_db(sid)
    xs = [row for row in db.working if row.kind == "xs"]
    before = {row.source_identity: row.label_letter for row in xs}
    for r in xs:
        r.label_letter = None
    auto_letter(db.working, every=every)
    changed = sum(row.label_letter != before[row.source_identity] for row in xs)
    assigned = sum(bool(row.label_letter) for row in xs)
    return {
        "ok": True,
        "cross_sections_evaluated": len(xs),
        "letters_assigned": assigned,
        "letters_changed": changed,
        "no_op": changed == 0,
    }


@router.post("/db/{sid}/node-names")
def post_node_names(sid: str, req: NodeNamesRequest) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = _session_db(sid)
    db.use_node_names = req.enabled
    if req.enabled:
        apply_node_names(db.working, _engineering_plan(_SESSIONS[sid]), _SESSIONS[sid].get("reach_idx", 0))
    return {"ok": True, "use_node_names": db.use_node_names}


@router.get("/db/{sid}/header")
def get_header(sid: str) -> dict:
    from dataclasses import asdict
    return asdict(_session_db(sid).header)


@router.put("/db/{sid}/header")
def put_header(sid: str, req: HeaderUpdate) -> dict:
    from dataclasses import asdict
    db = _session_db(sid)
    h = db.header
    for name in ("h_scale", "v_scale"):
        value = getattr(req, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise HTTPException(400, f"{name} must be finite and positive.")
    if "vertical_datum" in req.model_fields_set:
        h.vertical_datum=(req.vertical_datum or "").strip() or "UNKNOWN"
        h.datum_provenance="USER_ENTERED" if h.vertical_datum!="UNKNOWN" else "UNKNOWN"
    for k, v in req.model_dump(exclude_none=True).items():
        if k=="vertical_datum": continue
        setattr(h, k, v)
    return asdict(h)


def _revision_limits_response(db: RasplotDatabase) -> dict:
    extent = model_station_extent(db.working)
    if extent is None:
        raise HTTPException(400, "No model station data is available.")
    model_start, model_end = extent
    explicit = (
        db.header.revision_start_station is not None
        and db.header.revision_end_station is not None
    )
    return {
        "revision_start_station": (
            db.header.revision_start_station if explicit else model_start
        ),
        "revision_end_station": (
            db.header.revision_end_station if explicit else model_end
        ),
        "model_start_station": model_start,
        "model_end_station": model_end,
        "explicit": explicit,
        "review_required": bool(explicit and not model_start <= db.header.revision_start_station < db.header.revision_end_station <= model_end),
    }


def _reconcile_revision_limits(db: RasplotDatabase) -> dict:
    """Retain explicit user limits; never silently expand/reset the requested range."""
    extent = model_station_extent(db.working)
    if extent is None:
        raise HTTPException(400, "No model station data is available.")
    model_start, model_end = extent
    start = db.header.revision_start_station
    end = db.header.revision_end_station
    result = _revision_limits_response(db)
    result['review_required'] = start is not None and end is not None and not model_start <= start < end <= model_end
    return result


@router.get("/db/{sid}/revision-limits")
def get_revision_limits(sid: str) -> dict:
    return _revision_limits_response(_session_db(sid))


@router.put("/db/{sid}/revision-limits")
def put_revision_limits(sid: str, req: RevisionLimitsUpdate) -> dict:
    db = _session_db(sid)
    try:
        require_stationing(db)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    extent = model_station_extent(db.working)
    if extent is None:
        raise HTTPException(400, "No model station data is available.")
    model_start, model_end = extent
    start = float(req.revision_start_station)
    end = float(req.revision_end_station)
    if not math.isfinite(start) or not math.isfinite(end):
        raise HTTPException(400, "Revision limits must be finite numbers.")
    if start >= end:
        raise HTTPException(400, "Revision start station must be less than end station.")
    if start < model_start or end > model_end:
        raise HTTPException(400, "Revision limits must fall within the model extent.")
    db.header.revision_start_station = start
    db.header.revision_end_station = end
    return _revision_limits_response(db)


@router.delete("/db/{sid}/revision-limits")
def delete_revision_limits(sid: str) -> dict:
    db = _session_db(sid)
    db.header.revision_start_station = None
    db.header.revision_end_station = None
    return _revision_limits_response(db)


@router.get("/db/{sid}/profiles")
def get_profiles_meta(sid: str) -> dict:
    from dataclasses import asdict
    return {"profiles": [asdict(p) for p in _session_db(sid).profiles]}


@router.put("/db/{sid}/profiles")
def put_profiles_meta(sid: str, updates: list[ProfileMetaUpdate]) -> dict:
    db = _session_db(sid)
    changes=[]
    for u in updates:
        try:
            p=resolve_profiles(db.profiles,[u.profile_id])[0]
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        data=u.model_dump(exclude_unset=True)
        if data.get("color") is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", data["color"]):
            raise HTTPException(400,"Profile color must be #RRGGBB.")
        if data.get("linetype") is not None and data["linetype"] not in {"CONTINUOUS","DASHED","DOTTED","DASHDOT","CENTER","HIDDEN","PHANTOM"}:
            raise HTTPException(400,"Unsupported profile linetype.")
        data.pop("name",None);data.pop("profile_id",None)
        if data.get("backwater_elev") is not None and not math.isfinite(data["backwater_elev"]):
            raise HTTPException(400,"Backwater must be finite or null to clear.")
        if data.get("lineweight") is not None and (not math.isfinite(data["lineweight"]) or data["lineweight"]<=0):
            raise HTTPException(400,"Profile lineweight must be finite and positive.")
        if "frequency" in data:
            frequency=data["frequency"] or ""
            if p.result_type=="DERIVED_MAXIMUM_ENVELOPE" and frequency:
                raise HTTPException(400,"A derived saved-timestep maximum cannot be mapped to annual-chance frequency.")
            style=next((f for f in db.frequencies if f["frequency"]==frequency),None)
            if frequency and not style:
                raise HTTPException(400,"Unknown frequency mapping; choose an explicit supported mapping.")
            data.update({k:style[k] for k in ("legend_text","layer","color","linetype","lineweight") if style and k in style})
            if not frequency:
                data.update(legend_text=p.name,layer=f"PROF_{p.profile_id}",color="#196bd6",linetype="CONTINUOUS",lineweight=.3)
            data["annual_chance_percent"]=float(frequency[:-1]) if frequency in {"0.2%","1%","2%","4%","10%","20%","50%"} else None
            data["frequency"]=frequency
            data["mapping_provenance"]="USER_ENTERED" if frequency else "UNKNOWN"
        if data.get("display_name") and not data.get("frequency",p.frequency):
            data["legend_text"]=data["display_name"]
        _validate_style(data)
        changes.append((p,data))
    for p,data in changes:
        for k,v in data.items():
            if v is not None or k in ("backwater_elev","annual_chance_percent"): setattr(p,k,v)
    return {"ok":True}


@router.get("/db/{sid}/hydraulic-preflight")
def get_hydraulic_preflight(sid: str):
    db=_session_db(sid)
    return hydraulic_preflight(db,[p for p in db.profiles if p.selected] or db.profiles[:1],db.header.revision_start_station,db.header.revision_end_station)


@router.get("/db/{sid}/frequencies")
def get_frequencies(sid: str) -> dict:
    return {"frequencies": _session_db(sid).frequencies}


@router.put("/db/{sid}/frequencies")
def put_frequencies(sid: str, frequencies: list[dict]) -> dict:
    db = _session_db(sid)
    for item in frequencies:
        _validate_style(item)
    db.frequencies = frequencies
    return {"ok": True}


def _validate_style(data):
    color = data.get("color")
    if color is not None and (not isinstance(color,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",color)):
        raise HTTPException(400,"Profile color must be #RRGGBB.")
    layer = data.get("layer")
    if layer is not None and (not isinstance(layer,str) or not layer or re.search(r'[<>/\\":;?*|=\x00-\x1f]',layer)):
        raise HTTPException(400,"DXF layer must be nonempty and contain no CAD-reserved characters.")
    linetype = data.get("linetype")
    if linetype is not None and linetype not in {"CONTINUOUS","DASHED","DOTTED","DASHDOT","CENTER","HIDDEN","PHANTOM"}:
        raise HTTPException(400,"Unsupported profile linetype.")
    weight = data.get("lineweight")
    if weight is not None and (not isinstance(weight,(int,float)) or not math.isfinite(weight) or weight <= 0):
        raise HTTPException(400,"Lineweight must be finite and positive.")


@router.get("/db/{sid}/landmarks")
def get_landmarks(sid: str) -> dict:
    from dataclasses import asdict
    return {"landmarks": [asdict(l) for l in _session_db(sid).landmarks]}


@router.put("/db/{sid}/landmarks")
def put_landmarks(sid: str, items: list[LandmarkIn]) -> dict:
    db = _session_db(sid)
    out: list[Landmark] = []
    nid = db.next_landmark_id
    for it in items:
        lid = it.id if it.id is not None else nid
        if it.id is None:
            nid += 1
        out.append(Landmark(
            id=lid, distance=it.distance, elevation=it.elevation,
            text=it.text, orientation=it.orientation, underline=it.underline,
        ))
    db.landmarks = out
    db.next_landmark_id = max([l.id for l in out], default=0) + 1
    return {"ok": True, "n": len(out)}


@router.get("/db/{sid}/panels")
def get_panels(sid: str) -> dict:
    return {"panels": [p.to_dict() for p in _session_db(sid).panels]}


@router.get("/db/{sid}/auto-end")
def get_auto_end(sid: str, start_station: float = Query(...)) -> dict:
    db = _session_db(sid)
    try:
        require_stationing(db)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if not math.isfinite(start_station):
        raise HTTPException(400, "Start station must be numeric.")

    cumdists = [
        float(row.cumdist)
        for row in db.working
        if row.kind == "xs" and math.isfinite(float(row.cumdist))
    ]
    if not cumdists:
        raise HTTPException(400, "No profile station data is available.")

    available_min = min(cumdists)
    available_max = max(cumdists)
    if start_station < available_min:
        raise HTTPException(400, "Start station is below the available profile range.")
    if start_station >= available_max:
        raise HTTPException(400, "Start station must be below the available profile maximum.")

    h_scale = float(db.header.h_scale)
    if not math.isfinite(h_scale) or h_scale <= 0:
        raise HTTPException(400, "Horizontal scale must be greater than zero.")

    page_size = db.header.page_size
    if not page_size or page_size not in PAGE_SIZES:
        raise HTTPException(400, "Page size is missing or unsupported.")

    coverage = panel_coverage(h_scale, page_size)
    if not math.isfinite(coverage) or coverage <= 0:
        raise HTTPException(400, "Calculated panel coverage must be greater than zero.")

    return {
        "start_station": start_station,
        "end_station": min(available_max, start_station + coverage),
        "model_end_station": available_max,
        "h_scale": h_scale,
        "page_size": page_size,
        "usable_width_in": 13.0,
        "panel_coverage": coverage,
    }


@router.put("/db/{sid}/panels")
def put_panels(sid: str, req: PanelsReplace) -> dict:
    db = _session_db(sid)
    from ..core.state_contract import engineering_version
    if req.expected_engineering_version is not None and req.expected_engineering_version != engineering_version(db):
        raise HTTPException(409, 'Engineering state changed since Panel Setup opened. Reopen Panel Setup and review the current state before Apply.')
    try:
        require_stationing(db)
        from ..core.state_contract import require_revision
        station_range = require_revision(db)
        saved_suffixes = {panel.panel: panel.panel_suffix for panel in db.panels}
        if req.auto:
            panels = auto_panels(db.working,
                h_scale=req.h_scale if req.h_scale is not None else db.header.h_scale,
                v_scale=req.v_scale if req.v_scale is not None else db.header.v_scale,
                page_size=req.page_size or db.header.page_size, station_range=station_range)
            requested_suffixes = {
                item.panel: (item.panel_suffix if item.panel_suffix is not None
                             else saved_suffixes.get(item.panel, ""))
                for item in req.panels
            }
            for panel in panels:
                if panel.panel in requested_suffixes:
                    panel.panel_suffix = requested_suffixes[panel.panel]
        else:
            panels = []
            for item in req.panels:
                values = item.model_dump()
                if item.panel_suffix is None:
                    values["panel_suffix"] = saved_suffixes.get(item.panel, "")
                panels.append(PanelDef(**values))
        validate_collection(panels, station_range)
        from ..core.export_geometry import plot_dimensions, page_dimensions
        page = page_dimensions(req.page_size or db.header.page_size)
        for panel in panels:
            plot_dimensions(panel, page)
        if not req.draft:
            db.panels = panels
            review_panels(db)
        return {"panels": [p.to_dict() for p in panels], "draft": req.draft, "panels_stale": panels_stale(db)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc



@router.post("/db/{sid}/select-reach")
def select_reach(sid: str, req: ReachSelect) -> dict:
    """Rebuild working table for a reach, or for a multi-reach merge order."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    sess = _SESSIONS[sid]
    plan = sess["plan"]
    if req.order and len(req.order) > 1:
        try:
            merged = merge_reaches(plan.reaches, req.order, req.junction_distances,
                                   req.start_cumdist)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # Multi-reach construction is blocked in the data model until source
        # junction semantics are verified; do not create a divergent fake plan.
        raise HTTPException(400, "Continuous multi-reach selection is unsupported.")
    if req.reach_idx < 0 or req.reach_idx >= len(plan.reaches):
        raise HTTPException(400, "Reach index out of range")
    db = _rebuild_db(sid, reach_idx=req.reach_idx)
    return db.to_dict()



@router.get("/db/{sid}/structure-preflight")
def structure_preflight(sid: str, start: float | None = None, end: float | None = None) -> dict:
    db = _session_db(sid)
    try:
        require_stationing(db)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    extent = model_station_extent(db.working)
    if extent is None:
        raise HTTPException(400,"No ordinary cross-section range is available.")
    lo = start if start is not None else (db.header.revision_start_station if db.header.revision_start_station is not None else extent[0])
    hi = end if end is not None else (db.header.revision_end_station if db.header.revision_end_station is not None else extent[1])
    if not math.isfinite(lo) or not math.isfinite(hi) or lo >= hi:
        raise HTTPException(400,"Structure preflight requires finite increasing station limits.")
    records = preflight(db.working,lo,hi)
    return {"structures":records,"inventory":db.structure_inventory,"can_export":db.structure_inventory.get("state") in ("VERIFIED_NONE","VERIFIED_PARSED") and not any(r["affects_range"] and r["status"] != "VALIDATED" for r in records)}
