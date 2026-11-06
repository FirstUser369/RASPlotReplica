"""Evidence-based HEC-RAS HDF schema adapter registry.

Adapters are selected from dataset signatures and identity-bearing fields.  A
HEC-RAS version attribute is retained as provenance only and is never a
selector.  The registry deliberately models only layouts observed in project
fixtures or existing regression fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import h5py


class SchemaAdapterError(ValueError):
    """No unique, verified adapter can read the supplied source."""


@dataclass(frozen=True)
class DatasetSignature:
    path: str
    kind: str = "dataset"
    required_fields: tuple[str, ...] = ()
    required_one_of: tuple[str, ...] = ()

    def verify(self, handle: h5py.File | h5py.Group) -> tuple[bool, str]:
        obj = handle.get(self.path)
        if obj is None:
            return False, f"missing {self.path}"
        expected = h5py.Dataset if self.kind == "dataset" else h5py.Group
        if not isinstance(obj, expected):
            return False, f"{self.path} is not a {self.kind}"
        if self.required_fields:
            fields = tuple(obj.dtype.names or ()) if isinstance(obj, h5py.Dataset) else ()
            missing = sorted(set(self.required_fields) - set(fields))
            if missing:
                return False, f"{self.path} lacks identity fields {missing}"
        if self.required_one_of:
            fields = tuple(obj.dtype.names or ()) if isinstance(obj, h5py.Dataset) else ()
            if not set(self.required_one_of).intersection(fields):
                return False, f"{self.path} requires one of identity fields {list(self.required_one_of)}"
        return True, f"verified {self.path}"


@dataclass(frozen=True)
class HdfSchemaAdapter:
    name: str
    result_type: str
    result_root: str
    cross_section_path: str
    identity_strategy: str
    required_plan: tuple[DatasetSignature, ...]
    required_geometry: tuple[DatasetSignature, ...]
    identity_dataset_path: str = ""
    profile_labels_path: str = ""
    forbidden_plan_paths: tuple[str, ...] = ()
    required_root_attributes: tuple[str, ...] = ("Units System",)
    supported_result_types: tuple[str, ...] = ()
    required_units_metadata: str = "Plan root Units System; companion geometry units must agree when declared"
    structure_limitations: tuple[str, ...] = (
        "Structures require explicit Geometry/Structures inventory identity and source-backed geometry",
        "Unsupported structure shapes and incomplete connectivity remain export-blocking",
    )
    confidence: str = "VERIFIED_SIGNATURE"
    evidence: str = ""

    def match(self, plan: h5py.File, geometry: h5py.File) -> tuple[bool, list[str]]:
        proof: list[str] = []
        for attribute in self.required_root_attributes:
            if attribute not in plan.attrs:
                return False, [f"missing root attribute {attribute}"]
            proof.append(f"verified root attribute {attribute}")
        for signature in self.required_plan:
            valid, detail = signature.verify(plan)
            if not valid:
                return False, [detail]
            proof.append(detail)
        for signature in self.required_geometry:
            valid, detail = signature.verify(geometry)
            if not valid:
                return False, [detail]
            proof.append(detail)
        for path in self.forbidden_plan_paths:
            if plan.get(path) is not None:
                return False, [f"higher-confidence/precedence signature present: {path}"]
            proof.append(f"verified absent {path}")
        return True, proof

    def describe(self, proof: list[str]) -> dict[str, Any]:
        return {
            "name": self.name,
            "result_type": self.result_type,
            "result_root": self.result_root,
            "cross_section_path": self.cross_section_path,
            "identity_strategy": self.identity_strategy,
            "identity_dataset_path": self.identity_dataset_path,
            "profile_labels_path": self.profile_labels_path,
            "supported_result_types": list(self.supported_result_types),
            "required_units_metadata": self.required_units_metadata,
            "structure_limitations": list(self.structure_limitations),
            "confidence": self.confidence,
            "evidence": [self.evidence, *proof] if self.evidence else proof,
        }


GEOMETRY_SIGNATURE = (
    DatasetSignature("Geometry/Cross Sections", "group"),
    DatasetSignature("Geometry/Cross Sections/Attributes", "dataset",
                     ("River", "Reach", "RS", "Len Channel", "Left Bank", "Right Bank")),
    DatasetSignature("Geometry/Cross Sections/Station Elevation Info"),
    DatasetSignature("Geometry/Cross Sections/Station Elevation Values"),
)

UNSTEADY_ROOT = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"
STEADY_ROOT = "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles"
POST_ROOT = "Results/Post Process/Steady/Output/Output Blocks/Base Output/Post Process/Post Process Profiles"


DEFAULT_ADAPTERS = (
    HdfSchemaAdapter(
        name="UNSTEADY_BASE_OUTPUT_EXPLICIT_XS_V1",
        result_type="UNSTEADY_SAVED_TIMESTEPS",
        result_root=UNSTEADY_ROOT,
        cross_section_path=f"{UNSTEADY_ROOT}/Cross Sections",
        identity_strategy="EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES",
        required_plan=(
            DatasetSignature(f"{UNSTEADY_ROOT}/Cross Sections/Water Surface"),
            DatasetSignature(f"{UNSTEADY_ROOT}/Cross Sections/Cross Section Attributes", "dataset",
                             ("River", "Reach"), ("Station", "RS")),
        ),
        required_geometry=GEOMETRY_SIGNATURE,
        supported_result_types=("SAVED_TIMESTEP_WSEL", "DERIVED_MAXIMUM_ENVELOPE"),
        evidence="Observed in unsteady identity/timestamp regression fixtures",
    ),
    HdfSchemaAdapter(
        name="STEADY_BASE_OUTPUT_EXPLICIT_XS_V1",
        result_type="STEADY_PROFILES",
        result_root=STEADY_ROOT,
        cross_section_path=f"{STEADY_ROOT}/Cross Sections",
        identity_strategy="EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES",
        required_plan=(
            DatasetSignature(f"{STEADY_ROOT}/Cross Sections/Water Surface"),
            DatasetSignature(f"{STEADY_ROOT}/Cross Sections/Cross Section Attributes", "dataset",
                             ("River", "Reach"), ("Station", "RS")),
        ),
        required_geometry=GEOMETRY_SIGNATURE,
        forbidden_plan_paths=(UNSTEADY_ROOT,),
        supported_result_types=("SOURCE_WSEL",),
        evidence="Observed in current steady HDF fixtures with explicit result identities",
    ),
    HdfSchemaAdapter(
        name="STEADY_BASE_OUTPUT_GEOMETRY_INFO_LABELS_V1",
        result_type="STEADY_PROFILES",
        result_root=STEADY_ROOT,
        cross_section_path=f"{STEADY_ROOT}/Cross Sections",
        identity_strategy="EXACT_COMBINED_GEOMETRY_INFO_LABEL",
        required_plan=(
            DatasetSignature(f"{STEADY_ROOT}/Cross Sections/Water Surface"),
            DatasetSignature("Results/Steady/Output/Geometry Info/Cross Section Only"),
        ),
        required_geometry=GEOMETRY_SIGNATURE,
        forbidden_plan_paths=(
            UNSTEADY_ROOT,
            f"{STEADY_ROOT}/Cross Sections/Cross Section Attributes",
        ),
        supported_result_types=("SOURCE_WSEL",),
        evidence="Observed HEC-RAS 5.0.7-style steady fixture; version is evidence only",
    ),
    HdfSchemaAdapter(
        name="POST_PROCESS_STEADY_GEOMETRY_INFO_XS_V1",
        result_type="POST_PROCESS_SAVED_TIMESTEPS",
        result_root=POST_ROOT,
        cross_section_path=f"{POST_ROOT}/Cross Sections",
        identity_strategy="EXPLICIT_RESULT_IDENTITY_DATASET",
        required_plan=(
            DatasetSignature(f"{POST_ROOT}/Cross Sections/Water Surface"),
            DatasetSignature("Results/Post Process/Steady/Output/Geometry Info/Cross Section Attributes", "dataset",
                             ("River", "Reach"), ("Station", "RS")),
            DatasetSignature(f"{POST_ROOT}/Profile Dates"),
        ),
        required_geometry=GEOMETRY_SIGNATURE,
        identity_dataset_path="Results/Post Process/Steady/Output/Geometry Info/Cross Section Attributes",
        profile_labels_path=f"{POST_ROOT}/Profile Dates",
        forbidden_plan_paths=(UNSTEADY_ROOT, STEADY_ROOT),
        supported_result_types=("SOURCE_AUTHORITATIVE_MAXIMUM", "SAVED_TIMESTEP_WSEL"),
        evidence=("Observed in the authentic Muncie p04 HDF: exact post-process result-side "
                  "river/reach/station identities, Profile Dates, and WSEL unit metadata; "
                  "base unsteady output remains higher precedence when present"),
    ),
    HdfSchemaAdapter(
        name="POST_PROCESS_STEADY_EXPLICIT_XS_V1",
        result_type="POST_PROCESS_STEADY_PROFILES",
        result_root=POST_ROOT,
        cross_section_path=f"{POST_ROOT}/Cross Sections",
        identity_strategy="EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES",
        required_plan=(
            DatasetSignature(f"{POST_ROOT}/Cross Sections/Water Surface"),
            DatasetSignature(f"{POST_ROOT}/Cross Sections/Cross Section Attributes", "dataset",
                             ("River", "Reach"), ("Station", "RS")),
        ),
        required_geometry=GEOMETRY_SIGNATURE,
        forbidden_plan_paths=(UNSTEADY_ROOT, STEADY_ROOT),
        supported_result_types=("SOURCE_WSEL",),
        confidence="CODED_UNVALIDATED_FIXTURE",
        evidence="Existing reader path; no authorized independent real-model fixture currently available",
    ),
)


class HdfSchemaAdapterRegistry:
    def __init__(self, adapters: tuple[HdfSchemaAdapter, ...] = DEFAULT_ADAPTERS):
        self.adapters = adapters

    def select(self, plan: h5py.File, geometry: h5py.File) -> tuple[HdfSchemaAdapter, dict[str, Any]]:
        matches: list[tuple[HdfSchemaAdapter, list[str]]] = []
        failures: dict[str, list[str]] = {}
        for adapter in self.adapters:
            valid, proof = adapter.match(plan, geometry)
            if valid:
                matches.append((adapter, proof))
            else:
                failures[adapter.name] = proof
        if len(matches) > 1:
            names = [adapter.name for adapter, _proof in matches]
            raise SchemaAdapterError(
                f"Ambiguous HDF schema: verified signatures match multiple adapters {names}. "
                "No adapter was selected.")
        if not matches:
            observed = sorted(name for name, obj in _walk(plan)
                              if isinstance(obj, h5py.Dataset) and name.startswith("Results/"))
            reasons = "; ".join(f"{name}: {details[0]}" for name, details in failures.items())
            raise SchemaAdapterError(
                "Unsupported HDF result schema: no verified adapter signature matched; "
                "required result identity evidence is missing or incompatible. "
                f"Observed result datasets={observed!r}. Adapter evidence failures: {reasons}")
        adapter, proof = matches[0]
        if adapter.confidence == "CODED_UNVALIDATED_FIXTURE":
            raise SchemaAdapterError(
                f"Recognized candidate HDF schema {adapter.name}, but authoritative support is unavailable: "
                f"{adapter.evidence}. Provide an authorized authentic fixture with result identities, units, "
                "and expected hydraulic values before enabling this adapter.")
        return adapter, adapter.describe(proof)


def _walk(handle: h5py.File):
    found: list[tuple[str, Any]] = []
    handle.visititems(lambda name, obj: found.append((name, obj)))
    return found


REGISTRY = HdfSchemaAdapterRegistry()

