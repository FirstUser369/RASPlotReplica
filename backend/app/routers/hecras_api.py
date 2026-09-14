"""HTTP API routes for opening HEC-RAS projects and inspecting reaches/profiles.

All routes under the /api prefix.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core.hdf_reader import HdfReader, plan_to_dict
from ..core.merge import merge_reaches, compute_cumdist, MergedReach
from ..core.rasplot_db import (
    RasplotDatabase, WorkHeader, WorkingRow, Landmark, PanelDef, ProfileMeta,
    build_db_from_plan, auto_letter, apply_node_names, reletter_from,
    recalc_cumdist, fix_drawdowns, auto_panels, panel_coverage,
    PAGE_SIZES, plot_usable_size, DEFAULT_FREQUENCIES,
)

router = APIRouter(prefix="/api")


# --- In-memory session store (single-user localhost app) ------------------- #
_SESSIONS: dict[str, dict[str, Any]] = {}
_next_id = 1


def _new_id() -> str:
    global _next_id
    sid = f"s{_next_id}"
    _next_id += 1
    return sid


def _session_db(sid: str) -> RasplotDatabase:
    """Return (and lazily create) the RasplotDatabase for a session."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    sess = _SESSIONS[sid]
    if "db" not in sess:
        plan = sess["plan"]
        reach_idx = sess.get("reach_idx", 0)
        header = sess.get("header")
        sess["db"] = build_db_from_plan(plan, reach_idx=reach_idx, header=header)
    return sess["db"]


def _rebuild_db(sid: str, reach_idx: int = 0) -> RasplotDatabase:
    sess = _SESSIONS[sid]
    plan = sess["plan"]
    # preserve header / frequencies / landmarks where possible
    old: RasplotDatabase | None = sess.get("db")
    header = old.header if old else WorkHeader()
    db = build_db_from_plan(plan, reach_idx=reach_idx, header=header)
    if old:
        db.frequencies = old.frequencies
        db.landmarks = old.landmarks
        db.use_node_names = old.use_node_names
        # carry profile styling / backwater by name
        old_by = {p.name: p for p in old.profiles}
        for p in db.profiles:
            if p.name in old_by:
                o = old_by[p.name]
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


def _structure_cumdist(rs: str, xs_list: list, cumdist: list[float]) -> float | None:
    """Interpolate cumulative channel distance at a structure river station."""
    target = _rs_float(rs)
    if target is None or not xs_list or not cumdist:
        return None
    pairs = []
    for i, xs in enumerate(xs_list):
        rf = _rs_float(xs.rs)
        if rf is None:
            continue
        pairs.append((rf, cumdist[i]))
    if not pairs:
        return None
    # Prefer exact-ish match
    for rf, cd in pairs:
        if abs(rf - target) < 0.05:
            return cd
    # Linear interpolate between surrounding stations (RS rises upstream)
    ordered = sorted(pairs, key=lambda p: p[0])
    if target <= ordered[0][0]:
        return ordered[0][1]
    if target >= ordered[-1][0]:
        return ordered[-1][1]
    for (r0, c0), (r1, c1) in zip(ordered, ordered[1:]):
        if r0 <= target <= r1:
            if r1 == r0:
                return c0
            t = (target - r0) / (r1 - r0)
            return c0 + t * (c1 - c0)
    # Fallback nearest
    rf, cd = min(ordered, key=lambda p: abs(p[0] - target))
    return cd


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
    """Resolve profile selection: multi via comma-list, or single, or default first."""
    by_name = {p.name: p for p in plan.profiles}
    names: list[str] = []
    if profiles:
        names = [x.strip() for x in profiles.split(",") if x.strip()]
    elif profile:
        names = [profile]
    else:
        names = [plan.profiles[0].name] if plan.profiles else []
    out = []
    for name in names:
        if name in by_name:
            out.append(by_name[name])
    if not out and plan.profiles:
        out = [plan.profiles[0]]
    return out


# --------------------------------------------------------------------------- #
# Models                                                                       #
# --------------------------------------------------------------------------- #


class OpenRequest(BaseModel):
    path: str


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
    return {"ok": True}


@router.get("/settings")
def get_settings() -> dict:
    """Return optional local settings (last plan path, FIS defaults)."""
    root = Path(__file__).resolve().parents[3]  # RASPlotReplica/
    candidates = [
        root / "settings.local.json",
        root.parent / "settings.local.json",
    ]
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


@router.post("/source")
def open_source(req: OpenRequest) -> dict:
    """Open a HEC-RAS plan (*.pXX.hdf) file and return the summary."""
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {p}")
    if p.suffix.lower() != ".hdf" or not p.name.lower().split(".")[1].startswith("p"):
        raise HTTPException(400, f"Expected a HEC-RAS plan file like *.pXX.hdf; got {p.name}")
    reader = HdfReader(p)
    plan = reader.read_plan()
    sid = _new_id()
    # seed header from settings if present
    header = WorkHeader()
    try:
        settings = get_settings()
        fis = settings.get("default_fis") or {}
        header.stream_name = fis.get("stream_name", header.stream_name)
        header.community = fis.get("community", header.community)
        header.county = fis.get("county", header.county)
        header.state = fis.get("state", header.state)
        header.vertical_datum = fis.get("vertical_datum", header.vertical_datum)
        header.unit = fis.get("distance_unit", header.unit)
        header.h_scale = float(fis.get("h_scale", header.h_scale))
        header.v_scale = float(fis.get("v_scale", header.v_scale))
        header.page_size = fis.get("page_size", header.page_size)
    except Exception:
        pass
    _SESSIONS[sid] = {"plan": plan, "reach_idx": 0, "header": header, "path": str(p)}
    db = build_db_from_plan(plan, reach_idx=0, header=header)
    _SESSIONS[sid]["db"] = db
    payload = plan_to_dict(plan)
    payload["session_id"] = sid
    payload["db"] = db.to_dict()
    return payload


@router.get("/session/{sid}")
def get_session(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    return plan_to_dict(_SESSIONS[sid]["plan"])


@router.get("/reaches/{sid}")
def list_reaches(sid: str) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
    return {
        "reaches": [
            {
                "river": r.river, "reach": r.reach,
                "num_xs": len(r.cross_sections), "num_structures": len(r.structures),
                "rs_list": [c.rs for c in r.cross_sections],
            }
            for r in plan.reaches
        ]
    }


@router.get("/profile/{sid}")
def get_profile(
    sid: str,
    profile: str | None = Query(None, description="Primary profile name; default = first"),
    profiles: str | None = Query(None, description="Comma-separated profile names to overlay"),
    reach_idx: int = Query(0),
    label_every: int = Query(5, ge=1, le=100, description="Auto letter every Nth XS"),
) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
    if reach_idx < 0 or reach_idx >= len(plan.reaches):
        raise HTTPException(400, f"reach_idx out of range (0..{len(plan.reaches) - 1})")
    reach = plan.reaches[reach_idx]
    if not plan.profiles:
        raise HTTPException(400, "No profile results available")
    names = [p.name for p in plan.profiles]
    selected = _pick_profiles(plan, profile, profiles)
    sel = selected[0]
    cumdist = compute_cumdist(reach)
    xs_list = reach.cross_sections

    db: RasplotDatabase | None = None
    working_xs_by_idx: dict[int, WorkingRow] = {}
    working_xs_by_rs: dict[str, WorkingRow] = {}
    working_struct_rows: list[WorkingRow] = []
    working_struct_by_rs: dict[str, list[WorkingRow]] = {}
    try:
        db = _session_db(sid)
        for r in db.working:
            if r.kind == "xs":
                if r.source_xs_index is not None:
                    working_xs_by_idx[r.source_xs_index] = r
                if r.rs:
                    working_xs_by_rs[r.rs] = r
            elif r.kind == "struct":
                working_struct_rows.append(r)
                if r.rs:
                    working_struct_by_rs.setdefault(r.rs, []).append(r)
    except Exception:
        db = None

    # Prefer letters from session DB when available
    letters_by_rs: dict[str, str | None] = {}
    text_by_rs: dict[str, str | None] = {}
    if db is not None:
        for r in db.working:
            if r.kind == "xs":
                letters_by_rs[r.rs] = r.label_letter
                text_by_rs[r.rs] = r.label_text
    if not letters_by_rs:
        struct_rs = [s.rs for s in reach.structures if s.rs]
        letter_list = _assign_label_letters(xs_list, struct_rs, every=label_every)
        letters_by_rs = {xs_list[i].rs: letter_list[i] for i in range(len(xs_list))}

    selected_payload = [
        {"name": p.name, "wsel": list(p.wsel)}
        for p in selected
    ]

    out_xs = []
    for i, xs in enumerate(xs_list):
        working_row = working_xs_by_idx.get(i) or working_xs_by_rs.get(xs.rs)
        if working_row is not None:
            length_channel = working_row.length_chnl
            cumdist_i = working_row.cumdist
            minchel = working_row.minchel
            label_letter = working_row.label_letter
            label_text = working_row.label_text
            main_wsel = working_row.profiles.get(sel.name)
            if main_wsel is None and i < len(sel.wsel):
                main_wsel = sel.wsel[i]
            wsel_by_profile = {}
            for p in selected_payload:
                name = p["name"]
                wsel_val = working_row.profiles.get(name)
                if wsel_val is None and i < len(p["wsel"]):
                    wsel_val = p["wsel"][i]
                wsel_by_profile[name] = wsel_val
        else:
            length_channel = xs.length_channel
            cumdist_i = cumdist[i]
            minchel = xs.minchel
            label_letter = letters_by_rs.get(xs.rs)
            label_text = text_by_rs.get(xs.rs)
            main_wsel = sel.wsel[i] if i < len(sel.wsel) else None
            wsel_by_profile = {
                p["name"]: (p["wsel"][i] if i < len(p["wsel"]) else None)
                for p in selected_payload
            }

        row = {
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
        }
        out_xs.append(row)

    out_struct = []
    if db is not None and working_struct_rows:
        for row in working_struct_rows:
            out_struct.append({
                "river": row.river,
                "reach": row.reach,
                "rs": row.rs,
                "type": row.struc,
                "name": row.label_text or row.struc or row.rs,
                "weir_width": row.length_chnl,
                "low_chord": row.low_chord,
                "high_chord": row.high_chord,
                "minchel": row.minchel,
                "cumdist": row.cumdist,
            })
    else:
        for s in reach.structures:
            tlow = (s.type or "").lower()
            if not s.rs and "connection" in tlow:
                continue
            working_structs = working_struct_by_rs.get(s.rs, [])
            struct_row = None
            if working_structs:
                struct_row = working_structs[0]
            cum_s = _structure_cumdist(s.rs, xs_list, cumdist)
            if struct_row is not None and struct_row.cumdist is not None:
                cum_s = struct_row.cumdist
            minchel = s.minchel
            if struct_row is not None and struct_row.minchel is not None:
                minchel = struct_row.minchel
            elif minchel is None:
                nearest = _nearest_xs(s.rs, xs_list)
                if nearest is not None:
                    minchel = nearest.minchel
            out_struct.append({
                "river": s.river, "reach": s.reach, "rs": s.rs, "type": s.type,
                "name": s.name, "weir_width": s.weir_width,
                "low_chord": s.low_chord, "high_chord": s.high_chord,
                "minchel": minchel, "cumdist": cum_s,
            })
    return {
        "session_id": sid,
        "reach": {"river": reach.river, "reach": reach.reach},
        "profile": sel.name,
        "profiles_selected": [p.name for p in selected],
        "profile_names": names,
        "cross_sections": out_xs,
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
    plan = _SESSIONS[sid]["plan"]
    sel = plan.profiles[0]
    if profile:
        for p in plan.profiles:
            if p.name == profile:
                sel = p
                break
    out_xs = []
    for i, xs in enumerate(m.cross_sections):
        out_xs.append({
            "rs": xs.rs, "river": xs.river, "reach": xs.reach,
            "cumdist": m.cumdist[i], "minchel": xs.minchel,
            "wsel": sel.wsel[i] if i < len(sel.wsel) else None,
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
        "profile": sel.name,
        "profile_names": [p.name for p in plan.profiles],
    }


@router.get("/map/{sid}")
def geo(sid: str) -> dict:
    """Return GeoJSON of reaches (centerline + cross-sections + structures)."""
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    plan = _SESSIONS[sid]["plan"]
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
                                    "river": xs.river, "reach": xs.reach},
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
    p = Path(path).resolve()
    cache_root = Path(tempfile.gettempdir(), "rasplotreplica").resolve()
    try:
        p.relative_to(cache_root)
    except ValueError:
        raise HTTPException(400, "Path outside session cache")
    if not p.is_file():
        raise HTTPException(404, "File not found")
    ext = p.suffix.lower()
    media = {
        ".pdf": "application/pdf",
        ".dxf": "application/dxf",
        ".zip": "application/zip",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")
    return FileResponse(p, filename=p.name, media_type=media)


# --------------------------------------------------------------------------- #
# RASPLOT database CRUD                                                         #
# --------------------------------------------------------------------------- #


class WorkingPatch(BaseModel):
    id: int
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
    start_panel_no: int | None = None
    h_scale: float | None = None
    v_scale: float | None = None
    page_size: str | None = None
    table_name: str | None = None
    table_title: str | None = None


class ProfileMetaUpdate(BaseModel):
    name: str
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
    panel_x: float = 0.0
    panel_y: float = 0.0
    h_scale: float = 500.0
    v_scale: float = 5.0


class PanelsReplace(BaseModel):
    panels: list[PanelIn]
    auto: bool = False
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
        if p.minchel is not None:
            r.minchel = p.minchel
        if p.low_chord is not None:
            r.low_chord = p.low_chord
        if p.high_chord is not None:
            r.high_chord = p.high_chord
        if p.profiles is not None:
            r.profiles.update(p.profiles)
    return {"ok": True, "n": len(patches)}


@router.post("/db/{sid}/fix-drawdowns")
def post_fix_drawdowns(sid: str) -> dict:
    db = _session_db(sid)
    names = [p.name for p in db.profiles]
    fix_drawdowns(db.working, names)
    return {"ok": True}


@router.post("/db/{sid}/reletter")
def post_reletter(sid: str, req: ReletterRequest) -> dict:
    db = _session_db(sid)
    reletter_from(db.working, req.start_row_id, req.start_letter)
    return {"ok": True}


@router.post("/db/{sid}/recalc-cumdist")
def post_recalc_cumdist(sid: str, req: CumdistRequest) -> dict:
    db = _session_db(sid)
    recalc_cumdist(db.working, start=req.start)
    db.panels = auto_panels(
        db.working, h_scale=db.header.h_scale, v_scale=db.header.v_scale,
        page_size=db.header.page_size,
    )
    return {"ok": True, "panels": len(db.panels)}


@router.post("/db/{sid}/auto-letter")
def post_auto_letter(sid: str, every: int = Query(5, ge=1, le=100)) -> dict:
    db = _session_db(sid)
    for r in db.working:
        if r.kind == "xs":
            r.label_letter = None
    auto_letter(db.working, every=every)
    return {"ok": True}


@router.post("/db/{sid}/node-names")
def post_node_names(sid: str, req: NodeNamesRequest) -> dict:
    if sid not in _SESSIONS:
        raise HTTPException(404, "Unknown session")
    db = _session_db(sid)
    db.use_node_names = req.enabled
    if req.enabled:
        apply_node_names(db.working, _SESSIONS[sid]["plan"], _SESSIONS[sid].get("reach_idx", 0))
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
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(h, k, v)
    return asdict(h)


@router.get("/db/{sid}/profiles")
def get_profiles_meta(sid: str) -> dict:
    from dataclasses import asdict
    return {"profiles": [asdict(p) for p in _session_db(sid).profiles]}


@router.put("/db/{sid}/profiles")
def put_profiles_meta(sid: str, updates: list[ProfileMetaUpdate]) -> dict:
    db = _session_db(sid)
    by = {p.name: p for p in db.profiles}
    for u in updates:
        p = by.get(u.name)
        if not p:
            continue
        data = u.model_dump(exclude_none=True)
        data.pop("name", None)
        for k, v in data.items():
            setattr(p, k, v)
    return {"ok": True}


@router.get("/db/{sid}/frequencies")
def get_frequencies(sid: str) -> dict:
    return {"frequencies": _session_db(sid).frequencies}


@router.put("/db/{sid}/frequencies")
def put_frequencies(sid: str, frequencies: list[dict]) -> dict:
    db = _session_db(sid)
    db.frequencies = frequencies
    return {"ok": True}


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
    from dataclasses import asdict
    return {"panels": [asdict(p) for p in _session_db(sid).panels]}


@router.get("/db/{sid}/auto-end")
def get_auto_end(sid: str, start_station: float = Query(...)) -> dict:
    db = _session_db(sid)
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

    _pw, _ph, usable_width_in, _uh = plot_usable_size(page_size)
    coverage = panel_coverage(h_scale, page_size)
    if not math.isfinite(coverage) or coverage <= 0:
        raise HTTPException(400, "Calculated panel coverage must be greater than zero.")

    return {
        "start_station": start_station,
        "end_station": min(available_max, start_station + coverage),
        "h_scale": h_scale,
        "page_size": page_size,
        "usable_width_in": usable_width_in,
        "panel_coverage": coverage,
    }


@router.put("/db/{sid}/panels")
def put_panels(sid: str, req: PanelsReplace) -> dict:
    from dataclasses import asdict
    db = _session_db(sid)
    if req.auto:
        hs = req.h_scale if req.h_scale is not None else db.header.h_scale
        vs = req.v_scale if req.v_scale is not None else db.header.v_scale
        ps = req.page_size or db.header.page_size
        db.header.h_scale = hs
        db.header.v_scale = vs
        db.header.page_size = ps
        db.panels = auto_panels(db.working, h_scale=hs, v_scale=vs, page_size=ps)
    else:
        db.panels = [
            PanelDef(**p.model_dump()) for p in req.panels
        ]
    return {"panels": [asdict(p) for p in db.panels]}


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
        sess["merged"] = merged
        # Build a synthetic single reach
        from ..core.hdf_reader import ReachGeometry, ProfileResult
        synthetic = type("P", (), {})()
        # Use a lightweight stand-in plan
        class _Plan:
            pass
        p2 = _Plan()
        p2.profiles = plan.profiles
        p2.reaches = [ReachGeometry(
            river=merged.rivers[0] if merged.rivers else "",
            reach="+".join(merged.reaches),
            centerline=[],
            cross_sections=merged.cross_sections,
            structures=merged.structures,
        )]
        old = sess.get("db")
        header = old.header if old else WorkHeader()
        db = build_db_from_plan(p2, reach_idx=0, header=header, cumdist=merged.cumdist)
        sess["db"] = db
        sess["reach_idx"] = -1
        return db.to_dict()
    db = _rebuild_db(sid, reach_idx=req.reach_idx)
    return db.to_dict()

