"""Generic normalized HEC-RAS topology and flooding-source stationing.

This module contains no fixture names or schema-path assumptions.  Adapters
provide source records plus provenance; the domain validates their meaning.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any


READ_STATES = {"SUPPORTED", "UNVALIDATED", "UNKNOWN", "MISSING"}


def stable_id(kind: str, *parts: object) -> str:
    semantic = json.dumps([kind, *[str(p) for p in parts]], ensure_ascii=False,
                          separators=(",", ":"))
    return f"{kind.lower()}-{hashlib.sha256(semantic.encode()).hexdigest()[:20]}"


@dataclass
class HdfCapabilities:
    ras_version: str = "UNKNOWN"
    plan_identity: dict[str, Any] = field(default_factory=dict)
    geometry_identity: dict[str, Any] = field(default_factory=dict)
    unit_system: str = "UNKNOWN"
    geometry_available: str = "MISSING"
    results_available: str = "MISSING"
    river_centerline_connectivity_available: str = "MISSING"
    junctions_available: str = "MISSING"
    junction_distances_available: str = "MISSING"
    cross_sections_available: str = "MISSING"
    structures_detected: list[dict[str, Any]] = field(default_factory=list)
    result_profiles_available: str = "MISSING"
    datum_status: str = "UNKNOWN"
    datum_provenance: dict[str, Any] = field(default_factory=dict)
    available_geometry_datasets: list[str] = field(default_factory=list)
    available_result_datasets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported_or_unvalidated_features: list[str] = field(default_factory=list)
    schema_adapter: str = "UNRESOLVED_INSPECTION"
    workflow_capabilities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelContext:
    model_id: str
    generation: int
    session_id: str
    source_path: str
    status: str = "CANDIDATE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReachEndpoint:
    reach_id: str
    side: str
    connection_type: str
    connection_name: str
    endpoint_distance: float | None
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ReachConnection:
    id: str
    junction_id: str
    upstream_reach_id: str
    downstream_reach_id: str
    upstream_component: float | None
    downstream_component: float | None
    transition_distance: float | None
    status: str
    provenance: dict[str, Any]


@dataclass
class NormalizedReach:
    id: str
    river_id: str
    river_name: str
    reach_name: str
    cross_section_ids: list[str]
    structure_ids: list[str]
    upstream: ReachEndpoint
    downstream: ReachEndpoint
    topology_status: str
    provenance: dict[str, Any]
    component_id: str = ""


@dataclass
class NormalizedJunction:
    id: str
    name: str
    upstream_reach_ids: list[str] = field(default_factory=list)
    downstream_reach_ids: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopologyGraph:
    reaches: dict[str, NormalizedReach]
    junctions: dict[str, NormalizedJunction]
    connections: dict[str, ReachConnection]
    warnings: list[str] = field(default_factory=list)
    components: list[list[str]] = field(default_factory=list)
    cycle_reach_ids: list[str] = field(default_factory=list)
    branch_junction_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaches": [asdict(v) for v in self.reaches.values()],
            "junctions": [asdict(v) for v in self.junctions.values()],
            "connections": [asdict(v) for v in self.connections.values()],
            "warnings": list(self.warnings),
            "components": [list(component) for component in self.components],
            "cycle_reach_ids": list(self.cycle_reach_ids),
            "branch_junction_ids": list(self.branch_junction_ids),
        }


@dataclass(frozen=True)
class FloodingSourceSegment:
    reach_id: str
    order: int
    orientation: str = "DOWNSTREAM_TO_UPSTREAM"
    connection_to_next: str | None = None
    transition_distance: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class FloodingSource:
    id: str
    display_name: str
    model_id: str
    ordered_segments: list[FloodingSourceSegment]
    direction: str = "DOWNSTREAM_TO_UPSTREAM"
    topology_validation_status: str = "UNKNOWN"
    provenance: dict[str, Any] = field(default_factory=dict)
    validation_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DistanceLedgerEntry:
    kind: str
    from_id: str
    to_id: str
    distance: float
    cumulative_distance: float
    provenance: dict[str, Any]


@dataclass
class ContinuousDistanceResult:
    status: str
    distances: dict[str, float]
    ledger: list[DistanceLedgerEntry]
    messages: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "distances": self.distances,
                "ledger": [asdict(v) for v in self.ledger],
                "messages": self.messages}


def build_topology(model_id: str, reaches: list[Any], junction_records: list[dict[str, Any]],
                   source_path: str) -> TopologyGraph:
    warnings: list[str] = []
    named_records: dict[str, list[dict[str, Any]]] = {}
    for record in junction_records:
        name = str(record.get("Name", ""))
        if name:
            named_records.setdefault(name, []).append(record)
    ambiguous_names = {name for name, records in named_records.items() if len(records) != 1}
    for name in sorted(ambiguous_names):
        warnings.append(f"Junction name {name!r} is not unique; connections through it are ambiguous.")
    junctions = {
        stable_id("junction", model_id, name): NormalizedJunction(
            id=stable_id("junction", model_id, name), name=name,
            attributes=dict(records[0]),
            provenance={"source_path": source_path,
                        "dataset": "Geometry/Junctions/Attributes",
                        "source_record_count": len(records)})
        for name, records in named_records.items()
    }
    junction_by_name = {v.name: v for v in junctions.values()}
    normalized: dict[str, NormalizedReach] = {}
    for reach in reaches:
        rid = stable_id("reach", model_id, reach.river, reach.reach)
        river_id = stable_id("river", model_id, reach.river)
        raw = getattr(reach, "source_connectivity", {}) or {}
        def endpoint(side: str) -> ReachEndpoint:
            prefix = "US" if side == "UPSTREAM" else "DS"
            distance_field = "Junction to US XS" if side == "UPSTREAM" else "DS XS to Junction"
            raw_distance = raw.get(distance_field)
            distance = float(raw_distance) if raw_distance is not None and math.isfinite(float(raw_distance)) else None
            return ReachEndpoint(
                reach_id=rid, side=side,
                connection_type=str(raw.get(f"{prefix} Type", "UNKNOWN") or "UNKNOWN").upper(),
                connection_name=str(raw.get(f"{prefix} Name", "") or ""),
                endpoint_distance=distance,
                provenance={"source_path": source_path,
                            "dataset": "Geometry/River Centerlines/Attributes",
                            "field": distance_field, "raw_value": raw_distance},
            )
        us, ds = endpoint("UPSTREAM"), endpoint("DOWNSTREAM")
        for ep in (us, ds):
            if ep.connection_type == "JUNCTION" and ep.connection_name not in junction_by_name:
                warnings.append(f"Reach {reach.river}/{reach.reach} references unknown junction {ep.connection_name!r}.")
        normalized[rid] = NormalizedReach(
            id=rid, river_id=river_id, river_name=reach.river, reach_name=reach.reach,
            cross_section_ids=[stable_id("xs", model_id, x.river, x.reach, x.rs) for x in reach.cross_sections],
            structure_ids=[stable_id("structure", model_id, s.river, s.reach, s.rs, s.type, s.index) for s in reach.structures],
            upstream=us, downstream=ds,
            topology_status="SUPPORTED" if raw else "UNKNOWN",
            provenance={"source_path": source_path,
                        "dataset": "Geometry/River Centerlines/Attributes",
                        "source_record": raw},
        )
    connections: dict[str, ReachConnection] = {}
    for junction in junctions.values():
        # A reach's downstream endpoint enters a junction; a reach's upstream
        # endpoint leaves it in the downstream direction.
        upstream = [r for r in normalized.values() if r.downstream.connection_type == "JUNCTION" and r.downstream.connection_name == junction.name]
        downstream = [r for r in normalized.values() if r.upstream.connection_type == "JUNCTION" and r.upstream.connection_name == junction.name]
        junction.upstream_reach_ids = [r.id for r in upstream]
        junction.downstream_reach_ids = [r.id for r in downstream]
        for up in upstream:
            for down in downstream:
                a, b = up.downstream.endpoint_distance, down.upstream.endpoint_distance
                total = a + b if a is not None and b is not None and junction.name not in ambiguous_names else None
                cid = stable_id("connection", model_id, junction.name, up.id, down.id)
                connections[cid] = ReachConnection(
                    id=cid, junction_id=junction.id,
                    upstream_reach_id=up.id, downstream_reach_id=down.id,
                    upstream_component=a, downstream_component=b,
                    transition_distance=total,
                    status="SUPPORTED" if total is not None else ("UNVALIDATED" if junction.name in ambiguous_names else "MISSING"),
                    provenance={"rule": "DS XS to Junction + Junction to US XS",
                                "upstream_endpoint": up.downstream.provenance,
                                "downstream_endpoint": down.upstream.provenance},
                )
    # Deterministic undirected components distinguish selectable networks;
    # directed cycle detection prevents a route from silently looping.
    neighbors = {reach_id: set() for reach_id in normalized}
    directed = {reach_id: set() for reach_id in normalized}
    for connection in connections.values():
        neighbors[connection.upstream_reach_id].add(connection.downstream_reach_id)
        neighbors[connection.downstream_reach_id].add(connection.upstream_reach_id)
        directed[connection.upstream_reach_id].add(connection.downstream_reach_id)
    components: list[list[str]] = []
    unseen = set(normalized)
    while unseen:
        pending = [min(unseen)]
        component: list[str] = []
        while pending:
            current = pending.pop()
            if current not in unseen:
                continue
            unseen.remove(current)
            component.append(current)
            pending.extend(sorted(neighbors[current] & unseen, reverse=True))
        component.sort()
        components.append(component)
    components.sort(key=lambda item: item[0])
    for component in components:
        component_id = stable_id("component", model_id, *component)
        for reach_id in component:
            normalized[reach_id].component_id = component_id

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycle_ids: set[str] = set()
    def visit(reach_id: str) -> None:
        visiting.add(reach_id)
        stack.append(reach_id)
        for downstream_id in sorted(directed[reach_id]):
            if downstream_id in visiting:
                cycle_ids.update(stack[stack.index(downstream_id):])
            elif downstream_id not in visited:
                visit(downstream_id)
        stack.pop()
        visiting.remove(reach_id)
        visited.add(reach_id)
    for reach_id in sorted(normalized):
        if reach_id not in visited:
            visit(reach_id)
    if cycle_ids:
        warnings.append("Reach topology contains a directed cycle; routes through the cycle are blocked.")
        for reach_id in cycle_ids:
            normalized[reach_id].topology_status = "BLOCKED_CYCLE"
    branch_ids = sorted(
        junction.id for junction in junctions.values()
        if len(junction.upstream_reach_ids) > 1 or len(junction.downstream_reach_ids) > 1)
    return TopologyGraph(
        dict(sorted(normalized.items())), dict(sorted(junctions.items())),
        dict(sorted(connections.items())), warnings,
        components=components, cycle_reach_ids=sorted(cycle_ids),
        branch_junction_ids=branch_ids)


def create_flooding_source(model_id: str, display_name: str, reach_ids: list[str],
                           topology: TopologyGraph,
                           naming_provenance: str = "USER_DEFINED") -> FloodingSource:
    messages: list[str] = []
    segments: list[FloodingSourceSegment] = []
    if not display_name.strip():
        messages.append("Enter a flooding-source name.")
    if naming_provenance not in {"USER_DEFINED", "ENGINEERING_SUPPLIED"}:
        messages.append("Naming provenance must be USER_DEFINED or ENGINEERING_SUPPLIED.")
    if not reach_ids or len(set(reach_ids)) != len(reach_ids):
        messages.append("Select one or more unique reaches in downstream-to-upstream order.")
    known_reaches = [topology.reaches[item] for item in reach_ids if item in topology.reaches]
    components = {item.component_id for item in known_reaches}
    if len(components) > 1:
        labels = [f"{item.river_name}/{item.reach_name}" for item in known_reaches]
        messages.append(
            "Selected reaches belong to disconnected topology components: " + ", ".join(labels) + ".")
    selected_cycles = sorted(set(reach_ids) & set(topology.cycle_reach_ids))
    if selected_cycles:
        messages.append(
            "Selected route intersects a directed topology cycle; choose an acyclic authoritative path.")
    selected_branch_ids: set[str] = set()
    for index, reach_id in enumerate(reach_ids):
        if reach_id not in topology.reaches:
            messages.append(f"Unknown reach ID {reach_id}.")
        connection = None
        if index + 1 < len(reach_ids):
            upstream_id = reach_ids[index + 1]
            candidates = [c for c in topology.connections.values()
                          if c.upstream_reach_id == upstream_id and c.downstream_reach_id == reach_id]
            if len(candidates) != 1:
                messages.append(f"No unique authoritative connection from {upstream_id} to {reach_id}.")
            else:
                connection = candidates[0]
                if connection.junction_id in topology.branch_junction_ids:
                    selected_branch_ids.add(connection.junction_id)
                if connection.transition_distance is None:
                    messages.append(f"Connection {connection.id} has no authoritative transition distance.")
        segments.append(FloodingSourceSegment(
            reach_id=reach_id, order=index,
            connection_to_next=connection.id if connection else None,
            transition_distance=connection.transition_distance if connection else None,
            provenance=connection.provenance if connection else {},
        ))
    status = "SUPPORTED" if not messages else "BLOCKED"
    return FloodingSource(
        id=stable_id("flooding-source", model_id, display_name, *reach_ids),
        display_name=display_name, model_id=model_id, ordered_segments=segments,
        topology_validation_status=status,
        provenance={"naming": naming_provenance, "ordering": "USER_CONFIRMED",
                    "direction": "DOWNSTREAM_TO_UPSTREAM",
                    "component_ids": sorted(components),
                    "explicit_branch_junction_ids": sorted(selected_branch_ids)},
        validation_messages=messages,
    )


def continuous_distances(source: FloodingSource, topology: TopologyGraph,
                         reaches_by_id: dict[str, Any], start: float = 0.0,
                         working_lengths: dict[str, float] | None = None) -> ContinuousDistanceResult:
    if source.topology_validation_status != "SUPPORTED":
        return ContinuousDistanceResult("BLOCKED", {}, [], list(source.validation_messages))
    if not math.isfinite(start):
        return ContinuousDistanceResult("BLOCKED", {}, [], ["Starting CUMDIST must be finite."])
    edits = working_lengths or {}
    selected_ids = [segment.reach_id for segment in source.ordered_segments]
    if len(selected_ids) != len(set(selected_ids)):
        return ContinuousDistanceResult("BLOCKED", {}, [],
                                        ["Continuous route contains a duplicate reach or cycle."])
    running = float(start)
    distances: dict[str, float] = {}
    ledger: list[DistanceLedgerEntry] = []
    previous_xs_id = "ORIGIN"
    for segment_index, segment in enumerate(source.ordered_segments):
        reach = reaches_by_id.get(segment.reach_id)
        if reach is None or not reach.cross_sections:
            return ContinuousDistanceResult("BLOCKED", {}, ledger,
                                            [f"Reach {segment.reach_id} has no usable cross sections."])
        normalized = topology.reaches.get(segment.reach_id)
        actual_ids = [stable_id("xs", source.model_id, xs.river, xs.reach, xs.rs)
                      for xs in reach.cross_sections]
        if normalized is None or actual_ids != normalized.cross_section_ids:
            return ContinuousDistanceResult(
                "BLOCKED", {}, ledger,
                [f"Reach {segment.reach_id} cross-section identity/order no longer matches verified topology."])
        downstream_to_upstream = list(reversed(reach.cross_sections))
        first = downstream_to_upstream[0]
        first_id = stable_id("xs", source.model_id, first.river, first.reach, first.rs)
        if segment_index:
            prior = source.ordered_segments[segment_index - 1]
            connection = topology.connections.get(prior.connection_to_next or "")
            if connection is None or connection.transition_distance is None:
                return ContinuousDistanceResult("BLOCKED", {}, ledger,
                                                [f"Transition into reach {segment.reach_id} lacks authoritative distance."])
            running += connection.transition_distance
            ledger.append(DistanceLedgerEntry(
                "JUNCTION", previous_xs_id, first_id, connection.transition_distance,
                running, connection.provenance))
        distances[first_id] = running
        previous_xs_id = first_id
        for xs in downstream_to_upstream[1:]:
            xs_id = stable_id("xs", source.model_id, xs.river, xs.reach, xs.rs)
            raw = float(xs.length_channel)
            distance = float(edits.get(xs_id, raw))
            if not math.isfinite(distance) or distance < 0:
                return ContinuousDistanceResult("BLOCKED", {}, ledger,
                                                [f"Cross section {xs_id} has an invalid working channel length."])
            running += distance
            ledger.append(DistanceLedgerEntry(
                "CHANNEL_LENGTH", previous_xs_id, xs_id, distance, running,
                {"source_path": getattr(reach, "source_path", ""),
                 "dataset": "Geometry/Cross Sections/Attributes",
                 "field": "Len Channel", "raw_value": raw,
                 "working_value": distance, "edited": xs_id in edits}))
            distances[xs_id] = running
            previous_xs_id = xs_id
    return ContinuousDistanceResult("SUPPORTED", distances, ledger, [])
