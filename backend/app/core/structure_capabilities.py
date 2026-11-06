"""Declarative, source-evidence structure capability registry.

The registry classifies what the normalized structure model can represent.  It
does not manufacture geometry: type-specific engineering validation remains in
``structure_model.refresh`` and is always the final authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class StructureCapability:
    capability_id: str
    families: tuple[str, ...]
    source_signature: tuple[str, ...]
    required_geometry: tuple[str, ...]
    required_identity: tuple[str, ...]
    supported_openings: tuple[str, ...]
    normalization_rule: str
    drawing_representation: str
    export_eligible: bool
    limitations: tuple[str, ...]
    support_level: str = "INVENTORY_ONLY"


@dataclass(frozen=True)
class CapabilityDecision:
    capability_id: str
    recognized: bool
    export_eligible: bool
    missing_source_fields: tuple[str, ...]
    reason: str
    affected_workflows: tuple[str, ...]
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES = (
    StructureCapability(
        "BRIDGE_CHORD_ENVELOPE_V1", ("BRIDGE",),
        ("Geometry/Structures/Attributes", "Geometry/Structures/Table Info",
         "Geometry/Structures/Profile Data"),
        ("Upstream Distance", "Weir Width", "US/DS BR Weir Profile", "US/DS BR Lid Profile"),
        ("river", "reach", "RS", "US River/Reach/RS", "DS River/Reach/RS"),
        ("one source-backed chord envelope",),
        "Explicit adjacent XS plus source upstream distance/width; US/DS lid and weir profiles",
        "Two longitudinal UP/DN chord faces; no structure hydraulic WSEL",
        True,
        ("Multiple disjoint bridge openings are not yet represented separately",
         "Gate/culvert combinations on a bridge require a separate capability"),
        "AUTHORITATIVE_DRAWING",
    ),
    StructureCapability(
        "CULVERT_GROUP_ENVELOPES_V1", ("CULVERT", "MULTIPLE_OPENING"),
        ("Geometry/Structures/Attributes", "Geometry/Structures/Culvert Groups/Attributes",
         "plan-linked geometry text deck and culvert records"),
        ("Shape", "Barrels", "Rise", "Span", "Length", "US Distance",
         "US/DS Invert", "barrel centerline stations", "US/DS deck profiles"),
        ("river", "reach", "RS", "US River/Reach/RS", "DS River/Reach/RS",
         "Structure ID or explicit group river/reach/RS"),
        ("Box", "Circular pipe", "Ellipse", "multiple barrels", "multiple groups"),
        "Exact HDF/text group match; explicit XS interval; source gap, length, inverts, rise/span and barrels",
        "One longitudinal opening envelope per group with UP/DN faces",
        True,
        ("Transverse shapes remain metadata and are not redrawn as cross sections",
         "Groups with different longitudinal placement remain unsupported"),
        "AUTHORITATIVE_DRAWING",
    ),
    StructureCapability(
        "LATERAL_ANCHOR_ONLY_V1", ("LATERAL",),
        ("Geometry/Structures/Attributes", "User Defined Weir Connectivity"),
        ("crest/profile tables", "headwater/tailwater connectivity"),
        ("river", "reach", "RS", "receiving-side identity"),
        (), "Connected upstream XS anchor with zero added channel distance",
        "Preview inventory only", False,
        ("Authoritative main-channel extent and receiving-side semantics are not validated",),
        "SOURCE_GEOMETRY_PREVIEW",
    ),
    StructureCapability(
        "INLINE_WEIR_INVENTORY_ONLY_V1", ("INLINE", "WEIR"),
        ("Geometry/Structures/Attributes", "profile tables when present"),
        ("crest/profile tables", "longitudinal placement evidence"),
        ("river", "reach", "RS"), (), "Retain source inventory and provenance",
        "Preview inventory only", False,
        ("No source-validated longitudinal face-placement rule",),
        "SOURCE_GEOMETRY_PREVIEW",
    ),
    StructureCapability(
        "GATED_INVENTORY_ONLY_V1", ("GATED",),
        ("Geometry/Structures/Attributes", "gate group records and operating state"),
        ("gate opening geometry", "invert/soffit", "operating state", "longitudinal placement"),
        ("river", "reach", "RS"), (), "Retain source inventory and provenance",
        "Preview inventory only", False,
        ("Gate geometry and operating states lack an authorized validated fixture",),
        "INVENTORY_ONLY",
    ),
)


def _finite_positive(value: Any, *, allow_zero: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and (number >= 0 if allow_zero else number > 0)


def _bridge_lid_runs(points: Any) -> int:
    runs = 0
    active = False
    for point in points if isinstance(points, (list, tuple)) else ():
        valid = False
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                valid = math.isfinite(float(point[0])) and math.isfinite(float(point[1])) \
                    and float(point[1]) != 0
            except (TypeError, ValueError):
                pass
        if valid and not active:
            runs += 1
        active = valid
    return runs


class StructureCapabilityRegistry:
    def __init__(self, capabilities: tuple[StructureCapability, ...] = CAPABILITIES):
        self.capabilities = capabilities

    def declaration(self, family: str) -> StructureCapability | None:
        matches = [item for item in self.capabilities if family in item.families]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous structure capability registry for {family}: "
                             f"{[item.capability_id for item in matches]}")
        return matches[0] if matches else None

    def evaluate(self, model: Any) -> CapabilityDecision:
        capability = self.declaration(str(model.type))
        if capability is None:
            return CapabilityDecision(
                "UNRECOGNIZED_STRUCTURE", False, False, (),
                f"Structure type {model.type or 'UNKNOWN'} has no registered source geometry capability.",
                ("authoritative export",), (),
            )
        missing: list[str] = []
        source = model.source
        attrs = source.get("source_attributes", {})
        if not model.river: missing.append("river")
        if not model.reach: missing.append("reach")
        if not model.rs: missing.append("structure RS")

        if model.type == "BRIDGE":
            if model.openings:
                return CapabilityDecision(
                    capability.capability_id, True, False, (),
                    "Mixed bridge/culvert geometry was detected; bridge chords and culvert groups cannot "
                    "be collapsed into one longitudinal envelope.",
                    ("authoritative PDF", "authoritative DXF", "CAD Review Package"),
                    (f"culvert groups={len(model.openings)}",),
                )
            if model.upstream is None: missing.append("explicit upstream river/reach/RS")
            if model.downstream is None: missing.append("explicit downstream river/reach/RS")
            if not _finite_positive(source.get("upstream_distance"), allow_zero=True):
                missing.append("Upstream Distance")
            if not _finite_positive(source.get("weir_width")):
                missing.append("Weir Width")
            profiles = source.get("source_profiles", {})
            required = ("US BR Weir Profile", "US BR Lid Profile",
                        "DS BR Weir Profile", "DS BR Lid Profile")
            missing.extend(key for key in required if not profiles.get(key))
            opening_runs = [_bridge_lid_runs(profiles.get(key))
                            for key in ("US BR Lid Profile", "DS BR Lid Profile")]
            if any(count > 1 for count in opening_runs):
                return CapabilityDecision(
                    capability.capability_id, True, False, tuple(sorted(set(missing))),
                    "Multiple disjoint bridge lid openings were detected; the single-envelope bridge rule "
                    "cannot represent each opening independently.",
                    ("authoritative PDF", "authoritative DXF", "CAD Review Package"),
                    (f"upstream lid runs={opening_runs[0]}", f"downstream lid runs={opening_runs[1]}"),
                )
        elif model.type in {"CULVERT", "MULTIPLE_OPENING"}:
            if model.upstream is None: missing.append("explicit upstream river/reach/RS")
            if model.downstream is None: missing.append("explicit downstream river/reach/RS")
            if not model.openings: missing.append("explicitly associated culvert group")
            if model.text_geometry is None: missing.append("unique plan-linked geometry text record")
            required = ("Shape Name", "Barrels", "Rise", "Span", "Length",
                        "US Distance", "Invert US", "Invert DS")
            for index, group in enumerate(model.openings, 1):
                missing.extend(f"group {index} {key}" for key in required
                               if group.get(key) is None or group.get(key) == "")
                if not group.get("association"):
                    missing.append(f"group {index} explicit structure association")
        affected = () if capability.export_eligible and not missing else (
            "authoritative PDF", "authoritative DXF", "CAD Review Package")
        if missing and model.type == "BRIDGE":
            reason = ("Explicit adjacent ordinary XS and source-backed bridge chords are required. "
                      f"Missing source fields: {', '.join(sorted(set(missing)))}.")
        elif missing and model.type in {"CULVERT", "MULTIPLE_OPENING"}:
            reason = ("Explicit upstream/downstream connections must identify adjacent ordinary XS, and every "
                      "opening requires complete source geometry. "
                      f"Missing source fields: {', '.join(sorted(set(missing)))}.")
        else:
            reason = (f"Missing required source evidence: {', '.join(sorted(set(missing)))}."
                      if missing else
                      f"Recognized {capability.capability_id}; type-specific geometry validation is required.")
        return CapabilityDecision(
            capability.capability_id, True, capability.export_eligible and not missing,
            tuple(sorted(set(missing))), reason, affected,
            (capability.normalization_rule, capability.drawing_representation),
        )


STRUCTURE_CAPABILITIES = StructureCapabilityRegistry()

