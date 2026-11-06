"""HDF reader for HEC-RAS 6.x/7.x result and geometry files.

Implements a read-only adapter over h5py that exposes the data RAS-Plot v3 requires:

  * River/reach centreline + cross-section locations and bank stations
  * Cross-section attributes (River, Reach, RS, Length Channel, bank stations)
  * Structures (bridges, culverts, inline weirs) with low/high chord, invert
  * Profile results: WSEL, energy, crit depth (where present), flow per cross-section
  * Time-series (unsteady) and steady-profile result blocks

Schema references checked against the Muncie sample (HEC-RAS 6.4.1 June 2023).
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime
from .hydraulics import normalize_value, prepare_profiles, ground_value
from .engineering_units import EngineeringUnitContract, UnitContractError
from .hdf_schema_adapters import REGISTRY, HdfSchemaAdapter, SchemaAdapterError
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import h5py
import numpy as np


class AlignmentError(ValueError):
    """Source data cannot establish an unambiguous engineering association."""


def source_value(value):
    if isinstance(value, dict):
        return {k:source_value(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [source_value(v) for v in value]
    if isinstance(value, (bytes, np.bytes_)):
        return _s(value)
    if isinstance(value, np.ndarray):
        return [source_value(v) for v in value]
    if isinstance(value, np.generic):
        return source_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def source_record(record):
    return {key: source_value(record[key]) for key in record.dtype.names or ()}


def _walk_datasets(handle) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    handle.visititems(lambda name, obj: found.append((name, obj))
                      if isinstance(obj, h5py.Dataset) else None)
    return found


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def xs_identity(river: str, reach: str, rs: str) -> tuple[str, str, str]:
    """Case-sensitive source identifiers; never use RS as a global key."""
    key = tuple(_s(value) for value in (river, reach, rs))
    if not all(key):
        raise AlignmentError(f"Incomplete cross-section identity: {key!r}")
    return key


def channel_length(value: Any, context: str = "Channel length") -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AlignmentError(f"{context}: expected an explicit finite nonnegative length, got {value!r}")
    if not math.isfinite(result) or result < 0:
        raise AlignmentError(f"{context}: expected a finite nonnegative length, got {value!r}")
    return result


def station_number(rs: str) -> float:
    try:
        value = float(_s(rs))
    except (TypeError, ValueError):
        raise AlignmentError(f"Station {rs!r}: nonnumeric ordering is unsupported; provide verified XS ordering.")
    if not math.isfinite(value):
        raise AlignmentError(f"Station {rs!r}: station must be finite.")
    return value


def _convert_optional_length(value: Any, contract: EngineeringUnitContract) -> float | None:
    parsed = _f(value)
    return None if parsed is None else contract.source_to_working(parsed)


def _convert_profile_points(values: Any, contract: EngineeringUnitContract) -> Any:
    if not isinstance(values, (list, tuple)):
        return values
    converted = []
    for item in values:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return values
        x, y = _f(item[0]), _f(item[1])
        if x is None or y is None:
            return values
        converted.append((contract.source_to_working(x), contract.source_to_working(y)))
    return converted


_OPENING_LENGTH_FIELDS = {
    "rise", "span", "length", "us distance", "invert us", "invert ds",
    "us invert", "ds invert", "us station", "ds station",
}


def _convert_opening_record(record: dict[str, Any], contract: EngineeringUnitContract) -> dict[str, Any]:
    converted = {}
    for key, value in record.items():
        normalized = key.strip().lower()
        if normalized in _OPENING_LENGTH_FIELDS:
            parsed = _f(value)
            converted[key] = value if parsed is None else contract.source_to_working(parsed)
        elif key == "barrel_records" and isinstance(value, list):
            converted[key] = [_convert_opening_record(item, contract) for item in value]
        else:
            converted[key] = value
    return converted


def apply_engineering_unit_contract(plan: "Plan", contract: EngineeringUnitContract) -> None:
    """Convert source engineering lengths/elevations once into canonical feet.

    Map-coordinate polylines and river-station identity strings are deliberately
    untouched. Raw source quantities remain attached to their source objects.
    """
    if plan.unit_conversion_status == "COMPLETE":
        raise AlignmentError("Engineering unit conversion was already applied; refusing to double-convert the plan.")
    for reach in plan.reaches:
        for xs in reach.cross_sections:
            xs.source_engineering = {
                "unit": contract.source_length_unit.value,
                "length_channel": xs.length_channel,
                "left_bank": xs.left_bank,
                "right_bank": xs.right_bank,
                "station_elevation": list(xs.station_elevation),
                "minchel": xs.minchel,
            }
            xs.length_channel = contract.source_to_working(xs.length_channel)
            xs.left_bank = contract.source_to_working(xs.left_bank)
            xs.right_bank = contract.source_to_working(xs.right_bank)
            xs.station_elevation = list(contract.source_points_to_working(xs.station_elevation))
            xs.minchel = _convert_optional_length(xs.minchel, contract)
            xs.length_provenance.update({
                "source_unit": contract.source_length_unit.value,
                "working_unit": contract.working_length_unit.value,
                "working_value": xs.length_channel,
                "conversion_contract": contract.conversion_contract,
            })
            if xs.ground_provenance.get("banks"):
                xs.ground_provenance["source_banks"] = list(xs.ground_provenance["banks"])
                xs.ground_provenance["banks"] = [
                    contract.source_to_working(value) for value in xs.ground_provenance["banks"]]
            xs.ground_provenance.update({
                "source_unit": contract.source_length_unit.value,
                "working_unit": contract.working_length_unit.value,
                "conversion_contract": contract.conversion_contract,
            })
        for structure in reach.structures:
            _convert_structure_engineering(structure, contract)
        reach.source_connectivity_raw = dict(reach.source_connectivity)
        for key in ("Junction to US XS", "DS XS to Junction"):
            if key in reach.source_connectivity:
                converted = _convert_optional_length(reach.source_connectivity[key], contract)
                if converted is not None:
                    reach.source_connectivity[key] = converted
    for structure in plan.unassigned_structures:
        _convert_structure_engineering(structure, contract)
    plan.source_junction_records = [dict(item) for item in plan.junction_records]
    for record in plan.junction_records:
        for key in ("Junction to US XS", "DS XS to Junction"):
            if key in record:
                converted = _convert_optional_length(record[key], contract)
                if converted is not None:
                    record[key] = converted
    for profile in plan.profiles:
        for value in profile.values:
            if value.get("value") is not None:
                source = float(value["value"])
                value["source_value"] = source
                value["source_unit"] = contract.source_length_unit.value
                value["value"] = contract.source_to_working(source)
                value["working_unit"] = contract.working_length_unit.value
                value["conversion_contract"] = contract.conversion_contract
        profile.wsel = [value.get("value") for value in profile.values]
    plan.working_units = "INTERNAL_US_CUSTOMARY"
    plan.unit_contract = contract.to_manifest()
    plan.unit_conversion_status = "COMPLETE"


def _convert_structure_engineering(structure: "Structure", contract: EngineeringUnitContract) -> None:
    structure.source_engineering = {
        "unit": contract.source_length_unit.value,
        "upstream_distance": structure.upstream_distance,
        "weir_width": structure.weir_width,
        "low_chord": structure.low_chord,
        "high_chord": structure.high_chord,
        "minchel": structure.minchel,
        "profile_data": list(structure.profile_data),
        "lateral_hw_distance": structure.lateral_hw_distance,
        "lateral_tw_distance": structure.lateral_tw_distance,
    }
    for name in ("upstream_distance", "weir_width", "low_chord", "high_chord", "minchel",
                 "culvert_length", "culvert_us_distance", "invert_us", "invert_ds",
                 "lateral_hw_distance", "lateral_tw_distance"):
        setattr(structure, name, _convert_optional_length(getattr(structure, name), contract))
    structure.profile_data = _convert_profile_points(structure.profile_data, contract)
    structure.hw_connectivity = [(identity, contract.source_to_working(value))
                                 for identity, value in structure.hw_connectivity]
    structure.tw_connectivity = [(identity, contract.source_to_working(value))
                                 for identity, value in structure.tw_connectivity]
    structure.source_profiles_raw = source_value(structure.source_profiles)
    structure.source_profiles = {
        key: _convert_profile_points(value, contract)
        for key, value in structure.source_profiles.items()
    }
    structure.source_openings = source_value(structure.openings)
    structure.openings = [_convert_opening_record(record, contract) for record in structure.openings]


# --------------------------------------------------------------------------- #
# Data classes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class CrossSection:
    river: str
    reach: str
    rs: str
    name: str
    length_channel: float
    left_bank: float
    right_bank: float
    station_elevation: list[tuple[float, float]] = field(default_factory=list)
    polypoints: list[tuple[float, float]] = field(default_factory=list)  # x,y of cross-section line
    minchel: Optional[float] = None
    index: int = 0
    ground_provenance: dict = field(default_factory=dict)
    length_provenance: dict = field(default_factory=dict)
    source_engineering: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str, str]:
        return xs_identity(self.river, self.reach, self.rs)


@dataclass
class Structure:
    river: str
    reach: str
    rs: str
    type: str            # Bridge / Culvert / Inline Weir / Lateral Structure
    name: str
    upstream_distance: float
    weir_width: float
    low_chord: Optional[float] = None
    high_chord: Optional[float] = None
    minchel: Optional[float] = None
    poly_points: list[tuple[float, float]] = field(default_factory=list)
    profile_data: list[tuple[float, float]] = field(default_factory=list)  # station, elevation of centerline
    index: int = 0
    us_rs: str = ""
    ds_rs: str = ""
    culvert_length: Optional[float] = None
    culvert_us_distance: Optional[float] = None
    invert_us: Optional[float] = None
    invert_ds: Optional[float] = None
    hw_connectivity: list[tuple[str, float]] = field(default_factory=list)
    tw_connectivity: list[tuple[str, float]] = field(default_factory=list)
    lateral_hw_distance: Optional[float] = None
    lateral_tw_distance: Optional[float] = None
    source_id: str = ""
    source_path: str = ""
    source_attributes: dict[str, Any] = field(default_factory=dict)
    source_profiles: dict[str, Any] = field(default_factory=dict)
    source_connections: list[dict[str, Any]] = field(default_factory=list)
    openings: list[dict[str, Any]] = field(default_factory=list)
    source_issues: list[str] = field(default_factory=list)
    source_engineering: dict[str, Any] = field(default_factory=dict)
    source_profiles_raw: dict[str, Any] = field(default_factory=dict)
    source_openings: list[dict[str, Any]] = field(default_factory=list)



@dataclass
class ProfileResult:
    name: str                    # profile date/label as HEC-RAS records it
    wsel: list[float]            # per cross-section, length == number of cross sections
    energy: Optional[list[float]] = None
    flow: Optional[list[float]] = None
    xs_ids: list[tuple[str, str, str]] = field(default_factory=list)

    profile_id: str = ""
    key: str = ""
    source_path: str = ""
    source_dataset: str = ""
    result_type: str = "SOURCE_WSEL"
    values: list[dict] = field(default_factory=list)
    time_provenance: dict = field(default_factory=dict)

    def values_by_identity(self) -> dict[tuple[str, str, str], float]:
        if len(self.xs_ids) != len(self.wsel) or len(set(self.xs_ids)) != len(self.xs_ids):
            raise AlignmentError(f"Profile {self.name!r}: missing, duplicate or mismatched XS identities; positional assignment is unsupported.")
        return dict(zip(self.xs_ids, self.wsel))


@dataclass
class ReachGeometry:
    river: str
    reach: str
    centerline: list[tuple[float, float]]
    cross_sections: list[CrossSection]
    structures: list[Structure]
    rs_to_index: dict[str, int] = field(default_factory=dict)  # RS string -> index in cross_sections
    source_id: str = ""
    source_path: str = ""
    source_connectivity: dict[str, Any] = field(default_factory=dict)
    source_order_status: str = "UNKNOWN"
    source_order_provenance: dict[str, Any] = field(default_factory=dict)
    source_connectivity_raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HecRasProject:
    prj_path: Path
    plans: list["Plan"] = field(default_factory=list)


@dataclass
class Plan:
    name: str
    plan_path: Path
    geometry_path: Optional[Path]
    flow_path: Optional[Path]
    unsteady_path: Optional[Path]
    results_path: Optional[Path]            # plan.*.hdf
    flow_type: str = "steady"               # steady / unsteady
    profiles: list[ProfileResult] = field(default_factory=list)
    reaches: list[ReachGeometry] = field(default_factory=list)
    unassigned_structures: list[Structure] = field(default_factory=list)
    source_units: str = "UNKNOWN"
    source_datum: str = "UNKNOWN"
    structure_inventory: dict = field(default_factory=lambda: {"state":"VERIFIED_NONE"})
    model_id: str = ""
    ras_version: str = "UNKNOWN"
    capabilities: Any = None
    topology: Any = None
    junction_records: list[dict[str, Any]] = field(default_factory=list)
    source_junction_records: list[dict[str, Any]] = field(default_factory=list)
    working_units: str = "INTERNAL_US_CUSTOMARY"
    unit_contract: dict[str, str] = field(default_factory=dict)
    unit_conversion_status: str = "UNVERIFIED"
    schema_adapter: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _s(b: Any) -> str:
    """Decode an hdf byte-string into a clean python str."""
    if b is None:
        return ""
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace").rstrip("\x00").strip()
    if isinstance(b, np.ndarray):
        if b.size == 1:
            return _s(b.reshape(-1)[0])
        raise AlignmentError(f"Unsupported string array shape {b.shape}; expected one source label per result row.")
    return str(b).strip()


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _sorted_rs(xs_attrs: np.ndarray) -> np.ndarray:
    """Sort cross-section attributes by descending numeric RS (upstream first),
    which matches HEC-RAS's internal convention."""
    order = sorted(range(len(xs_attrs)), key=lambda i: (
        _s(xs_attrs[i]["River"]), _s(xs_attrs[i]["Reach"]),
        -station_number(_s(xs_attrs[i]["RS"])), _s(xs_attrs[i]["RS"]),
    ))
    return xs_attrs[order]


# --------------------------------------------------------------------------- #
# Reader                                                                       #
# --------------------------------------------------------------------------- #


class HdfReader:
    """Reads HEC-RAS 6.x/7.x plan/geometry HDF files."""

    def __init__(self, plan_hdf_path: str | Path):
        self.plan_hdf_path = Path(plan_hdf_path)
        self.unassigned_structures = []
        self.structure_inventory = {"state":"VERIFIED_NONE"}
        self.junction_records = []
        self.result_identity_adapter = "UNRESOLVED_INSPECTION"
        self.schema_adapter: HdfSchemaAdapter | None = None
        self.schema_adapter_evidence: dict[str, Any] = {}

    def inspect(self):
        """Discover readable schema capabilities without asserting export support."""
        from .model_topology import HdfCapabilities
        result = HdfCapabilities()
        if not self.plan_hdf_path.is_file():
            raise FileNotFoundError(self.plan_hdf_path)
        with h5py.File(self.plan_hdf_path, "r") as source:
            result.ras_version = _s(source.attrs.get("File Version", "UNKNOWN")) or "UNKNOWN"
            raw_units = _s(source.attrs.get("Units System", "UNKNOWN")) or "UNKNOWN"
            try:
                result.unit_system = EngineeringUnitContract.from_source_system(raw_units).source_system.value
            except UnitContractError:
                result.unit_system = raw_units
            result.results_available = "SUPPORTED" if "Results" in source else "MISSING"
            result.result_profiles_available = result.results_available
            result.available_result_datasets = sorted(
                name for name, obj in _walk_datasets(source) if name.startswith("Results/")
            )
            datum = _s(source.attrs.get("Vertical Datum", "UNKNOWN")) or "UNKNOWN"
            result.datum_status = "SUPPORTED" if datum != "UNKNOWN" else "UNKNOWN"
            result.datum_provenance = {"source_path": str(self.plan_hdf_path),
                                       "attribute": "Vertical Datum", "value": datum}
            plan_info = source.get("Plan Data/Plan Information")
            if plan_info is not None:
                result.plan_identity = {
                    key: _s(plan_info.attrs.get(key, ""))
                    for key in ("Plan Name", "Plan Title", "Plan Filename", "Plan ShortID")
                    if key in plan_info.attrs
                }
        try:
            geometry_path = self._find_geometry()
        except (AlignmentError, OSError, KeyError) as exc:
            result.warnings.append(str(exc))
            return result
        with h5py.File(geometry_path, "r") as geometry:
            result.geometry_identity = {
                "source_path": str(geometry_path),
                "file_type": _s(geometry.attrs.get("File Type", "UNKNOWN")),
                "file_version": _s(geometry.attrs.get("File Version", "UNKNOWN")),
            }
            result.geometry_available = "SUPPORTED" if "Geometry" in geometry else "MISSING"
            result.available_geometry_datasets = sorted(
                name for name, _obj in _walk_datasets(geometry) if name.startswith("Geometry/")
            )
            xs = geometry.get("Geometry/Cross Sections/Attributes")
            result.cross_sections_available = "SUPPORTED" if xs is not None else "MISSING"
            center = geometry.get("Geometry/River Centerlines/Attributes")
            required = {"River Name", "Reach Name", "US Type", "US Name", "DS Type", "DS Name"}
            distance_fields = {"DS XS to Junction", "Junction to US XS"}
            fields = set(center.dtype.names or ()) if center is not None else set()
            result.river_centerline_connectivity_available = "SUPPORTED" if required <= fields else ("UNVALIDATED" if center is not None else "MISSING")
            result.junction_distances_available = "SUPPORTED" if distance_fields <= fields else "MISSING"
            result.junctions_available = "SUPPORTED" if geometry.get("Geometry/Junctions/Attributes") is not None else "MISSING"
            structures = geometry.get("Geometry/Structures/Attributes")
            if structures is not None:
                structure_fields = structures.dtype.names or ()
                types = sorted({_s(row["Type"]) for row in structures if "Type" in structure_fields})
                result.structures_detected = [{"type": value, "status": "SUPPORTED" if value.upper() in {"CULVERT", "LATERAL STRUCTURE", "INLINE WEIR"} else "UNVALIDATED"} for value in types]
                result.unsupported_or_unvalidated_features.extend(
                    value for value in types if value.upper() not in {"CULVERT", "LATERAL STRUCTURE", "INLINE WEIR"}
                )
        if result.unit_system not in {"US Customary", "SI", "UNKNOWN"}:
            result.unsupported_or_unvalidated_features.append(f"Unit system {result.unit_system}")
        return result

    # -- entry points ------------------------------------------------------- #

    def read_plan(self) -> Plan:
        if not self.plan_hdf_path.exists():
            raise FileNotFoundError(self.plan_hdf_path)
        capabilities = self.inspect()
        plan = Plan(
            name=self.plan_hdf_path.stem,
            plan_path=self.plan_hdf_path,
            geometry_path=self._find_geometry(),
            flow_path=None,
            unsteady_path=None,
            results_path=self.plan_hdf_path,
            flow_type="unsteady" if self._has_unsteady_results() else "steady",
            capabilities=capabilities,
        )
        with h5py.File(self.plan_hdf_path,"r") as f:
            plan.source_units = _s(f.attrs.get("Units System","UNKNOWN"))
            plan.source_datum = _s(f.attrs.get("Vertical Datum","UNKNOWN"))
            plan.ras_version = _s(f.attrs.get("File Version", "UNKNOWN")) or "UNKNOWN"
        plan.model_id = "model-" + file_fingerprint(self.plan_hdf_path)[:20]
        try:
            unit_contract = EngineeringUnitContract.from_source_system(plan.source_units)
        except UnitContractError as exc:
            raise AlignmentError(str(exc)) from exc
        plan.source_units = unit_contract.source_system.value
        if plan.geometry_path is not None:
            with h5py.File(plan.geometry_path, "r") as geometry_source:
                geometry_units = _s(geometry_source.attrs.get("Units System", ""))
            if geometry_units:
                try:
                    geometry_system = EngineeringUnitContract.from_source_system(geometry_units).source_system
                except UnitContractError as exc:
                    raise AlignmentError(f"Geometry source units are unsupported: {geometry_units!r}.") from exc
                if geometry_system is not unit_contract.source_system:
                    raise AlignmentError(
                        f"Plan/results units {plan.source_units!r} conflict with geometry units {geometry_system.value!r}.")
            elif unit_contract.source_system.value == "SI" and plan.geometry_path != plan.plan_path:
                raise AlignmentError(
                    "SI companion geometry has no Units System metadata; authoritative conversion is unavailable.")
        try:
            with h5py.File(self.plan_hdf_path, "r") as plan_source, \
                    h5py.File(plan.geometry_path or self.plan_hdf_path, "r") as geometry_source:
                self.schema_adapter, self.schema_adapter_evidence = REGISTRY.select(
                    plan_source, geometry_source)
        except SchemaAdapterError as exc:
            raise AlignmentError(str(exc)) from exc
        plan.schema_adapter = dict(self.schema_adapter_evidence)
        plan.reaches = self._read_reaches_geometry()
        if not plan.reaches:
            raise AlignmentError("No ordinary cross-section geometry is available in the selected plan geometry.")
        plan.unassigned_structures = self.unassigned_structures
        plan.structure_inventory = self.structure_inventory
        plan.junction_records = list(self.junction_records)
        # Source-result structure groups are positive evidence even if the
        # geometry inventory disappeared. Do not derive faces from these groups.
        if plan.structure_inventory["state"] in ("VERIFIED_NONE", "VERIFIED_PARSED"):
            parsed = [s for r in plan.reaches for s in r.structures] + plan.unassigned_structures
            evidence = []
            with h5py.File(self.plan_hdf_path,"r") as f:
                def structure_evidence(name, obj):
                    if not name.startswith("Results/") or not isinstance(obj,h5py.Group): return
                    kind = name.rsplit("/",1)[-1]
                    if kind in {"Lateral Structures","Inline Structures","Bridges","Culverts"} and len(obj):
                        evidence.append((name, kind, len(obj)))
                f.visititems(structure_evidence)
            for path, kind, count in evidence:
                token = {"Lateral Structures":"LATERAL", "Inline Structures":"INLINE", "Bridges":"BRIDGE", "Culverts":"CULVERT"}[kind]
                if count > sum(token in s.type.upper() for s in parsed):
                    plan.structure_inventory = {"state":"INCOMPLETE", "reason":f"{path} contains structure results not accounted for by the geometry inventory."}
                    break
            if not parsed and plan.structure_inventory["state"] == "VERIFIED_NONE":
                from .structure_model import linked_text
                if linked_text(plan):
                    plan.structure_inventory = {"state":"INCOMPLETE", "reason":"Plan-linked geometry text contains structures absent from the HDF inventory."}
        plan.profiles = self._read_profiles(plan.reaches)
        prepare_profiles(plan.profiles)
        apply_engineering_unit_contract(plan, unit_contract)
        from .model_topology import build_topology
        plan.topology = build_topology(plan.model_id, plan.reaches, plan.junction_records,
                                       str(plan.geometry_path or plan.plan_path))
        capabilities.result_profiles_available = "SUPPORTED" if plan.profiles else "MISSING"
        capabilities.schema_adapter = self.schema_adapter.name
        capabilities.workflow_capabilities.append({
            "capability": "HDF_SCHEMA_ADAPTER",
            "status": self.schema_adapter.confidence,
            "evidence": dict(self.schema_adapter_evidence),
        })
        return plan

    # -- file / sibling helpers -------------------------------------------- #

    def _find_geometry(self) -> Optional[Path]:
        with h5py.File(self.plan_hdf_path, "r") as f:
            if "Geometry" in f:
                return self.plan_hdf_path

        with h5py.File(self.plan_hdf_path, "r") as f:
            info = f.get("Plan Data/Plan Information")
            filename = _s(info.attrs.get("Geometry Filename", "")) if info is not None else ""
        if not filename:
            raise AlignmentError("Embedded geometry is missing and the plan does not identify its companion geometry; refusing to choose a .g*.hdf by filename order.")
        linked = Path(filename)
        if not linked.is_absolute():
            linked = self.plan_hdf_path.parent / linked
        if linked.suffix.lower() != ".hdf":
            linked = Path(str(linked) + ".hdf")
        if not linked.is_file():
            raise AlignmentError(f"Plan-linked geometry HDF is missing: {linked}")
        return linked

    def _has_unsteady_results(self) -> bool:
        with h5py.File(self.plan_hdf_path, "r") as f:
            return "Results/Unsteady" in f

    # -- geometry ----------------------------------------------------------- #

    def _read_reaches_geometry(self) -> list[ReachGeometry]:
        geom_path = self._find_geometry()
        if geom_path is None or not geom_path.exists():
            return []
        reaches_map: dict[tuple[str, str], ReachGeometry] = {}
        self.unassigned_structures = []
        self.structure_inventory = {"state":"VERIFIED_NONE"}

        with h5py.File(geom_path, "r") as g:
            xs = g.get("/Geometry/Cross Sections")
            structs = g.get("/Geometry/Structures")
            centerlines = g.get("/Geometry/River Centerlines")
            junctions = g.get("/Geometry/Junctions/Attributes")
            self.junction_records = [source_record(row) for row in junctions[()]] if junctions is not None else []

            # ----- River centerline attrs ------------------------------ #
            center_attrs = None
            if centerlines is not None and "Attributes" in centerlines:
                center_attrs = centerlines["Attributes"][()]
            cl_info = centerlines["Polyline Info"][()] if centerlines is not None and "Polyline Info" in centerlines else None
            cl_parts = centerlines["Polyline Parts"][()] if centerlines is not None and "Polyline Parts" in centerlines else None
            cl_pts   = centerlines["Polyline Points"][()] if centerlines is not None and "Polyline Points" in centerlines else None

            # ----- Cross-section attrs --------------------------------- #
            if xs is not None and "Attributes" in xs:
                # Slice geometry using the SOURCE row index, then order complete
                # objects. Sorting only attributes would detach their geometry.
                xs_attrs = xs["Attributes"][()]
                xs_info = xs["Polyline Info"][()] if "Polyline Info" in xs else None
                xs_parts = xs["Polyline Parts"][()] if "Polyline Parts" in xs else None
                xs_pts = xs["Polyline Points"][()] if "Polyline Points" in xs else None
                se_info = xs["Station Elevation Info"][()] if "Station Elevation Info" in xs else None
                se_values = xs["Station Elevation Values"][()] if "Station Elevation Values" in xs else None

                fields = xs_attrs.dtype.names or ()
                for i, rec in enumerate(xs_attrs):
                    river = _s(rec["River"])
                    reach = _s(rec["Reach"])
                    rs = _s(rec["RS"])
                    if "Name" in fields:
                        name = _s(rec["Name"])
                    elif "Node Name" in fields:
                        name = _s(rec["Node Name"])
                    else:
                        name = ""
                    raw_length = _f(rec["Len Channel"])
                    length_channel = raw_length if raw_length is not None else math.nan
                    left_bank = _f(rec["Left Bank"])
                    right_bank = _f(rec["Right Bank"])
                    # Polyline points for this xs (index i into the parts/pts arrays)
                    polypts = self._slice_polyline(xs_info, xs_parts, xs_pts, i) if xs_info is not None else []
                    # Station-elevation pairs for invert computation
                    se_pairs = self._slice_station_elevation(se_info, se_values, i) if se_info is not None else []
                    minchel, ground_provenance = ground_value(se_pairs, left_bank, right_bank)
                    cx = CrossSection(
                        river=river,
                        reach=reach,
                        rs=rs,
                        name=name,
                        length_channel=length_channel,
                        left_bank=left_bank,
                        right_bank=right_bank,
                        station_elevation=se_pairs,
                        polypoints=polypts,
                        minchel=minchel,
                        ground_provenance=ground_provenance,
                        length_provenance={"source_path": str(geom_path),
                                           "dataset": "Geometry/Cross Sections/Attributes",
                                           "field": "Len Channel",
                                           "raw_value": source_value(rec["Len Channel"]),
                                           "status": "SOURCE" if raw_length is not None else "MISSING"},
                        index=i,
                    )
                    key = (river, reach)
                    if key not in reaches_map:
                        reaches_map[key] = ReachGeometry(
                            river=river, reach=reach, centerline=[], cross_sections=[], structures=[],
                            source_id=f"Cross Sections/Attributes[{i}]", source_path=str(geom_path),
                        )
                    reaches_map[key].cross_sections.append(cx)
                    reaches_map[key].rs_to_index[rs] = i

            # ----- Structures ----------------------------------------- #
            if structs is not None:
                if "Attributes" not in structs:
                    self.structure_inventory = {"state":"INCOMPLETE", "reason":"Geometry/Structures exists but Attributes is missing."}
                else:
                    inventory = structs["Attributes"]
                    fields = inventory.dtype.names or ()
                    if not {"River","Reach","RS","Type"}.issubset(fields) or len(inventory.shape) != 1:
                        raise AlignmentError("Malformed Geometry/Structures/Attributes: structured River, Reach, RS and Type records are required.")
                    self.structure_inventory = {"state":"VERIFIED_PARSED" if len(inventory) else "VERIFIED_NONE"}
                    if len(inventory) == 0 and any(k != "Attributes" and getattr(v, "size", 0) > 0 for k,v in structs.items()):
                        self.structure_inventory = {"state":"INCOMPLETE", "reason":"Empty structure Attributes conflicts with structure datasets."}
                    if "Culvert Groups" in structs and "Attributes" not in structs["Culvert Groups"]:
                        self.structure_inventory = {"state":"INCOMPLETE", "reason":"Culvert Groups/Attributes is missing."}
            if structs is not None and "Attributes" in structs:
                s_attrs = structs["Attributes"][()]
                s_cl_info = structs["Centerline Info"][()] if "Centerline Info" in structs else None
                s_cl_parts = structs["Centerline Parts"][()] if "Centerline Parts" in structs else None
                s_cl_pts = structs["Centerline Points"][()] if "Centerline Points" in structs else None
                s_profile_data = structs["Profile Data"][()] if "Profile Data" in structs else None
                s_table_info = structs["Table Info"][()] if "Table Info" in structs else None
                culvert_groups = structs.get("Culvert Groups")
                culvert_group_attrs = None
                culvert_barrel_attrs = None
                if culvert_groups is not None and "Attributes" in culvert_groups:
                    culvert_group_attrs = culvert_groups["Attributes"][()]
                    group_fields = culvert_group_attrs.dtype.names or ()
                    if "Barrels" in culvert_groups and "Attributes" in culvert_groups["Barrels"]:
                        culvert_barrel_attrs = culvert_groups["Barrels"]["Attributes"][()]
                    if "Structure ID" in group_fields:
                        invalid_ids = [int(row["Structure ID"]) for row in culvert_group_attrs
                                       if int(row["Structure ID"]) < 0 or int(row["Structure ID"]) >= len(s_attrs)]
                        if invalid_ids:
                            self.structure_inventory = {"state":"INCOMPLETE", "reason":"Culvert group Structure ID is outside the structure inventory."}
                    elif {"River", "Reach", "RS"}.issubset(group_fields):
                        known = {(_s(r["River"]), _s(r["Reach"]), _s(r["RS"])) for r in s_attrs}
                        if any((_s(r["River"]), _s(r["Reach"]), _s(r["RS"])) not in known for r in culvert_group_attrs):
                            self.structure_inventory = {"state":"INCOMPLETE", "reason":"Culvert group identities are absent from the structure inventory."}
                    else:
                        self.structure_inventory = {"state":"UNSUPPORTED", "reason":"Culvert groups lack Structure ID and explicit river/reach/RS identities."}
                if s_table_info is not None:
                    if len(s_table_info) != len(s_attrs):
                        self.structure_inventory = {"state":"INCOMPLETE", "reason":"Structure Table Info count differs from Attributes."}
                    for record in s_table_info:
                        for key in s_table_info.dtype.names or ():
                            if not key.endswith(" (Count)"): continue
                            prefix = key.removesuffix(" (Count)")
                            index_key = prefix + " (Index)"
                            if index_key not in s_table_info.dtype.names: continue
                            count, start = int(record[key]), int(record[index_key])
                            if count > 0 and (s_profile_data is None or start < 0 or start + count > len(s_profile_data)):
                                self.structure_inventory = {"state":"MALFORMED", "reason":f"Structure {prefix} references missing or truncated Profile Data."}
                connectivity = structs["User Defined Weir Connectivity"][()] \
                    if "User Defined Weir Connectivity" in structs else None
                fields = s_attrs.dtype.names
                for i, rec in enumerate(s_attrs):
                    river = _s(rec["River"]) if "River" in fields else ""
                    reach_name = _s(rec["Reach"]) if "Reach" in fields else ""
                    rs = _s(rec["RS"]) if "RS" in fields else ""
                    stype = _s(rec["Type"]) if "Type" in fields else ""
                    name = ""
                    if "Node Name" in fields and rec["Node Name"]:
                        name = _s(rec["Node Name"])
                    elif "Groupname" in fields and rec["Groupname"]:
                        name = _s(rec["Groupname"])
                    up_dist = _f(rec["Upstream Distance"]) if "Upstream Distance" in fields else 0.0
                    weir_w = _f(rec["Weir Width"]) if "Weir Width" in fields else 0.0
                    weir_min = _f(rec["Weir Min Elevation"]) if "Weir Min Elevation" in fields else None
                    polypts = self._slice_polyline(s_cl_info, s_cl_parts, s_cl_pts, i) if s_cl_info is not None else []
                    pdata = self._slice_table_profile(
                        s_table_info, s_profile_data, i, "Centerline Profile"
                    )
                    low_chord, high_chord = self._structure_chords(
                        s_table_info, s_profile_data, i, stype, weir_min
                    )
                    # Channel invert near structure: nearest XS minchel later in API; store weir
                    # min as a base when available for laterals / inline weirs
                    minchel = None
                    st = Structure(
                        river=river, reach=reach_name, rs=rs, type=stype, name=name,
                        upstream_distance=up_dist or 0.0, weir_width=weir_w or 0.0,
                        low_chord=low_chord, high_chord=high_chord, minchel=minchel,
                        poly_points=polypts, profile_data=pdata, index=i,
                    )
                    st.source_id = f"Structures/Attributes[{i}]"
                    st.source_path = str(geom_path)
                    st.source_attributes = source_record(rec)
                    if s_table_info is not None and i < len(s_table_info):
                        st.source_profiles["table_info"] = source_record(s_table_info[i])
                        for field_name in s_table_info.dtype.names or ():
                            if field_name.endswith(" (Index)"):
                                prefix = field_name.removesuffix(" (Index)")
                                st.source_profiles[prefix] = self._slice_table_profile(s_table_info, s_profile_data, i, prefix)
                    st.source_profiles["centerline"] = list(pdata)
                    st.us_rs = _s(rec["US RS"]).strip() if "US RS" in fields else ""
                    st.ds_rs = _s(rec["DS RS"]).strip() if "DS RS" in fields else ""
                    st.lateral_hw_distance = _f(rec["LW HW Distance"]) \
                        if "LW HW Distance" in fields else None
                    st.lateral_tw_distance = _f(rec["LW TW Distance"]) \
                        if "LW TW Distance" in fields else None
                    if connectivity is not None:
                        connection_fields = connectivity.dtype.names or ()
                        for connection in connectivity:
                            if "SID" not in connection_fields or int(connection["SID"]) != i:
                                continue
                            st.source_connections.append(source_record(connection))
                            side = _s(connection["HW/TW"]) if "HW/TW" in connection_fields else ""
                            connected_rs = _s(connection["RS/FP"]) if "RS/FP" in connection_fields else ""
                            station = _f(connection["Station"]) if "Station" in connection_fields else None
                            if not connected_rs or station is None:
                                continue
                            if side == "HW":
                                st.hw_connectivity.append((connected_rs, station))
                            elif side == "TW":
                                st.tw_connectivity.append((connected_rs, station))
                    if culvert_group_attrs is not None:
                        group_fields = culvert_group_attrs.dtype.names or ()
                        for group_index, grp in enumerate(culvert_group_attrs):
                            association = ""
                            if "Structure ID" in group_fields:
                                if int(grp["Structure ID"]) != i:
                                    continue
                                association = "explicit Structure ID -> Structures/Attributes row"
                            elif all(k in group_fields for k in ("River", "Reach", "RS")):
                                if (_s(grp["River"]), _s(grp["Reach"]), _s(grp["RS"])) != (river, reach_name, rs):
                                    continue
                                association = "explicit river/reach/RS"
                            else:
                                st.source_issues.append("Culvert groups lack an explicit Structure ID or river/reach/RS association.")
                                continue
                            opening = source_record(grp)
                            opening["source_id"] = f"Structures/Culvert Groups/Attributes[{group_index}]"
                            opening["association"] = association
                            # HEC-RAS schemas use both US/DS Invert and Invert US/DS.
                            # Retain the source fields and publish stable aliases downstream.
                            if "US Invert" in opening and "Invert US" not in opening:
                                opening["Invert US"] = opening["US Invert"]
                            if "DS Invert" in opening and "Invert DS" not in opening:
                                opening["Invert DS"] = opening["DS Invert"]
                            if culvert_barrel_attrs is not None:
                                barrel_fields = culvert_barrel_attrs.dtype.names or ()
                                opening["barrel_records"] = [source_record(barrel) for barrel in culvert_barrel_attrs
                                    if "Structure ID" in barrel_fields and int(barrel["Structure ID"]) == i
                                    and ("Culvert Group ID" not in barrel_fields
                                         or int(barrel["Culvert Group ID"]) == group_index)]
                            st.openings.append(opening)
                        # Never drop later groups or publish the first as the whole structure.
                        if len(st.openings) == 1:
                            group = st.openings[0]
                            st.culvert_length = _f(group.get("Length"))
                            st.culvert_us_distance = _f(group.get("US Distance"))
                            st.invert_us = _f(group.get("Invert US", group.get("US Invert")))
                            st.invert_ds = _f(group.get("Invert DS", group.get("DS Invert")))
                    key = (river, reach_name)
                    if key in reaches_map:
                        reaches_map[key].structures.append(st)
                    else:
                        self.unassigned_structures.append(st)

            # ----- Centerlines (one per reach; index matches attribute rows) ---- #
            if center_attrs is not None:
                for i, rec in enumerate(center_attrs):
                    river = _s(rec["River Name"])
                    reach = _s(rec["Reach Name"])
                    polypts = self._slice_polyline(cl_info, cl_parts, cl_pts, i) \
                        if cl_info is not None and cl_pts is not None else []
                    key = (river, reach)
                    if key in reaches_map:
                        reaches_map[key].centerline = polypts
                        reaches_map[key].source_id = f"River Centerlines/Attributes[{i}]"
                        reaches_map[key].source_path = str(geom_path)
                        reaches_map[key].source_connectivity = source_record(rec)

        # Establish the file's source-row orientation from reaches whose numeric
        # stations prove it.  This permits unique nonnumeric labels only when
        # the same HDF supplies a consistent ordering convention; labels are
        # never coerced into fabricated station numbers.
        analyses: dict[tuple[str, str], tuple[list[float] | None, str]] = {}
        proven_directions: set[str] = set()
        for key, reach in reaches_map.items():
            identities = [xs.identity for xs in reach.cross_sections]
            if len(set(identities)) != len(identities):
                raise AlignmentError(
                    f"Reach {(reach.river, reach.reach)!r}: duplicate river-station labels require "
                    "a result-side authoritative discriminator; none is available in this schema.")
            try:
                numbers = [station_number(xs.rs) for xs in reach.cross_sections]
            except AlignmentError:
                analyses[key] = (None, "NONNUMERIC")
                continue
            if len(set(numbers)) != len(numbers):
                raise AlignmentError(
                    f"Reach {(reach.river, reach.reach)!r}: numerically coincident river-station "
                    "identifiers cannot establish XS ordering.")
            descending = all(a > b for a, b in zip(numbers, numbers[1:]))
            ascending = all(a < b for a, b in zip(numbers, numbers[1:]))
            direction = "UPSTREAM_FIRST" if descending else "DOWNSTREAM_FIRST" if ascending else "NONMONOTONIC"
            analyses[key] = (numbers, direction)
            if direction != "NONMONOTONIC":
                proven_directions.add(direction)

        for key, reach in reaches_map.items():
            source_indices = [xs.index for xs in reach.cross_sections]
            source_stations = [xs.rs for xs in reach.cross_sections]
            numbers, direction = analyses[key]
            warnings: list[str] = []
            if numbers is not None:
                reach.source_order_status = (
                    "SOURCE_UPSTREAM_FIRST" if direction == "UPSTREAM_FIRST"
                    else "SOURCE_DOWNSTREAM_FIRST_NORMALIZED" if direction == "DOWNSTREAM_FIRST"
                    else "SOURCE_NONMONOTONIC_NUMERIC_NORMALIZED")
                if direction == "NONMONOTONIC":
                    warnings.append("Source rows were nonmonotonic; numeric RS ordering established the normalized order.")
                reach.cross_sections.sort(key=lambda xs: -station_number(xs.rs))
                normalization = "numeric river station descending (upstream first)"
            else:
                if len(proven_directions) != 1:
                    raise AlignmentError(
                        f"Reach {(reach.river, reach.reach)!r}: nonnumeric river-station labels require "
                        "one model-wide source-row orientation proven by numeric reaches; "
                        f"observed evidence={sorted(proven_directions)!r}.")
                proven = next(iter(proven_directions))
                if proven == "DOWNSTREAM_FIRST":
                    reach.cross_sections.reverse()
                    reach.source_order_status = "SOURCE_NONNUMERIC_DOWNSTREAM_FIRST_NORMALIZED"
                else:
                    reach.source_order_status = "SOURCE_NONNUMERIC_UPSTREAM_FIRST_VERIFIED"
                normalization = (
                    "source record order normalized using model-wide numeric-reach evidence; "
                    "river-station labels retained verbatim")
                warnings.append(
                    "Nonnumeric RS labels use source row order verified by consistent numeric reaches in this HDF.")
            reach.source_order_provenance = {
                "source_path": str(geom_path),
                "dataset": "Geometry/Cross Sections/Attributes",
                "source_indices": source_indices,
                "source_stations": source_stations,
                "normalized_indices": [xs.index for xs in reach.cross_sections],
                "normalized_stations": [xs.rs for xs in reach.cross_sections],
                "ordering_rule": reach.source_order_status,
                "normalization": normalization,
                "warnings": warnings,
            }
            for index, xs in enumerate(reach.cross_sections):
                if math.isfinite(xs.length_channel) and xs.length_channel >= 0:
                    continue
                if index == len(reach.cross_sections) - 1:
                    # The downstream-most record owns no adjacent downstream
                    # interval, so its HEC-RAS NaN is semantically unused.
                    xs.length_channel = 0.0
                    xs.length_provenance["status"] = "TERMINAL_NON_INTERVAL"
                    xs.length_provenance["normalized_value"] = 0.0
                else:
                    raise AlignmentError(
                        f"XS {xs.identity!r}: nonterminal Len Channel is missing or invalid."
                    )
            reach.rs_to_index = {xs.rs: i for i, xs in enumerate(reach.cross_sections)}
        return [reaches_map[key] for key in sorted(reaches_map)]

    # ----- polyline / station-elevation slicing ----------------------------- #

    @staticmethod
    def _slice_polyline(info: np.ndarray, parts: np.ndarray, pts: np.ndarray, idx: int) -> list[tuple[float, float]]:
        try:
            p_start, p_count, p_dstart, p_dcount = info[idx]
            out = []
            for p in range(p_start, p_start + p_count):
                pp_start, pp_count = parts[p]
                for q in range(pp_start, pp_start + pp_count):
                    x, y = pts[q]
                    out.append((float(x), float(y)))
            return out
        except Exception:
            return []

    @staticmethod
    def _slice_table_profile(
        table_info: np.ndarray | None,
        profile_data: np.ndarray | None,
        idx: int,
        prefix: str,
    ) -> list[tuple[float, float]]:
        """Slice station-elevation pairs for one structure profile from Table Info."""
        if table_info is None or profile_data is None or idx >= len(table_info):
            return []
        row = table_info[idx]
        names = table_info.dtype.names or ()
        ikey, ckey = f"{prefix} (Index)", f"{prefix} (Count)"
        if ikey not in names or ckey not in names:
            return []
        start = int(row[ikey])
        count = int(row[ckey])
        if count <= 0:
            return []
        chunk = profile_data[start: start + count]
        out: list[tuple[float, float]] = []
        for row2 in chunk:
            try:
                out.append((float(row2[0]), float(row2[1])))
            except Exception:
                continue
        return out

    @classmethod
    def _structure_chords(
        cls,
        table_info: np.ndarray | None,
        profile_data: np.ndarray | None,
        idx: int,
        stype: str,
        weir_min: float | None,
    ) -> tuple[Optional[float], Optional[float]]:
        """Derive low/high chord elevations for FIS structure glyphs.

        Bridge / culvert: high chord = max lid elev (road), low chord = min opening
        elev under deck (US/DS BR Profile). Weir/lateral: crest = Weir Min Elevation
        or centerline profile elev used as high chord.
        """
        t = (stype or "").lower()
        low: Optional[float] = None
        high: Optional[float] = None

        lid_elevs: list[float] = []
        open_elevs: list[float] = []
        for prefix in ("US BR Lid Profile", "DS BR Lid Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:  # not NaN
                    lid_elevs.append(e)
        for prefix in ("US BR Profile", "DS BR Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:
                    open_elevs.append(e)
        weir_elevs: list[float] = []
        for prefix in ("US BR Weir Profile", "DS BR Weir Profile", "Centerline Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:
                    weir_elevs.append(e)

        if lid_elevs:
            high = float(max(lid_elevs))
        if open_elevs:
            # Opening soffit ≈ max of internal BR profile points near deck;
            # low chord for FIS is typically min deck underside ≈ max open elev
            # when open profile is the flow-opening outline under the roadway.
            low = float(max(open_elevs)) if "bridge" in t or "culvert" in t else float(min(open_elevs))
            if high is None:
                high = float(max(open_elevs))

        if high is None and weir_min is not None and weir_min == weir_min:
            high = float(weir_min)
        if high is None and weir_elevs:
            high = float(min(weir_elevs)) if ("lateral" in t or "weir" in t or "inline" in t) else float(max(weir_elevs))
        if low is None and high is not None and ("lateral" in t or "weir" in t or "inline" in t):
            # Discrete weir crest: same elev (horizontal mark)
            low = high
        return low, high

    @staticmethod
    def _slice_station_elevation(se_info: np.ndarray, se_values: np.ndarray, idx: int) -> list[tuple[float, float]]:
        try:
            start, count = se_info[idx]
            if se_values is None or start < 0 or count <= 0 or start + count > len(se_values):
                raise ValueError("Invalid station/elevation slice")
            chunk = se_values[start:start + count]
            return [(float(s), float(e)) for s, e in chunk]
        except Exception:
            return []

    @staticmethod
    def _compute_minchel(se_pairs: list[tuple[float, float]], lb: float, rb: float) -> Optional[float]:
        return ground_value(se_pairs,lb,rb)[0]

    # -- results ------------------------------------------------------------ #

    def _read_profiles(self, reaches: list[ReachGeometry]) -> list[ProfileResult]:
        if not reaches:
            return []
        with h5py.File(self.plan_hdf_path, "r") as f:
            adapter = self.schema_adapter
            if adapter is None:
                raise AlignmentError(
                    "Unsupported or missing hydraulic result layout: no verified schema adapter was "
                    "resolved from combined geometry/result evidence.")
            if adapter.result_type == "UNSTEADY_SAVED_TIMESTEPS":
                return self._read_unsteady_profiles(f, reaches, adapter.result_root)
            if adapter.result_type == "POST_PROCESS_SAVED_TIMESTEPS":
                return self._read_post_process_profiles(f, reaches, adapter)
            if adapter.result_type in {"STEADY_PROFILES", "POST_PROCESS_STEADY_PROFILES"}:
                return self._read_steady_profiles(f, reaches, adapter.cross_section_path)
        raise AlignmentError(f"Adapter {adapter.name} declares unsupported result type {adapter.result_type!r}.")

    def _read_unsteady_profiles(self, f: h5py.File, reaches: list[ReachGeometry], ut_path: str) -> list[ProfileResult]:
        cs = f[ut_path + "/Cross Sections"]
        if "Water Surface" not in cs:
            raise AlignmentError(f"{cs.name}: Water Surface dataset is absent.")
        dataset=cs["Water Surface"]
        wsel_all=dataset[()]
        identities,order=self._result_order(f,cs,reaches,wsel_all)
        T=wsel_all.shape[0]
        parent=f[ut_path]
        stamps=parent.get("Time Date Stamp")
        if stamps is None: stamps=cs.get("Time Date Stamp")
        labels=[];time_info={"kind":"STEP_INDEX","reason":"Source timestamps absent or unverified","timezone":"UNKNOWN"}
        if stamps is not None:
            raw=self._string_rows(stamps)
            try:
                dates=[datetime.strptime(t,"%d%b%Y %H:%M:%S") for t in raw]
                if len(dates)!=T or any(a>=b for a,b in zip(dates,dates[1:])):
                    raise ValueError("Timestamp count/order mismatch")
                labels=raw
                time_info={"kind":"SOURCE","dataset":stamps.name,"timezone":"UNKNOWN","representation":"HEC-RAS calendar string; no timezone inferred"}
            except (ValueError,TypeError):
                time_info["reason"]="Source timestamp count, format or ordering could not be verified"
        dry=[float(v) for k,v in dataset.attrs.items() if k.lower() in ("dry value","dry_value")]
        missing=[float(v) for k,v in dataset.attrs.items() if k.lower() in ("missing_value","_fillvalue","missing value")]
        values=[[normalize_value(wsel_all[t,j],dry,missing) for j in order] for t in range(T)]
        idxs=list(range(0,T,max(1,T//30)))
        if T-1 not in idxs: idxs.append(T-1)
        profiles=[]
        for t in idxs:
            profiles.append(ProfileResult(name=labels[t] if labels else f"Saved step {t} (source time unavailable)",
                wsel=[v["value"] for v in values[t]],xs_ids=identities,values=values[t],
                profile_id=f"unsteady-step-{t}",source_path=str(self.plan_hdf_path),source_dataset=dataset.name,
                result_type="SAVED_TIMESTEP_WSEL",time_provenance={**time_info,"row":t}))
        maximum=[]
        for j in range(len(order)):
            column=[row[j] for row in values];valid=[v["value"] for v in column if v["state"]=="VALID"]
            state="VALID" if len(valid)==T else "PARTIAL_ENVELOPE" if valid else "DRY" if all(v["state"]=="DRY" for v in column) else "MISSING"
            if any(v["state"]=="UNKNOWN_SENTINEL" for v in column): state="UNKNOWN_SENTINEL"
            maximum.append({"value":max(valid) if valid else None,"state":state,"raw":None,"valid_steps":len(valid),"total_steps":T,"provenance":"DERIVED"})
        profiles.insert(0,ProfileResult(name="Maximum WSEL",wsel=[v["value"] for v in maximum],xs_ids=identities,
            values=maximum,profile_id="unsteady-saved-maximum",source_path=str(self.plan_hdf_path),source_dataset=dataset.name,
            result_type="DERIVED_MAXIMUM_ENVELOPE",time_provenance={**time_info,"rows":[0,T-1],"description":"Per-XS maximum over all saved base-output timesteps; not a simultaneous profile or source-authoritative maximum"}))
        prepare_profiles(profiles)
        return profiles

    def _read_post_process_profiles(self, f: h5py.File, reaches: list[ReachGeometry], adapter) -> list[ProfileResult]:
        """Read the authenticated post-process saved-profile family.

        Row zero in the observed Muncie schema is explicitly named ``Max WS``
        by HEC-RAS and is retained as a source-authoritative maximum. It is not
        recomputed from, or conflated with, the following saved time rows.
        """
        cs = f[adapter.cross_section_path]
        dataset = cs.get("Water Surface")
        if dataset is None:
            raise AlignmentError(f"{cs.name}: Water Surface dataset is absent.")
        values = dataset[()]
        identities, order = self._result_order(f, cs, reaches, values)
        labels_dataset = f.get(adapter.profile_labels_path)
        if labels_dataset is None:
            raise AlignmentError(f"{adapter.profile_labels_path}: required post-process profile identities are absent.")
        labels = self._string_rows(labels_dataset)
        if len(labels) != values.shape[0] or not labels or labels[0].strip().upper() != "MAX WS":
            raise AlignmentError(
                f"{labels_dataset.name}: expected one label per result row with source 'Max WS' first; "
                "post-process result meaning is ambiguous.")
        variable_units = dataset.attrs.get("Variable_Unit")
        unit_tokens = {_s(value).strip().lower() for value in np.asarray(variable_units).reshape(-1)} \
            if variable_units is not None else set()
        expected_unit = "m" if _s(f.attrs.get("Units System", "")).strip().upper() == "SI" else "ft"
        if expected_unit not in unit_tokens:
            raise AlignmentError(
                f"{dataset.name}: WSEL unit metadata must explicitly contain {expected_unit!r}; "
                f"observed {sorted(token for token in unit_tokens if token)!r}.")
        dry = [float(value) for key, value in dataset.attrs.items()
               if key.lower() in ("dry value", "dry_value")]
        missing = [float(value) for key, value in dataset.attrs.items()
                   if key.lower() in ("missing_value", "_fillvalue", "missing value")]
        normalized = [[normalize_value(values[row, column], dry, missing) for column in order]
                      for row in range(values.shape[0])]
        sample = list(range(1, values.shape[0], max(1, (values.shape[0] - 1) // 30)))
        if values.shape[0] > 1 and values.shape[0] - 1 not in sample:
            sample.append(values.shape[0] - 1)
        selected_rows = [0, *sample]
        profiles: list[ProfileResult] = []
        for row in selected_rows:
            result_type = "SOURCE_AUTHORITATIVE_MAXIMUM" if row == 0 else "SAVED_TIMESTEP_WSEL"
            profiles.append(ProfileResult(
                name="Maximum WSEL" if row == 0 else labels[row],
                wsel=[item["value"] for item in normalized[row]], xs_ids=identities,
                values=normalized[row], profile_id=f"post-process-row-{row}",
                source_path=str(self.plan_hdf_path), source_dataset=dataset.name,
                result_type=result_type,
                time_provenance={
                    "kind": "SOURCE_POST_PROCESS_PROFILE_DATE",
                    "dataset": labels_dataset.name, "row": row,
                    "source_label": labels[row], "timezone": "UNKNOWN",
                    "description": ("HEC-RAS source-authoritative post-process maximum"
                                    if row == 0 else
                                    "HEC-RAS post-process saved profile; source date text retained without timezone inference"),
                }))
        prepare_profiles(profiles)
        return profiles

    def _read_steady_profiles(self, f: h5py.File, reaches: list[ReachGeometry], cs_path: str, wsel: str = "Water Surface") -> list[ProfileResult]:
        base = f[cs_path]
        cs = base
        if wsel not in base and "Water Surface" not in base and "Cross Sections" in base:
            cs = base["Cross Sections"]

        wsel_ds = cs[wsel] if wsel in cs else None
        if wsel_ds is None:
            wsel_ds = cs.get("Water Surface", None)
        if wsel_ds is None:
            raise AlignmentError(f"{cs.name}: Water Surface dataset is absent.")

        arr = wsel_ds[()]
        identities, order = self._result_order(f, cs, reaches, arr)
        names: list[str] = []
        parent = base
        if cs_path.endswith("/Cross Sections"):
            parent = f[cs_path.rsplit("/Cross Sections", 1)[0]]
        if "Profile Names" in parent:
            names = self._string_rows(parent["Profile Names"])
        elif "Profile Names" in cs:
            names = self._string_rows(cs["Profile Names"])
        if not names:
            names = [f"Profile {i+1}" for i in range(arr.shape[0])]

        if len(names) != arr.shape[0]:
            raise AlignmentError(f"{cs.name}: profile names/count do not uniquely match result rows.")
        profiles = []
        for i, name in enumerate(names):
            wsel = [float(arr[i, order_pos]) for order_pos in order]
            profiles.append(ProfileResult(name=name, wsel=wsel, xs_ids=identities, profile_id=f"steady-row-{i}", source_path=str(self.plan_hdf_path), source_dataset=wsel_ds.name, values=[normalize_value(v, [float(x) for k,x in wsel_ds.attrs.items() if k.lower() in ("dry_value","dry value")], [float(x) for k,x in wsel_ds.attrs.items() if k.lower() in ("missing_value","missing value","_fillvalue")]) for v in wsel]))
        prepare_profiles(profiles)
        return profiles

    @staticmethod
    def _string_rows(dataset):
        values = dataset[()]
        if isinstance(values, (bytes, str, np.bytes_, np.str_)) or (
                isinstance(values, np.generic) and values.ndim == 0):
            raise AlignmentError(
                f'{dataset.name}: expected a string row array, got scalar {type(values).__name__}. '
                'Unsupported source schema.')
        if not isinstance(values, np.ndarray):
            raise AlignmentError(
                f'{dataset.name}: expected a string row array, got {type(values).__name__}. '
                'Unsupported source schema.')
        if values.ndim not in (1, 2) or (values.ndim == 2 and values.shape[1] != 1):
            raise AlignmentError(f'{dataset.name}: expected a string row array, got {values.shape}. Unsupported source schema.')
        return [_s(value) for value in values]

    def _result_order(self, f, cs, reaches, values):
        """Prove a bijection before moving any result column into canonical order."""
        expected = [xs.identity for reach in reaches for xs in reach.cross_sections]
        if values.ndim != 2 or values.shape[0] == 0:
            raise AlignmentError(f"{cs.name}: expected nonempty profile/time by XS results, got {values.shape}.")
        attrs = cs.get("Cross Section Attributes")
        strategy = self.schema_adapter.identity_strategy if self.schema_adapter is not None else None
        if strategy == "EXPLICIT_RESULT_IDENTITY_DATASET":
            identity_path = self.schema_adapter.identity_dataset_path if self.schema_adapter is not None else ""
            attrs = f.get(identity_path)
            if attrs is None:
                raise AlignmentError(f"{identity_path}: adapter requires an explicit result identity dataset.")
        if strategy in {"EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES", "EXPLICIT_RESULT_IDENTITY_DATASET"} or (strategy is None and attrs is not None):
            if attrs is None:
                raise AlignmentError(f"{cs.name}: adapter requires explicit Cross Section Attributes.")
            records = attrs[()]
            fields = records.dtype.names or ()
            rs_field = "Station" if "Station" in fields else "RS"
            if not {"River", "Reach", rs_field}.issubset(fields):
                raise AlignmentError(f"{attrs.name}: River, Reach and RS/Station are required to associate results.")
            actual = [xs_identity(r["River"], r["Reach"], r[rs_field]) for r in records]
            self.result_identity_adapter = "RESULT_CROSS_SECTION_ATTRIBUTES"
        elif strategy == "EXACT_COMBINED_GEOMETRY_INFO_LABEL" or strategy is None:
            # Observed HEC-RAS 5.0.7 steady schema: complete result-column
            # identities, not a positional fallback to geometry attributes.
            path = "Results/Steady/Output/Geometry Info/Cross Section Only"
            if not cs.name.startswith("/Results/Steady/") or path not in f:
                raise AlignmentError(f"{cs.name}: result-side XS identity dataset is missing; cannot prove geometry/WSEL association.")
            labels = {}
            for key in expected:
                label = " ".join(" ".join(key).split())
                if label in labels:
                    raise AlignmentError(f"{path}: ambiguous combined river/reach/station label {label!r}.")
                labels[label] = key
            actual = []
            for value in f[path][()]:
                label = " ".join(_s(value).split())
                if label not in labels:
                    raise AlignmentError(f"{path}: result XS {label!r} has no exact geometry identity.")
                actual.append(labels[label])
            self.result_identity_adapter = "STEADY_GEOMETRY_INFO_COMBINED_LABELS"
        else:
            raise AlignmentError(f"Unsupported result identity strategy {strategy!r}.")
        if len(actual) != values.shape[1] or len(set(actual)) != len(actual):
            raise AlignmentError(f"{cs.name}: result identity count/duplicates do not match {values.shape[1]} WSEL columns.")
        missing, extra = set(expected) - set(actual), set(actual) - set(expected)
        if missing or extra or len(set(expected)) != len(expected):
            raise AlignmentError(f"{cs.name}: geometry/result identity mismatch; missing={sorted(missing)!r}, extra={sorted(extra)!r}.")
        indices = {key: i for i, key in enumerate(actual)}
        return expected, [indices[key] for key in expected]

    @staticmethod
    def _rs_to_float(rs: str) -> float:
        try:
            return float(rs)
        except Exception:
            return 0.0


# --------------------------------------------------------------------------- #
# Convenience JSON serialisers                                                  #
# --------------------------------------------------------------------------- #


def reach_to_dict(r: ReachGeometry) -> dict[str, Any]:
    return {
        "river": r.river,
        "reach": r.reach,
        "centerline": r.centerline,
        "cross_sections": [source_value(asdict(c)) for c in r.cross_sections],
        "structures": [source_value(asdict(s)) for s in r.structures],
    }


def plan_to_dict(p: Plan) -> dict[str, Any]:
    return {
        "name": p.name,
        "plan_path": str(p.plan_path),
        "geometry_path": str(p.geometry_path) if p.geometry_path else None,
        "flow_type": p.flow_type,
        "reaches": [reach_to_dict(r) for r in p.reaches],
        "profiles": [{**asdict(pr),"display_name":pr.name + (" (derived saved-timestep envelope)" if pr.result_type=="DERIVED_MAXIMUM_ENVELOPE" else "")} for pr in p.profiles],
        "source_units": p.source_units, "working_units": p.working_units,
        "unit_contract": dict(p.unit_contract),
        "unit_conversion_status": p.unit_conversion_status,
        "schema_adapter": dict(p.schema_adapter),
        "source_datum":p.source_datum,
        "model_id": getattr(p, "model_id", ""), "ras_version": getattr(p, "ras_version", "UNKNOWN"),
        "capabilities": p.capabilities.to_dict() if getattr(p, "capabilities", None) else None,
        "topology": p.topology.to_dict() if getattr(p, "topology", None) else None,
    }
