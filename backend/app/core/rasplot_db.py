"""In-memory RASPLOT v3-compatible database schema.

Tables mirror the Access schema (user guide p.12 / §5.3 / §6.x) without MS Access:
  working, profiles, floodway, landmark, panel, workheader, frequencies.
"""
from __future__ import annotations
from .hydraulics import prepare_profiles, normalize_value

import math
from bisect import bisect_left, bisect_right
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from .structure_model import StructureModel, normalize, linked_text, sync_rows, models_from_rows


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
    """Return FEMA panel station coverage in feet."""
    return float(h_scale) * 13.0


def vertical_panel_coverage(v_scale: float) -> float:
    """Return FEMA panel elevation coverage in feet."""
    return float(v_scale) * 9.0


@dataclass
class WorkingRow:
    """One row of the FIS / RASPLOT profile table."""
    id: int
    river: str
    reach: str
    rs: str
    struc: str = ""                 # e.g. BRIDGE-Dn, CULVERT-Up, or ""
    length_chnl: float = 0.0
    cumdist: float | None = 0.0
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
    canonical_length_chnl: Optional[float] = None  # immutable source (compatibility field)
    source_length_chnl: Optional[float] = None  # original HEC-RAS value; never patched
    connected_ds_rs: str = ""
    culvert_us_gap: Optional[float] = None
    culvert_barrel_length: Optional[float] = None
    culvert_ds_residual: Optional[float] = None
    structure_partition_valid: Optional[bool] = None
    structure_partition_warning: str = ""
    structure_model: StructureModel | None = None
    hydraulic_values: dict = field(default_factory=dict)
    raw_profiles: dict = field(default_factory=dict)
    adjustments: list[dict] = field(default_factory=list)
    ground_provenance: dict = field(default_factory=dict)
    source_identity: str = ""       # stable model/source identity for ordinary XS
    row_identity: str = ""          # stable identity for XS and synthetic structure rows
    source_order_index: Optional[int] = None
    source_order_rule: str = ""


@dataclass
class ProfileMeta:
    name: str                       # HEC-RAS profile name
    profile_id: str = ""
    key: str = ""
    source_name: str = ""
    display_name: str = ""
    annual_chance_percent: float | None = None
    result_type: str = "SOURCE_WSEL"
    source_path: str = ""
    source_dataset: str = ""
    mapping_provenance: str = "UNKNOWN"
    backwater_provenance: str = "USER_ENTERED_REFERENCE_ONLY"
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
    panel_x: float | None = None
    panel_y: float | None = None
    h_scale: float = 500.0          # ft per inch
    v_scale: float = 5.0            # ft per inch
    panel_suffix: str = ""

    def __post_init__(self) -> None:
        self.panel_suffix = normalize_panel_suffix(self.panel_suffix)

    @property
    def display_id(self) -> str:
        return format_panel_display_id(self.panel, self.panel_suffix)

    def to_dict(self) -> dict:
        return {**asdict(self), "display_id": self.display_id}


def normalize_panel_suffix(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Panel suffix must be text.")
    suffix = value.strip()
    if not suffix:
        return ""
    if not suffix.isascii() or not suffix.isalpha():
        raise ValueError("Panel suffix must contain only ASCII letters A-Z.")
    if len(suffix) > 3:
        raise ValueError("Panel suffix cannot exceed three letters.")
    return suffix.upper()


def format_panel_display_id(panel_number: int, panel_suffix: str | None = "") -> str:
    return f"{panel_number}{normalize_panel_suffix(panel_suffix)}"


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
    community: str = ""
    county: str = ""
    state: str = ""
    stream_name: str = ""
    stream_distance_label: str = ""
    unit: str = "Feet"              # Feet|Hundred Feet|Thousand Feet|Meters|Kilometers|Miles
    vertical_datum: str = "UNKNOWN"
    source_datum: str = "UNKNOWN"
    datum_provenance: str = "UNKNOWN"
    require_known_datum: bool = False
    start_panel_no: int = 1
    h_scale: float = 500.0
    v_scale: float = 5.0
    page_size: str = "11x17"
    table_name: str = "Table 24"
    table_title: str = "FLOODWAY DATA"
    revision_start_station: float | None = None
    revision_end_station: float | None = None


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
    source_units: str = "INTERNAL_US_CUSTOMARY"
    working_units: str = "INTERNAL_US_CUSTOMARY"
    unit_contract: dict[str, str] = field(default_factory=dict)
    unit_conversion_status: str = "LEGACY_US_VALIDATED"
    stationing_start: float = 0.0
    stationing_signature: str = ""
    panel_signature: str = ""
    panel_review_state: str = "UNSPECIFIED"
    panel_review_dependencies: dict[str, str] = field(default_factory=dict)
    panel_review_snapshots: dict = field(default_factory=dict)
    structure_inventory: dict = field(default_factory=lambda: {"state": "VERIFIED_NONE"})
    flooding_source: dict = field(default_factory=dict)
    distance_ledger: dict = field(default_factory=dict)
    next_id: int = 1
    next_landmark_id: int = 1

    def to_dict(self) -> dict:
        from .state_contract import stationing_dirty, panels_stale, panel_stale_reasons, engineering_version
        inventory = dict(self.structure_inventory)
        inventory['classification'] = {
            'VERIFIED_NONE': 'VERIFIED_NO_STRUCTURES',
            'VERIFIED_PARSED': 'PARSED_STRUCTURES',
            'INCOMPLETE': 'INCOMPLETE_STRUCTURE_INVENTORY',
            'MALFORMED': 'INVALID_STRUCTURE_DATA',
            'UNSUPPORTED': 'UNSUPPORTED_STRUCTURES_PRESENT',
        }.get(inventory.get('state'), 'INCOMPLETE_STRUCTURE_INVENTORY')
        if inventory.get('state') == 'VERIFIED_PARSED' and any(m.status != 'VALIDATED' for m in models_from_rows(self.working)):
            inventory['classification'] = 'UNSUPPORTED_STRUCTURES_PRESENT'
        return {
            "engineering_version": engineering_version(self),
            "stationing_dirty": stationing_dirty(self),
            "panels_stale": panels_stale(self),
            "panel_stale_reasons": panel_stale_reasons(self),
            "panel_review_state": self.panel_review_state,
            "structure_inventory": inventory,
            "flooding_source": deepcopy(self.flooding_source),
            "distance_ledger": deepcopy(self.distance_ledger),
            "source_units": self.source_units,
            "working_units": self.working_units,
            "unit_contract": deepcopy(self.unit_contract),
            "unit_conversion_status": self.unit_conversion_status,
            "working": [asdict(r) for r in self.working],
            "structures": [m.to_dict() for m in models_from_rows(self.working)],
            "profiles": [asdict(p) for p in self.profiles],
            "landmarks": [asdict(l) for l in self.landmarks],
            "panels": [p.to_dict() for p in self.panels],
            "floodway": [asdict(f) for f in self.floodway],
            "header": asdict(self.header),
            "frequencies": self.frequencies,
            "use_node_names": self.use_node_names,
        }


def model_station_extent(rows: list[WorkingRow]) -> tuple[float, float] | None:
    """Return the usable model station range from Working cross sections."""
    stations = [
        float(row.cumdist)
        for row in rows
        if row.kind == "xs" and math.isfinite(float(row.cumdist))
    ]
    if not stations:
        return None
    return min(stations), max(stations)


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
    prepare_profiles(plan.profiles)
    profile_names = [p.key for p in plan.profiles]
    profile_values_by_id = {p.key: p.values_by_identity() for p in plan.profiles}
    profile_details_by_id = {
        p.key: dict(zip(p.xs_ids, p.values)) for p in plan.profiles
    }
    from .hdf_reader import AlignmentError
    from .model_topology import stable_id
    model_id = getattr(plan, "model_id", "") or str(getattr(plan, "plan_path", "UNKNOWN"))
    expected_ids = {xs.identity for r in plan.reaches for xs in r.cross_sections}
    if len(profile_values_by_id) != len(plan.profiles):
        raise AlignmentError("Duplicate profile names cannot identify hydraulic results uniquely.")
    for name, values in profile_values_by_id.items():
        if set(values) != expected_ids:
            raise AlignmentError(f"Profile {name!r}: geometry/result identities do not match.")
    # Ordinary XS retain their Phase 1 source/result/ordering contract.
    xs_rows = []
    for i, xs in enumerate(xs_list):
        xs_rows.append(WorkingRow(
            id=0, river=xs.river, reach=xs.reach, rs=xs.rs,
            length_chnl=xs.length_channel if i < len(xs_list)-1 else 0.0,
            cumdist=cumdist[i], minchel=xs.minchel,
            profiles={p.key: profile_values_by_id[p.key][xs.identity] for p in plan.profiles},
            raw_profiles={p.profile_id: profile_values_by_id[p.key][xs.identity] for p in plan.profiles},
            # Hydraulic detail records contain source scalars only. A shallow
            # copy gives each working row independent mutable provenance while
            # avoiding a deep-copy traversal for every XS/profile pair.
            hydraulic_values={p.profile_id:{**profile_details_by_id[p.key][xs.identity],"profile_id":p.profile_id,"profile_key":p.key,"xs_identity":xs.identity,"source_path":p.source_path,"source_dataset":p.source_dataset,"result_type":p.result_type,"provenance":"SOURCE" if p.result_type!="DERIVED_MAXIMUM_ENVELOPE" else "DERIVED"} for p in plan.profiles},
            ground_provenance=deepcopy(xs.ground_provenance),
            kind="xs", source_xs_index=i, canonical_length_chnl=xs.length_channel,
            source_length_chnl=xs.length_channel,
            source_order_index=i,
            source_order_rule=getattr(reach, "source_order_status", "UNKNOWN"),
            source_identity=stable_id("xs", model_id, xs.river, xs.reach, xs.rs),
            row_identity=stable_id("xs", model_id, xs.river, xs.reach, xs.rs),
        ))
    sources = list(structs) + list(getattr(plan,"unassigned_structures",[]))
    models = normalize(sources, xs_rows, profile_names, linked_text(plan))
    rows = []
    pending = list(models)
    structures_by_upstream: dict[tuple[str, str, str], list[StructureModel]] = {}
    unlinked_structures: list[StructureModel] = []
    for model in pending:
        if model.upstream is None:
            unlinked_structures.append(model)
        else:
            structures_by_upstream.setdefault(model.upstream, []).append(model)

    def append_structure(model):
        # Unsupported records are retained, not assigned nearest-XS geometry.
        labels = ["CULVERT-UP","CULVERT-DN"] if model.type == "CULVERT" else [model.type]
        upstream_index = next((i for i,x in enumerate(xs_rows) if (x.river,x.reach,x.rs) == model.upstream),None)
        for label in labels:
            source = model.source
            rows.append(WorkingRow(
                id=0, river=model.river, reach=model.reach, rs=model.rs,
                struc=label, kind="struct", cumdist=None, length_chnl=0.0,
                source_xs_index=upstream_index, profiles={name:None for name in profile_names},
                weir_width=source.get("weir_width"), connected_us_rs=model.upstream[2] if model.upstream else "",
                connected_ds_rs=model.downstream[2] if model.downstream else "",
                structure_profile_data=source.get("profile_data",[]),
                hw_connectivity=source.get("hw_connectivity",[]), tw_connectivity=source.get("tw_connectivity",[]),
                lateral_hw_distance=source.get("lateral_hw_distance"), lateral_tw_distance=source.get("lateral_tw_distance"),
                structure_model=model,
                source_identity=model.identity,
                row_identity=stable_id("working-structure", model_id, model.identity, label),
            ))
    for xs in xs_rows:
        rows.append(xs)
        for model in structures_by_upstream.pop((xs.river, xs.reach, xs.rs), ()):
            append_structure(model)
    for model in unlinked_structures:
        append_structure(model)
    for remaining in structures_by_upstream.values():
        for model in remaining:
            append_structure(model)
    for i,row in enumerate(rows,1):
        row.id=i
    sync_rows(rows)
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
    name_by_identity = {(xs.river, xs.reach, xs.rs): (xs.name or "").strip() for xs in xs_list}
    for r in rows:
        if r.kind != "xs":
            continue
        name = name_by_identity.get((r.river, r.reach, r.rs), "")
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
    from .hdf_reader import xs_identity, station_number, AlignmentError
    keys = [xs_identity(row.river, row.reach, row.rs) for row in xs_rows]
    if len({key[:2] for key in keys}) != 1:
        raise AlignmentError("Continuous multi-reach recalculation requires verified junction topology; unsupported.")
    if len(set(keys)) != len(keys):
        raise AlignmentError("Working XS require unique source identities.")
    try:
        stations = [station_number(row.rs) for row in xs_rows]
    except AlignmentError:
        supported = {
            "SOURCE_NONNUMERIC_UPSTREAM_FIRST_VERIFIED",
            "SOURCE_NONNUMERIC_DOWNSTREAM_FIRST_NORMALIZED",
        }
        if (not all(row.source_order_rule in supported for row in xs_rows)
                or [row.source_order_index for row in xs_rows] != list(range(len(xs_rows)))):
            raise AlignmentError(
                "Working XS with nonnumeric labels require unchanged verified source ordering.")
    else:
        if any(a <= b for a, b in zip(stations, stations[1:])):
            raise AlignmentError("Working XS require strictly upstream-first river stations.")

    cumdist = compute_cumdist_from_lengths(
        [row.length_chnl for row in xs_rows],
        start=start,
    )
    if any(not math.isfinite(value) for value in cumdist):
        raise AlignmentError("Calculated CUMDIST is nonfinite; reduce the starting distance or invalid working lengths. No stationing was changed.")
    by_rs = {row.rs: row for row in xs_rows}
    for row in rows:
        if row.kind == "struct" and row.structure_partition_valid is True:
            us = by_rs.get(row.connected_us_rs)
            if us is not None and row.culvert_us_gap is not None and row.culvert_barrel_length is not None:
                if us.length_chnl - row.culvert_us_gap - row.culvert_barrel_length < -0.01:
                    raise AlignmentError(f"XS {us.rs}: edited length cannot contain the validated culvert gap and barrel; CUMDIST was not changed.")
    for row, distance in zip(xs_rows, cumdist):
        row.cumdist = distance

    sync_rows(rows)

    return rows


def detect_drawdowns(rows: list[WorkingRow], profile_names: list[str] | None = None) -> list[dict]:
    """Report Fix Drawdowns candidates without changing working hydraulic values."""
    xs = [row for row in rows if row.kind == "xs"]
    if not xs:
        return []
    names = profile_names or list(xs[0].profiles.keys())
    findings: list[dict] = []
    for name in names:
        carried: Optional[float] = None
        for row in reversed(xs):
            value = row.profiles.get(name)
            if value is None:
                carried = None
            elif carried is None:
                carried = value
            elif value < carried:
                findings.append({
                    "xs_identity": row.source_identity,
                    "profile_key": name,
                    "working_value": value,
                    "qualifying_level": carried,
                    "reason": "UPSTREAM_BELOW_CARRIED_DOWNSTREAM_WSEL",
                })
            else:
                carried = value
    return findings


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
                carried = None
                continue
            if carried is None:
                carried = v
            elif v < carried:
                source_value=next((v for v in r.hydraulic_values.values() if v.get("profile_key")==name),{})
                source_value.update(working_provenance="USER_ENTERED",working_state="VALID")
                r.adjustments.append({"profile_id":source_value.get("profile_id"),"profile_key":name,"original_value":v,"adjusted_value":carried,"reason":"USER_FIX_DRAWDOWNS","provenance":"USER_ENTERED"})
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
    station_range: tuple[float, float] | None = None,
) -> list[PanelDef]:
    """Build FEMA panels from the validated continuous-distance profile.

    A panel may use at most 13 horizontal inches and 9 vertical inches at its
    stated scales.  Horizontal distance and local elevation variation both
    participate in splitting; adjacent panels share their split coordinate so
    the selected station interval remains continuous.

    single_panel: force one panel covering the full station range (may exceed
        paper length at the given H scale — used only when explicitly requested).
    fit_to_page: recompute h_scale so the full reach fits on one panel width,
        keeping V scale (and elev range) as given.
    """
    from .state_contract import MAX_PANELS
    from .export_geometry import validate_scales, page_dimensions
    validate_scales(h_scale, v_scale)
    page_dimensions(page_size, custom_page)
    if usable_width_in is not None and (not math.isfinite(usable_width_in) or usable_width_in <= 0):
        raise ValueError("Panel width must be finite and positive.")
    if not math.isfinite(elev_pad) or elev_pad < 0:
        raise ValueError("Elevation padding must be finite and nonnegative.")
    xs = [r for r in rows if r.kind == "xs"]
    if not xs:
        return []
    if any(r.cumdist is None or not math.isfinite(r.cumdist) for r in xs):
        raise ValueError("Panel station range must be finite.")
    if station_range is not None and (any(not math.isfinite(v) for v in station_range) or station_range[0] >= station_range[1]):
        raise ValueError("Panel station range must be finite and increasing.")
    cum = [r.cumdist for r in xs]
    x_min, x_max = float(min(cum)), float(max(cum))
    if station_range is not None:
        requested_min, requested_max = map(float, station_range)
        x_min = max(x_min, requested_min)
        x_max = min(x_max, requested_max)
        if x_min >= x_max:
            return []
    if not math.isfinite(x_max - x_min):
        raise ValueError("Calculated panel station span must be finite.")
    reach_len = max(1.0, x_max - x_min)
    def profile_series() -> list[list[tuple[float, float | None]]]:
        ordered = sorted(xs, key=lambda row: float(row.cumdist))
        keys = sorted({key for row in ordered for key in row.profiles})
        series = [[(float(row.cumdist), row.minchel) for row in ordered]]
        series.extend([[(float(row.cumdist), row.profiles.get(key)) for row in ordered]
                       for key in keys])
        return series

    # Pre-index finite runs once. Panel splitting calls elevations_between many
    # times; rescanning every XS/profile for each binary-search probe is
    # quadratic for long routes.
    indexed_runs: list[tuple[list[float], list[float]]] = []
    for points in profile_series():
        run_x: list[float] = []
        run_y: list[float] = []
        for station, value in points:
            if value is None:
                if run_x:
                    indexed_runs.append((run_x, run_y))
                    run_x, run_y = [], []
                continue
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Panel elevations must be finite; resolve invalid ground or hydraulic values before Panel Setup.")
            run_x.append(float(station))
            run_y.append(value)
        if run_x:
            indexed_runs.append((run_x, run_y))
    structure_stations: list[float] = []
    structure_elevations: list[tuple[float, ...]] = []
    for row in sorted((item for item in rows if item.kind != "xs" and item.cumdist is not None),
                      key=lambda item: float(item.cumdist)):
        values = tuple(float(value) for value in (row.minchel, row.low_chord, row.high_chord)
                       if value is not None)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Panel elevations must be finite; resolve invalid structure geometry before Panel Setup.")
        structure_stations.append(float(row.cumdist))
        structure_elevations.append(values)

    def elevations_between(lo: float, hi: float) -> list[float]:
        """Values and linearly clipped endpoints inside a continuous interval."""
        values: list[float] = []
        for stations, elevations in indexed_runs:
            left, right = bisect_left(stations, lo), bisect_right(stations, hi)
            values.extend(elevations[left:right])
            for boundary in (lo, hi):
                insertion = bisect_left(stations, boundary)
                if 0 < insertion < len(stations) and stations[insertion] != boundary:
                    x0, x1 = stations[insertion - 1], stations[insertion]
                    if x0 < boundary < x1:
                        ratio = (boundary - x0) / (x1 - x0)
                        values.append(elevations[insertion - 1] + ratio *
                                      (elevations[insertion] - elevations[insertion - 1]))
        left = bisect_left(structure_stations, lo)
        right = bisect_right(structure_stations, hi)
        for item in structure_elevations[left:right]:
            values.extend(item)
        return values

    all_elevations = elevations_between(x_min, x_max)
    y_min = (min(all_elevations) if all_elevations else 0.0) - elev_pad
    y_max = (max(all_elevations) if all_elevations else y_min + 10.0) + elev_pad

    hs = float(h_scale)
    vs = float(v_scale)

    if fit_to_page or single_panel:
        if fit_to_page:
            # H scale so the full reach fits in one fixed-width FEMA panel.
            hs = max(1.0, math.ceil(reach_len / 13.0))
            elev_span = max(1.0, y_max - y_min)
            vs_needed = elev_span / 9.0
            if vs_needed > vs:
                vs = max(1.0, math.ceil(vs_needed * 10) / 10.0)
        elif single_panel:
            hs = max(hs, math.ceil(reach_len / 13.0))
        return [PanelDef(
            panel=1,
            start_station=x_min,
            end_station=x_min + panel_coverage(hs),
            start_elevation=y_min,
            end_elevation=y_min + vertical_panel_coverage(vs),
            h_scale=hs,
            v_scale=vs,
        )]

    span = panel_coverage(hs)
    vertical_span = vertical_panel_coverage(vs)
    panels: list[PanelDef] = []
    if not math.isfinite(span) or span <= 0 or not math.isfinite((x_max - x_min) / span):
        raise ValueError("Panel generation step must be finite and positive; increase horizontal scale.")
    count = max(1, math.ceil((x_max - x_min - 1e-6) / span))
    if not math.isfinite(span) or span <= 0 or x_min + span <= x_min or count > MAX_PANELS:
        raise ValueError(f"Panel generation exceeds {MAX_PANELS} panels or cannot advance; increase horizontal scale or narrow the range.")
    station = x_min
    tolerance = max(1e-8, (x_max - x_min) * 1e-10)
    while station < x_max - tolerance:
        candidate_end = min(x_max, station + span)

        def vertical_bounds(end: float) -> tuple[float, float]:
            values = elevations_between(station, end)
            low = (min(values) if values else 0.0) - elev_pad
            high = (max(values) if values else low + min(10.0, vertical_span)) + elev_pad
            return low, high

        low, high = vertical_bounds(candidate_end)
        if high - low > vertical_span + 1e-8:
            point_low, point_high = vertical_bounds(station)
            if point_high - point_low > vertical_span + 1e-8:
                raise ValueError(
                    f"Profile elevation range at continuous distance {station:g} exceeds the "
                    f"{vertical_span:g}-foot panel height. Increase vertical scale."
                )
            feasible = station
            blocked = candidate_end
            for _ in range(60):
                middle = (feasible + blocked) / 2.0
                middle_low, middle_high = vertical_bounds(middle)
                if middle_high - middle_low <= vertical_span + 1e-8:
                    feasible = middle
                else:
                    blocked = middle
            candidate_end = feasible
            if candidate_end <= station + tolerance:
                raise ValueError(
                    "Profile elevation changes too steeply for the selected vertical scale. "
                    "Increase vertical scale or narrow Revision Limits."
                )
            low, high = vertical_bounds(candidate_end)

        if len(panels) >= MAX_PANELS:
            raise ValueError(f"Panel generation exceeds {MAX_PANELS} panels; increase horizontal or vertical scale or narrow the range.")
        # Retain the established full-width final panel when no vertical split
        # was needed; vertically split panels end at their engineering boundary.
        stored_end = station + span if candidate_end >= x_max - tolerance and high - low <= vertical_span + 1e-8 else candidate_end
        panels.append(PanelDef(
            panel=len(panels) + 1,
            start_station=station,
            end_station=stored_end,
            start_elevation=low,
            end_elevation=low + vertical_span,
            h_scale=hs,
            v_scale=vs,
        ))
        station = candidate_end
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
    db.structure_inventory=deepcopy(getattr(plan,"structure_inventory",{"state":"VERIFIED_NONE"}))
    db.source_units=getattr(plan,"source_units","INTERNAL_US_CUSTOMARY")
    db.working_units=getattr(plan,"working_units","INTERNAL_US_CUSTOMARY")
    db.unit_contract=deepcopy(getattr(plan,"unit_contract",{}))
    db.unit_conversion_status=getattr(plan,"unit_conversion_status","LEGACY_US_VALIDATED")
    db.header.source_datum=getattr(plan,"source_datum","UNKNOWN")
    if db.header.source_datum!="UNKNOWN" and db.header.datum_provenance!="USER_ENTERED":
        db.header.vertical_datum=db.header.source_datum
        db.header.datum_provenance="SOURCE"
    # Letters are a reviewer-authored drawing decision.  A newly opened model
    # starts without generated letters; saved-project restoration reapplies its
    # durable label overrides and the explicit Auto Letters action remains
    # available to users who want generated labels.
    working = build_working_from_plan(plan, reach_idx=reach_idx, cumdist=cumdist)
    db.working = working
    db.next_id = (max((r.id for r in working), default=0) + 1)

    # Profile metadata — try to auto-map FEMA frequencies by name heuristics
    for p in plan.profiles:
        freq, color, ltype, legend, layer, lw = _default_profile_style(p.name, db.frequencies)
        db.profiles.append(ProfileMeta(
            name=p.name, profile_id=p.profile_id, key=p.key, source_name=p.name, display_name=p.name, result_type=p.result_type, source_path=p.source_path, source_dataset=p.source_dataset, frequency=freq, color=color, linetype=ltype,
            lineweight=lw, legend_text=p.name + (" (derived saved-timestep envelope)" if p.result_type=="DERIVED_MAXIMUM_ENVELOPE" else ""), layer=f"PROF_{p.profile_id}",
            selected=(p.profile_id == plan.profiles[0].profile_id) if plan.profiles else False,
        ))
    if db.profiles:
        db.profiles[0].selected = True

    try:
        db.panels = auto_panels(
            working, h_scale=db.header.h_scale, v_scale=db.header.v_scale,
            page_size=db.header.page_size,
        )
    except ValueError as exc:
        # A valid source may not fit the default presentation scale.  Loading
        # remains available, but Panel Setup must obtain an explicit workable
        # scale before a reviewed/exportable panel set exists.
        message = str(exc)
        if not (message.startswith("Profile elevation range at continuous distance")
                or message.startswith("Profile elevation changes too steeply")):
            raise
        db.panels = []
    if plan.reaches:
        r = plan.reaches[reach_idx]
        db.header.stream_name = f"{r.river} {r.reach}".upper()
    from .state_contract import seal_stationing, review_panels
    db.stationing_start = min((r.cumdist for r in working if r.kind == "xs"), default=0.0)
    seal_stationing(db)
    review_panels(db)
    return db


def _default_profile_style(name: str, freqs: list[dict]) -> tuple[str, str, str, str, str, float]:
    # Names convey no verified frequency semantics, including an exact "1%".
    return ("", "#196bd6", "CONTINUOUS", name, "", 0.30)


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
