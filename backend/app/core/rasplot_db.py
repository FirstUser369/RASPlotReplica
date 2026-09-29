"""In-memory RASPLOT v3-compatible database schema.

Tables mirror the Access schema (user guide p.12 / §5.3 / §6.x) without MS Access:
  working, profiles, floodway, landmark, panel, workheader, frequencies.
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .geometry_text_reader import parse_geometry_text_structures


# Default FEMA flood frequencies (RASPLOT v3 §4.5)
DEFAULT_FREQUENCIES: list[dict[str, Any]] = [
    {"frequency": "1%", "legend_text": "1% ANNUAL CHANCE FLOOD", "layer": "PROF_1PCT",
     "color": "#C00000", "linetype": "CONTINUOUS", "lineweight": 0.35},
    {"frequency": "1%+", "legend_text": "1% PLUS ANNUAL CHANCE FLOOD", "layer": "PROF_1PCT_PLUS",
     "color": "#FF6600", "linetype": "DASHED", "lineweight": 0.25},
    {"frequency": "0.2%", "legend_text": "0.2% ANNUAL CHANCE FLOOD", "layer": "PROF_02PCT",
     "color": "#7030A0", "linetype": "DASHDOT", "lineweight": 0.25},
    {"frequency": "2%", "legend_text": "2% ANNUAL CHANCE FLOOD", "layer": "PROF_2PCT",
     "color": "#0070C0", "linetype": "CONTINUOUS", "lineweight": 0.30},
    {"frequency": "4%", "legend_text": "4% ANNUAL CHANCE FLOOD", "layer": "PROF_4PCT",
     "color": "#00B050", "linetype": "DASHED", "lineweight": 0.25},
    {"frequency": "10%", "legend_text": "10% ANNUAL CHANCE FLOOD", "layer": "PROF_10PCT",
     "color": "#00B0F0", "linetype": "DOTTED", "lineweight": 0.25},
    {"frequency": "20%", "legend_text": "20% ANNUAL CHANCE FLOOD", "layer": "PROF_20PCT",
     "color": "#92D050", "linetype": "DASHED", "lineweight": 0.20},
    {"frequency": "50%", "legend_text": "50% ANNUAL CHANCE FLOOD", "layer": "PROF_50PCT",
     "color": "#A6A6A6", "linetype": "DOTTED", "lineweight": 0.20},
    {"frequency": "1% Future", "legend_text": "1% FUTURE CONDITIONS", "layer": "PROF_1PCT_FUT",
     "color": "#FF0000", "linetype": "DASHDOT", "lineweight": 0.25},
]


# Sheet page sizes in inches (landscape width x height)
PAGE_SIZES: dict[str, tuple[float, float]] = {
    "11x17": (17.0, 11.0),
    "ANSI_A": (11.0, 8.5),
    "ANSI_B": (17.0, 11.0),
    "ANSI_C": (22.0, 17.0),
    "ANSI_D": (34.0, 22.0),
    "A3": (16.54, 11.69),
    "A1": (33.11, 23.39),
    "LETTER": (11.0, 8.5),
    "TABLOID": (17.0, 11.0),
}

# Plot-area margins (inches) — must match dxf_export / PDF layout
PLOT_MARGIN_L = 1.75   # title block / left
PLOT_MARGIN_R = 0.75
PLOT_MARGIN_B = 1.00
PLOT_MARGIN_T = 1.25


def plot_usable_size(
    page_size: str = "11x17",
    custom_page: tuple[float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Return (page_w, page_h, usable_width_in, usable_height_in)."""
    if custom_page:
        page_w, page_h = custom_page
    else:
        page_w, page_h = PAGE_SIZES.get(page_size, (17.0, 11.0))
    uw = max(2.0, page_w - PLOT_MARGIN_L - PLOT_MARGIN_R)
    uh = max(2.0, page_h - PLOT_MARGIN_B - PLOT_MARGIN_T)
    return page_w, page_h, uw, uh


def panel_coverage(
    h_scale: float,
    page_size: str = "11x17",
    custom_page: tuple[float, float] | None = None,
) -> float:
    """Return the station coverage of one printable panel in feet."""
    _pw, _ph, usable_width_in, _uh = plot_usable_size(page_size, custom_page)
    return float(h_scale) * usable_width_in


@dataclass
class WorkingRow:
    """One row of the FIS / RASPLOT profile table."""
    id: int
    river: str
    reach: str
    rs: str
    struc: str = ""                 # e.g. BRIDGE-Dn, CULVERT-Up, or ""
    length_chnl: float = 0.0
    cumdist: float = 0.0
    minchel: Optional[float] = None
    low_chord: Optional[float] = None
    high_chord: Optional[float] = None
    label_letter: Optional[str] = None
    label_text: Optional[str] = None
    profiles: dict[str, Optional[float]] = field(default_factory=dict)  # name -> WSEL
    kind: str = "xs"                # xs | struct
    source_xs_index: Optional[int] = None
    weir_width: Optional[float] = None
    connected_us_rs: str = ""
    structure_profile_data: list[tuple[float, float]] = field(default_factory=list)
    hw_connectivity: list[tuple[str, float]] = field(default_factory=list)
    tw_connectivity: list[tuple[str, float]] = field(default_factory=list)
    lateral_hw_distance: Optional[float] = None
    lateral_tw_distance: Optional[float] = None
    canonical_length_chnl: Optional[float] = None
    connected_ds_rs: str = ""
    culvert_us_gap: Optional[float] = None
    culvert_barrel_length: Optional[float] = None
    culvert_ds_residual: Optional[float] = None
    structure_partition_valid: Optional[bool] = None
    structure_partition_warning: str = ""


@dataclass
class ProfileMeta:
    name: str                       # HEC-RAS profile name
    frequency: str = ""             # FEMA frequency label
    backwater_elev: Optional[float] = None
    backwater_text: str = ""
    color: str = "#196bd6"
    linetype: str = "CONTINUOUS"
    lineweight: float = 0.30
    legend_text: str = ""
    layer: str = ""
    selected: bool = False


@dataclass
class Landmark:
    id: int
    distance: float
    elevation: float
    text: str
    orientation: str = "Vertical"   # Horizontal | Vertical
    underline: bool = False
    adjusted: bool = False


@dataclass
class PanelDef:
    panel: int
    start_station: float
    end_station: float
    start_elevation: float
    end_elevation: float
    panel_x: float = 0.0
    panel_y: float = 0.0
    h_scale: float = 500.0          # ft per inch
    v_scale: float = 5.0            # ft per inch


@dataclass
class FloodwayRow:
    id: int
    river: str
    reach: str
    rs: str
    label_letter: Optional[str] = None
    width: Optional[float] = None
    area: Optional[float] = None
    velocity: Optional[float] = None
    regulatory: Optional[float] = None
    elev_without_fw: Optional[float] = None
    elev_with_fw: Optional[float] = None
    elev_diff: Optional[float] = None


@dataclass
class WorkHeader:
    community: str = "AND INCORPORATED AREAS"
    county: str = "COUNTY"
    state: str = "IN"
    stream_name: str = "STREAM NAME"
    stream_distance_label: str = ""
    unit: str = "Feet"              # Feet|Hundred Feet|Thousand Feet|Meters|Kilometers|Miles
    vertical_datum: str = "NAVD88"
    start_panel_no: int = 1
    h_scale: float = 500.0
    v_scale: float = 5.0
    page_size: str = "11x17"
    table_name: str = "Table 24"
    table_title: str = "FLOODWAY DATA"


@dataclass
class RasplotDatabase:
    """Session-scoped RASPLOT database object."""
    working: list[WorkingRow] = field(default_factory=list)
    profiles: list[ProfileMeta] = field(default_factory=list)
    landmarks: list[Landmark] = field(default_factory=list)
    panels: list[PanelDef] = field(default_factory=list)
    floodway: list[FloodwayRow] = field(default_factory=list)
    header: WorkHeader = field(default_factory=WorkHeader)
    frequencies: list[dict[str, Any]] = field(default_factory=lambda: deepcopy(DEFAULT_FREQUENCIES))
    use_node_names: bool = False
    next_id: int = 1
    next_landmark_id: int = 1

    def to_dict(self) -> dict:
        return {
            "working": [asdict(r) for r in self.working],
            "profiles": [asdict(p) for p in self.profiles],
            "landmarks": [asdict(l) for l in self.landmarks],
            "panels": [asdict(p) for p in self.panels],
            "floodway": [asdict(f) for f in self.floodway],
            "header": asdict(self.header),
            "frequencies": self.frequencies,
            "use_node_names": self.use_node_names,
        }


def _interpolate_station_elevation(pairs: list[tuple[float, float]], station: float) -> Optional[float]:
    if not pairs:
        return None
    ordered = sorted(pairs, key=lambda item: item[0])
    if len(ordered) == 1:
        return ordered[0][1]
    if station <= ordered[0][0]:
        return ordered[0][1]
    if station >= ordered[-1][0]:
        return ordered[-1][1]
    for i in range(len(ordered) - 1):
        x0, y0 = ordered[i]
        x1, y1 = ordered[i + 1]
        if x0 <= station <= x1:
            if x1 == x0:
                return y1
            t = (station - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return ordered[-1][1]


def _geometry_text_structures_for_plan(plan: Any) -> dict[str, Any]:
    plan_path = getattr(plan, "plan_path", None) or getattr(plan, "results_path", None) or getattr(plan, "plan_hdf_path", None)
    if plan_path is None:
        return {}
    plan_file = Path(plan_path)
    if not plan_file.exists():
        return {}
    candidates: list[Path] = []
    base_stem = plan_file.stem.split(".")[0]
    for candidate in plan_file.parent.glob(f"{base_stem}.g*"):
        if candidate.is_file() and candidate.suffix.lower() not in {".hdf", ".bak"}:
            candidates.append(candidate)
    if not candidates:
        return {}
    by_rs: dict[str, Any] = {}
    for candidate in sorted(candidates):
        for entry in parse_geometry_text_structures(candidate):
            rs_key = str(entry.rs).strip()
            if rs_key:
                by_rs[rs_key] = entry
    return by_rs


def _index_to_letter(i: int) -> str:
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


def letter_to_index(letter: str) -> int:
    letter = (letter or "").strip().upper()
    if not letter:
        return 0
    n = 0
    for ch in letter:
        if not ("A" <= ch <= "Z"):
            return 0
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def build_working_from_plan(
    plan: Any,
    reach_idx: int = 0,
    cumdist: list[float] | None = None,
    structures: list | None = None,
) -> list[WorkingRow]:
    """Build WORKING rows from a HEC-RAS plan reach (upstream-first order).

    Structure rows are inserted as paired -Dn / -Up per v3 §5.3 (where both
    faces exist); otherwise a single structure row at the computed station.
    """
    from .merge import compute_cumdist

    if not plan.reaches:
        return []
    reach = plan.reaches[reach_idx]
    if cumdist is None:
        cumdist = compute_cumdist(reach)
    xs_list = reach.cross_sections
    structs = structures if structures is not None else reach.structures
    profile_names = [p.name for p in plan.profiles]
    geometry_text_structures = _geometry_text_structures_for_plan(plan)

    rows: list[WorkingRow] = []
    rid = 1

    # Laterals use their explicit connected US XS when available. Other
    # structure types retain nearest-RS association pending separate validation.
    struct_at: dict[int, list] = {}
    for s in structs:
        explicit_i = None
        if (s.type or "").upper() == "LATERAL" and s.us_rs:
            explicit_i = next(
                (i for i, xs in enumerate(xs_list) if xs.rs == s.us_rs),
                None,
            )
        if explicit_i is not None:
            struct_at.setdefault(explicit_i, []).append(s)
            continue
        try:
            srs = float(s.rs)
        except Exception:
            continue
        best_i, best_d = 0, float("inf")
        for i, xs in enumerate(xs_list):
            try:
                d = abs(float(xs.rs) - srs)
            except Exception:
                continue
            if d < best_d:
                best_d, best_i = d, i
        struct_at.setdefault(best_i, []).append(s)

    for i, xs in enumerate(xs_list):
        profs = {
            p.name: (p.wsel[i] if i < len(p.wsel) else None)
            for p in plan.profiles
        }
        row_length_chnl = xs.length_channel
        if i == len(xs_list) - 1:
            row_length_chnl = 0.0
        rows.append(WorkingRow(
            id=rid, river=xs.river, reach=xs.reach, rs=xs.rs,
            length_chnl=row_length_chnl, cumdist=cumdist[i] if i < len(cumdist) else 0.0,
            minchel=xs.minchel, profiles=profs, kind="xs", source_xs_index=i,
            canonical_length_chnl=xs.length_channel,
        ))
        rid += 1
        for s in struct_at.get(i, []):
            s_type = (s.type or "STRUCTURE").upper()
            if s_type == "CULVERT" and s.us_rs and s.ds_rs and s.culvert_length is not None and s.culvert_us_distance is not None and s.invert_us is not None and s.invert_ds is not None:
                us_xs = next((x for x in xs_list if x.rs == s.us_rs), None)
                ds_xs = next((x for x in xs_list if x.rs == s.ds_rs), None)
                canonical_interval = us_xs.length_channel if us_xs is not None else None
                raw_dn_len = (
                    canonical_interval - s.culvert_length - s.culvert_us_distance
                    if canonical_interval is not None else None
                )
                partition_valid = (
                    us_xs is not None
                    and ds_xs is not None
                    and raw_dn_len is not None
                    and raw_dn_len >= -0.01
                    and math.isclose(
                        s.culvert_us_distance + s.culvert_length + raw_dn_len,
                        canonical_interval,
                        rel_tol=1e-7,
                        abs_tol=0.01,
                    )
                )
                dn_len = max(0.0, raw_dn_len or 0.0)
                partition_warning = "" if partition_valid else (
                    "Culvert partition does not resolve to the connected US XS channel length."
                )
                anchor_cumdist = cumdist[i] if i < len(cumdist) else 0.0
                up_face_cumdist = (
                    anchor_cumdist - s.culvert_us_distance
                    if partition_valid else anchor_cumdist
                )
                dn_face_cumdist = (
                    up_face_cumdist - s.culvert_length
                    if partition_valid else anchor_cumdist
                )
                geom_struct = geometry_text_structures.get(str(s.rs).strip()) if s.rs else None
                profile_values = {n: (profs.get(n) if n in profs else None) for n in profile_names}
                up_low = s.invert_us
                dn_low = s.invert_ds
                up_high = None
                dn_high = None
                if geom_struct is not None:
                    if geom_struct.rise is not None:
                        up_low = (s.invert_us or 0.0) + float(geom_struct.rise)
                        dn_low = (s.invert_ds or 0.0) + float(geom_struct.rise)
                    if geom_struct.barrel_us_station is not None and geom_struct.deck_up:
                        up_high = _interpolate_station_elevation(geom_struct.deck_up, float(geom_struct.barrel_us_station))
                    if geom_struct.barrel_ds_station is not None and geom_struct.deck_dn:
                        dn_high = _interpolate_station_elevation(geom_struct.deck_dn, float(geom_struct.barrel_ds_station))
                rows.append(WorkingRow(
                    id=rid, river=s.river or xs.river, reach=s.reach or xs.reach, rs=s.rs,
                    struc="CULVERT-UP",
                    length_chnl=0.0,
                    cumdist=up_face_cumdist,
                    minchel=s.invert_us,
                    low_chord=up_low,
                    high_chord=up_high,
                    profiles=profile_values,
                    kind="struct", source_xs_index=i,
                    weir_width=s.weir_width,
                    connected_us_rs=s.us_rs,
                    connected_ds_rs=s.ds_rs,
                    culvert_us_gap=s.culvert_us_distance,
                    culvert_barrel_length=s.culvert_length,
                    culvert_ds_residual=dn_len,
                    structure_partition_valid=partition_valid,
                    structure_partition_warning=partition_warning,
                ))
                rid += 1
                rows.append(WorkingRow(
                    id=rid, river=s.river or xs.river, reach=s.reach or xs.reach, rs=s.rs,
                    struc="CULVERT-DN",
                    length_chnl=0.0,
                    cumdist=dn_face_cumdist,
                    minchel=s.invert_ds,
                    low_chord=dn_low,
                    high_chord=dn_high,
                    profiles=profile_values,
                    kind="struct", source_xs_index=i,
                    weir_width=s.weir_width,
                    connected_us_rs=s.us_rs,
                    connected_ds_rs=s.ds_rs,
                    culvert_us_gap=s.culvert_us_distance,
                    culvert_barrel_length=s.culvert_length,
                    culvert_ds_residual=dn_len,
                    structure_partition_valid=partition_valid,
                    structure_partition_warning=partition_warning,
                ))
                rid += 1
                continue

            # Generic fallback for non-culvert structures.
            if any(k in s_type for k in ("BRIDGE", "INLINE", "WEIR")) and "LATERAL" not in s_type:
                faces = ("Dn", "Up")
            else:
                faces = ("",)
            for face in faces:
                label = f"{s_type}-{face}" if face else s_type
                scum = cumdist[i] if i < len(cumdist) else 0.0
                if face == "Up":
                    scum = scum + max(1.0, (s.weir_width or 20.0) * 0.5)
                elif face == "Dn":
                    scum = scum - max(1.0, (s.weir_width or 20.0) * 0.5)
                    scum = max(0.0, scum)
                rows.append(WorkingRow(
                    id=rid, river=s.river or xs.river, reach=s.reach or xs.reach, rs=s.rs,
                    struc=label,
                    length_chnl=0.0 if s_type == "LATERAL" else (s.weir_width or 0.0),
                    cumdist=scum,
                    minchel=s.minchel if s.minchel is not None else xs.minchel,
                    low_chord=s.low_chord, high_chord=s.high_chord,
                    profiles={n: None for n in profile_names},
                    kind="struct", source_xs_index=i,
                    weir_width=s.weir_width,
                    connected_us_rs=s.us_rs,
                    structure_profile_data=list(s.profile_data),
                    hw_connectivity=list(s.hw_connectivity),
                    tw_connectivity=list(s.tw_connectivity),
                    lateral_hw_distance=s.lateral_hw_distance,
                    lateral_tw_distance=s.lateral_tw_distance,
                ))
                rid += 1
    return rows


def auto_letter(rows: list[WorkingRow], every: int = 5) -> list[WorkingRow]:
    """Assign letters downstream→upstream on XS rows (and structure neighbors)."""
    xs_idxs = [i for i, r in enumerate(rows) if r.kind == "xs"]
    if not xs_idxs:
        return rows
    marked: set[int] = {xs_idxs[0], xs_idxs[-1]}
    marked.update(xs_idxs[:: max(1, every)])
    # mark XS that have adjacent structure rows
    for i, r in enumerate(rows):
        if r.kind == "struct" and i > 0 and rows[i - 1].kind == "xs":
            marked.add(i - 1)
    # letter order: downstream first = reverse in upstream-first list
    ordered = sorted(marked, reverse=True)
    for k, idx in enumerate(ordered):
        rows[idx].label_letter = _index_to_letter(k)
    return rows


def apply_node_names(rows: list[WorkingRow], plan: Any, reach_idx: int = 0) -> list[WorkingRow]:
    """Import HEC-RAS node names into LABELLETTER (≤3) or LABEL TEXT (>3)."""
    if not plan.reaches:
        return rows
    xs_list = plan.reaches[reach_idx].cross_sections
    name_by_rs = {xs.rs: (xs.name or "").strip() for xs in xs_list}
    for r in rows:
        if r.kind != "xs":
            continue
        name = name_by_rs.get(r.rs, "")
        if not name:
            continue
        if len(name) <= 3:
            r.label_letter = name
        else:
            r.label_text = name
    return rows


def reletter_from(rows: list[WorkingRow], start_row_id: int, start_letter: str) -> list[WorkingRow]:
    """Re-letter from a selected XS row downward (downstream) in alpha order (v3 §6.2)."""
    start_idx = next((i for i, r in enumerate(rows) if r.id == start_row_id), None)
    if start_idx is None:
        return rows
    # "below it in the table" = higher index if table is upstream-first with DS at bottom
    # v3 table is displayed upstream-first; "below" = toward downstream = higher indices
    base = letter_to_index(start_letter)
    k = 0
    for i in range(start_idx, len(rows)):
        if rows[i].kind != "xs":
            continue
        if rows[i].label_letter or i == start_idx:
            rows[i].label_letter = _index_to_letter(base + k)
            k += 1
    return rows


def recalc_cumdist(rows: list[WorkingRow], start: float = 0.0) -> list[WorkingRow]:
    """Recalculate canonical main-channel CUMDIST from ordinary XS rows only."""
    xs_rows = [row for row in rows if row.kind == "xs"]
    if not xs_rows:
        return rows

    from .merge import compute_cumdist_from_lengths

    cumdist = compute_cumdist_from_lengths(
        [
            row.canonical_length_chnl
            if row.canonical_length_chnl is not None else row.length_chnl
            for row in xs_rows
        ],
        start=start,
    )
    for row, distance in zip(xs_rows, cumdist):
        row.cumdist = distance

    # A lateral is temporarily displayed at its explicit connected US XS. It
    # is not a main-channel interval and must not affect the XS chain.
    xs_by_rs = {row.rs: row for row in xs_rows}
    xs_by_index = {
        row.source_xs_index: row
        for row in xs_rows
        if row.source_xs_index is not None
    }
    for row in rows:
        if row.kind != "struct" or (row.struc or "").upper() != "LATERAL":
            continue
        anchor = xs_by_rs.get(row.connected_us_rs)
        if anchor is None and row.source_xs_index is not None:
            anchor = xs_by_index.get(row.source_xs_index)
        if anchor is not None:
            row.cumdist = anchor.cumdist

    # A validated culvert partition locates faces within an already-accounted
    # ordinary-XS interval; it never contributes another reach length.
    culvert_pairs: dict[str, dict[str, WorkingRow]] = {}
    for row in rows:
        label = (row.struc or "").upper()
        if label in {"CULVERT-UP", "CULVERT-DN"}:
            culvert_pairs.setdefault(str(row.rs), {})[label] = row
    for pair in culvert_pairs.values():
        up = pair.get("CULVERT-UP")
        dn = pair.get("CULVERT-DN")
        template = up or dn
        if template is None or template.structure_partition_valid is not True:
            continue
        us_xs = xs_by_rs.get(template.connected_us_rs)
        ds_xs = xs_by_rs.get(template.connected_ds_rs)
        if us_xs is None or ds_xs is None:
            continue
        gap = template.culvert_us_gap
        barrel = template.culvert_barrel_length
        residual = template.culvert_ds_residual
        if gap is None or barrel is None or residual is None:
            continue
        up_face = us_xs.cumdist - gap
        dn_face = up_face - barrel
        partition_ok = math.isclose(
            dn_face - ds_xs.cumdist,
            residual,
            rel_tol=1e-7,
            abs_tol=0.01,
        )
        if not partition_ok:
            for row in (up, dn):
                if row is not None:
                    row.structure_partition_valid = False
                    row.structure_partition_warning = (
                        "Recalculated culvert faces do not conserve the connected XS interval."
                    )
            continue
        if up is not None:
            up.cumdist = up_face
        if dn is not None:
            dn.cumdist = dn_face

    return rows


def fix_drawdowns(rows: list[WorkingRow], profile_names: list[str] | None = None) -> list[WorkingRow]:
    """Eliminate upstream-below-downstream WSEL dips per frequency (v3 §6.2).

    Walks downstream→upstream; if upstream WSEL < carried level, raise it.
    """
    xs = [r for r in rows if r.kind == "xs"]
    if not xs:
        return rows
    # Determine profiles
    names = profile_names or list(xs[0].profiles.keys())
    # Process each profile independently, DS→US (reversed liste)
    for name in names:
        carried: Optional[float] = None
        for r in reversed(xs):
            v = r.profiles.get(name)
            if v is None:
                continue
            if carried is None:
                carried = v
            elif v < carried:
                r.profiles[name] = carried
            else:
                carried = v
    return rows


def auto_panels(
    rows: list[WorkingRow],
    h_scale: float = 500.0,
    v_scale: float = 5.0,
    page_size: str = "11x17",
    usable_width_in: float | None = None,
    elev_pad: float = 2.0,
    custom_page: tuple[float, float] | None = None,
    single_panel: bool = False,
    fit_to_page: bool = False,
) -> list[PanelDef]:
    """Build panels spanning the reach length at the given horizontal scale.

    Panel station span = h_scale (ft/in) × usable plot width (in).
    Usable width matches the DXF/PDF plot area (page − L/R margins), not an
    ad-hoc reduced width — large sheets (ANSI D, A1) correctly stay one panel
    when the reach fits at the chosen H scale.

    single_panel: force one panel covering the full station range (may exceed
        paper length at the given H scale — used only when explicitly requested).
    fit_to_page: recompute h_scale so the full reach fits on one panel width,
        keeping V scale (and elev range) as given.
    """
    xs = [r for r in rows if r.kind == "xs"]
    if not xs:
        return []
    cum = [r.cumdist for r in xs]
    x_min, x_max = float(min(cum)), float(max(cum))
    reach_len = max(1.0, x_max - x_min)
    inv = [r.minchel for r in xs if r.minchel is not None]
    wsel_vals: list[float] = []
    for r in xs:
        for v in r.profiles.values():
            if v is not None:
                wsel_vals.append(v)
    y_min = (min(inv) if inv else 0.0) - elev_pad
    y_max = (max(wsel_vals) if wsel_vals else y_min + 10.0) + elev_pad

    _pw, _ph, uw_default, uh = plot_usable_size(page_size, custom_page)
    uw = float(usable_width_in) if usable_width_in is not None else uw_default

    hs = float(h_scale)
    vs = float(v_scale)

    if fit_to_page or single_panel:
        if fit_to_page:
            # H scale so full reach = one usable width (round up to whole ft/in)
            hs = max(1.0, math.ceil(reach_len / uw))
            # Optionally fit vertical too if elevation span exceeds page height
            elev_span = max(1.0, y_max - y_min)
            vs_needed = elev_span / uh
            if vs_needed > vs:
                vs = max(1.0, math.ceil(vs_needed * 10) / 10.0)
        return [PanelDef(
            panel=1,
            start_station=x_min,
            end_station=x_max if x_max > x_min else x_min + 1.0,
            start_elevation=y_min,
            end_elevation=y_max,
            h_scale=hs,
            v_scale=vs,
        )]

    span = panel_coverage(hs, page_size, custom_page)  # feet of station per panel
    # Tiny overflow tolerance (0.5%) avoids a near-empty second panel when
    # the reach is only a few feet longer than one page at the listed scale.
    if reach_len <= span * 1.005:
        return [PanelDef(
            panel=1, start_station=x_min, end_station=x_max,
            start_elevation=y_min, end_elevation=y_max,
            h_scale=hs, v_scale=vs,
        )]

    panels: list[PanelDef] = []
    s = x_min
    n = 1
    while s < x_max - 1e-6:
        e = min(x_max, s + span)
        panels.append(PanelDef(
            panel=n, start_station=s, end_station=e,
            start_elevation=y_min, end_elevation=y_max,
            h_scale=hs, v_scale=vs,
        ))
        n += 1
        if e >= x_max:
            break
        s = e
    if not panels:
        panels.append(PanelDef(
            panel=1, start_station=x_min, end_station=max(x_max, x_min + 1),
            start_elevation=y_min, end_elevation=y_max,
            h_scale=hs, v_scale=vs,
        ))
    return panels


def fit_h_scale_for_page(
    reach_length_ft: float,
    page_size: str = "11x17",
    custom_page: tuple[float, float] | None = None,
) -> float:
    """Smallest whole ft/in H scale that fits reach_length on one panel."""
    _pw, _ph, uw, _uh = plot_usable_size(page_size, custom_page)
    return max(1.0, math.ceil(max(1.0, reach_length_ft) / uw))


def build_db_from_plan(
    plan: Any,
    reach_idx: int = 0,
    header: WorkHeader | None = None,
    cumdist: list[float] | None = None,
) -> RasplotDatabase:
    """Construct a full RasplotDatabase from a loaded HEC-RAS plan."""
    db = RasplotDatabase(header=header or WorkHeader())
    working = build_working_from_plan(plan, reach_idx=reach_idx, cumdist=cumdist)
    working = auto_letter(working, every=5)
    db.working = working
    db.next_id = (max((r.id for r in working), default=0) + 1)

    # Profile metadata — try to auto-map FEMA frequencies by name heuristics
    for p in plan.profiles:
        freq, color, ltype, legend, layer, lw = _guess_frequency(p.name, db.frequencies)
        db.profiles.append(ProfileMeta(
            name=p.name, frequency=freq, color=color, linetype=ltype,
            lineweight=lw, legend_text=legend or p.name, layer=layer or f"PROF_{p.name}",
            selected=(p.name == plan.profiles[0].name) if plan.profiles else False,
        ))
    if db.profiles:
        db.profiles[0].selected = True

    db.panels = auto_panels(
        working, h_scale=db.header.h_scale, v_scale=db.header.v_scale,
        page_size=db.header.page_size,
    )
    if plan.reaches:
        r = plan.reaches[reach_idx]
        db.header.stream_name = f"{r.river} {r.reach}".upper()
    return db


def _guess_frequency(name: str, freqs: list[dict]) -> tuple[str, str, str, str, str, float]:
    n = (name or "").lower()
    mapping = [
        (("max", "maximum", "100", "1%", "1 pct", "base"), "1%"),
        (("500", "0.2", "0.2%"), "0.2%"),
        (("50 year", "50-year", "2%", "2 pct"), "2%"),
        (("25", "4%"), "4%"),
        (("10 year", "10-year", "10%"), "10%"),
        (("5 year", "5-year", "20%"), "20%"),
        (("2 year", "2-year", "50%"), "50%"),
        (("plus", "1%+"), "1%+"),
        (("future",), "1% Future"),
    ]
    hit = ""
    for keys, freq in mapping:
        if any(k in n for k in keys):
            hit = freq
            break
    for f in freqs:
        if f["frequency"] == hit:
            return (
                f["frequency"], f["color"], f["linetype"],
                f["legend_text"], f["layer"], f.get("lineweight", 0.3),
            )
    # default blue continuous
    return ("", "#196bd6", "CONTINUOUS", name, f"PROF_{name}", 0.30)


def distance_unit_factor(unit: str) -> float:
    """Multiply foot-distances by this factor to display in the chosen unit."""
    u = (unit or "Feet").lower()
    return {
        "feet": 1.0,
        "hundred feet": 0.01,
        "thousand feet": 0.001,
        "meters": 0.3048,
        "kilometres": 0.0003048,
        "kilometers": 0.0003048,
        "miles": 1.0 / 5280.0,
    }.get(u, 1.0)
