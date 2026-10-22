"""Versioned, portable RAS-Plot project document primitives.

This module deliberately has no dependency on live sessions or HDF parsing. A
project document stores user-authored settings and source verification evidence;
it never stores authoritative HDF geometry or hydraulic result arrays.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePath, PureWindowsPath
import re
import tempfile
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROJECT_FORMAT = "rasplot-project"
PROJECT_SCHEMA_VERSION = 1
SEMANTIC_IDENTITY_VERSION = "rasplot-source-identity-v1"
REVIEW_CONTRACT_VERSION = "rasplot-panel-review-v1"
LEGACY_REVIEW_CONTRACT_VERSION = "legacy-runtime-v0"
DEFAULT_MAX_PROJECT_BYTES = 10 * 1024 * 1024
DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024


class ProjectFileError(ValueError):
    """Base class for actionable project-document failures."""


class ProjectFileTooLargeError(ProjectFileError):
    pass


class SourceModifiedError(ProjectFileError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


SourceVerificationStatus = Literal[
    "VERIFIED", "SOURCE_MISSING", "SOURCE_CHANGED",
    "SOURCE_UNREADABLE", "SOURCE_UNSTABLE",
]


class SourceVerificationAttempt(StrictModel):
    path: str
    origin: Literal["PROJECT_RELATIVE", "ABSOLUTE_HINT", "CALLER_REPLACEMENT"]
    status: SourceVerificationStatus
    detail: str


class SourceVerificationResult(StrictModel):
    status: SourceVerificationStatus
    resolved_path: str | None = None
    resolution_origin: Literal[
        "PROJECT_RELATIVE", "ABSOLUTE_HINT", "CALLER_REPLACEMENT"
    ] | None = None
    relocated: bool = False
    fingerprint: "SourceFingerprint | None" = None
    warnings: tuple[str, ...] = ()
    attempts: tuple[SourceVerificationAttempt, ...] = ()
    semantic_identity_required: bool = True


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


def _path_text(value: str, label: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{label} cannot be blank.")
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} contains invalid control characters.")
    return value


class ApplicationIdentity(StrictModel):
    application_version: str = ""
    build_id: str = ""


class SourceFingerprint(StrictModel):
    algorithm: Literal["sha256"] = "sha256"
    sha256: str
    size_bytes: int = Field(ge=0, strict=True)
    mtime_ns: int = Field(ge=0, strict=True)

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError("Source SHA-256 must contain exactly 64 hexadecimal characters.")
        return normalized


class ModelIdentity(StrictModel):
    model_id: str
    ras_version: str = "UNKNOWN"
    plan_identity: dict[str, Any] = Field(default_factory=dict)
    geometry_identity: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_id")
    @classmethod
    def model_id_present(cls, value: str) -> str:
        return _path_text(value, "Model ID")


class SemanticReachIdentity(StrictModel):
    reach_id: str
    river: str
    reach: str
    ordered_xs_ids: list[str]

    @model_validator(mode="after")
    def valid_reach(self) -> "SemanticReachIdentity":
        for label, value in (("Reach ID", self.reach_id), ("River", self.river),
                             ("Reach", self.reach)):
            _path_text(value, label)
        if not self.ordered_xs_ids or any(not value.strip() for value in self.ordered_xs_ids):
            raise ValueError("Semantic reach requires ordered nonblank cross-section identities.")
        if len(self.ordered_xs_ids) != len(set(self.ordered_xs_ids)):
            raise ValueError("Cross-section identities within a reach must be unique.")
        return self


class SemanticProfileIdentity(StrictModel):
    profile_id: str
    source_name: str
    result_type: str
    source_dataset: str
    ordered_xs_ids: list[str]

    @model_validator(mode="after")
    def valid_profile(self) -> "SemanticProfileIdentity":
        for label, value in (("Profile ID", self.profile_id),
                             ("Profile result type", self.result_type),
                             ("Profile source dataset", self.source_dataset)):
            _path_text(value, label)
        if not self.ordered_xs_ids or any(not value.strip() for value in self.ordered_xs_ids):
            raise ValueError("Semantic profile requires ordered nonblank cross-section identities.")
        if len(self.ordered_xs_ids) != len(set(self.ordered_xs_ids)):
            raise ValueError("Profile cross-section identities must be unique.")
        return self


class SemanticStructureIdentity(StrictModel):
    structure_id: str
    river: str
    reach: str
    river_station: str
    structure_type: str
    source_record_id: str

    @model_validator(mode="after")
    def valid_structure(self) -> "SemanticStructureIdentity":
        for label, value in (
            ("Structure ID", self.structure_id), ("Structure river", self.river),
            ("Structure reach", self.reach), ("Structure river station", self.river_station),
            ("Structure type", self.structure_type), ("Structure source record", self.source_record_id),
        ):
            _path_text(value, label)
        return self


class SemanticConnectionIdentity(StrictModel):
    connection_id: str
    upstream_reach_id: str
    downstream_reach_id: str
    transition_distance: float

    @model_validator(mode="after")
    def valid_connection(self) -> "SemanticConnectionIdentity":
        for label, value in (("Connection ID", self.connection_id),
                             ("Upstream reach ID", self.upstream_reach_id),
                             ("Downstream reach ID", self.downstream_reach_id)):
            _path_text(value, label)
        _finite(self.transition_distance, "Junction transition distance")
        if self.transition_distance < 0:
            raise ValueError("Junction transition distance cannot be negative.")
        return self


class SemanticSourceIdentity(StrictModel):
    contract_version: Literal[SEMANTIC_IDENTITY_VERSION] = SEMANTIC_IDENTITY_VERSION
    model_id: str
    ras_version: str
    unit_system: str
    source_datum: str = "UNKNOWN"
    datum_provenance: str = "UNKNOWN"
    plan_identity: dict[str, str] = Field(default_factory=dict)
    geometry_file_type: str = "UNKNOWN"
    geometry_file_version: str = "UNKNOWN"
    reaches: list[SemanticReachIdentity]
    profiles: list[SemanticProfileIdentity]
    structures: list[SemanticStructureIdentity] = Field(default_factory=list)
    connections: list[SemanticConnectionIdentity] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_identities(self) -> "SemanticSourceIdentity":
        _path_text(self.model_id, "Semantic model ID")
        collections = (
            ("reach", [item.reach_id for item in self.reaches]),
            ("cross-section", [xs for item in self.reaches for xs in item.ordered_xs_ids]),
            ("profile", [item.profile_id for item in self.profiles]),
            ("structure", [item.structure_id for item in self.structures]),
            ("connection", [item.connection_id for item in self.connections]),
        )
        for label, identities in collections:
            if len(identities) != len(set(identities)):
                raise ValueError(f"Semantic {label} identities must be unique.")
        return self


class SourceReference(StrictModel):
    kind: Literal["hec-ras-plan-hdf"] = "hec-ras-plan-hdf"
    relative_path: str | None = None
    absolute_path_hint: str | None = None
    file_name: str
    fingerprint: SourceFingerprint
    model_identity: ModelIdentity
    semantic_identity: SemanticSourceIdentity | None = None

    @field_validator("file_name")
    @classmethod
    def valid_file_name(cls, value: str) -> str:
        value = _path_text(value, "Source file name")
        if PurePath(value).name != value or PureWindowsPath(value).name != value:
            raise ValueError("Source file name must not contain a directory path.")
        return value

    @field_validator("relative_path")
    @classmethod
    def valid_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _path_text(value, "Relative source path")
        windows = PureWindowsPath(value)
        portable = PurePath(value.replace("\\", "/"))
        if windows.is_absolute() or portable.is_absolute() or ".." in portable.parts:
            raise ValueError("Relative source path must remain within the project directory.")
        return value

    @field_validator("absolute_path_hint")
    @classmethod
    def valid_absolute_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _path_text(value, "Absolute source path hint")
        if not (PureWindowsPath(value).is_absolute() or Path(value).is_absolute()):
            raise ValueError("Absolute source path hint must be absolute.")
        return value

    @model_validator(mode="after")
    def has_location(self) -> "SourceReference":
        if self.relative_path is None and self.absolute_path_hint is None:
            raise ValueError("Source reference requires a relative path or absolute path hint.")
        for candidate in (self.relative_path, self.absolute_path_hint):
            if candidate is not None and PureWindowsPath(candidate).name.casefold() != self.file_name.casefold():
                raise ValueError("Source path file name does not match source.file_name.")
        return self


class FloodingSourceState(StrictModel):
    source_id: str
    display_name: str
    naming_provenance: Literal["USER_DEFINED", "ENGINEERING_SUPPLIED"]
    ordered_reach_ids: list[str]
    ordered_connection_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_source(self) -> "FloodingSourceState":
        _path_text(self.source_id, "Flooding-source ID")
        _path_text(self.display_name, "Flooding-source name")
        if not self.ordered_reach_ids or any(not item.strip() for item in self.ordered_reach_ids):
            raise ValueError("Flooding source requires ordered nonblank reach identities.")
        if len(self.ordered_reach_ids) != len(set(self.ordered_reach_ids)):
            raise ValueError("Flooding-source reach identities must be unique.")
        if len(self.ordered_connection_ids) != max(0, len(self.ordered_reach_ids) - 1):
            raise ValueError("Flooding source requires one connection identity between adjacent reaches.")
        if len(self.ordered_connection_ids) != len(set(self.ordered_connection_ids)):
            raise ValueError("Flooding-source connection identities must be unique.")
        return self


class EngineeringContext(StrictModel):
    mode: Literal["single_reach", "flooding_source"]
    reach_id: str | None = None
    flooding_source: FloodingSourceState | None = None
    stationing_start: float = 0.0
    stationing_state: Literal["FRESH", "DIRTY"] = "FRESH"

    @field_validator("stationing_start")
    @classmethod
    def finite_start(cls, value: float) -> float:
        return _finite(value, "Stationing start")

    @model_validator(mode="after")
    def valid_mode(self) -> "EngineeringContext":
        if self.mode == "single_reach":
            if not self.reach_id or self.flooding_source is not None:
                raise ValueError("Single-reach context requires reach_id and no flooding source.")
        elif self.reach_id is not None or self.flooding_source is None:
            raise ValueError("Flooding-source context requires flooding_source and no reach_id.")
        return self


class ChannelLengthOverride(StrictModel):
    xs_identity: str
    source_value: float
    working_value: float

    @field_validator("xs_identity")
    @classmethod
    def identity_present(cls, value: str) -> str:
        return _path_text(value, "Cross-section identity")

    @field_validator("source_value", "working_value")
    @classmethod
    def valid_length(cls, value: float) -> float:
        _finite(value, "Channel length")
        if value < 0:
            raise ValueError("Channel length cannot be negative.")
        return value


class ElevationOverride(StrictModel):
    row_identity: str
    field: Literal["MINCHEL", "LOW_CHORD", "HIGH_CHORD"]
    original_value: float | None
    working_value: float | None
    provenance: Literal["USER_ENTERED"] = "USER_ENTERED"

    @field_validator("row_identity")
    @classmethod
    def row_present(cls, value: str) -> str:
        return _path_text(value, "Working-row identity")

    @field_validator("original_value", "working_value")
    @classmethod
    def finite_elevation(cls, value: float | None) -> float | None:
        return value if value is None else _finite(value, "Working elevation")

    @model_validator(mode="after")
    def supported_clear_semantics(self) -> "ElevationOverride":
        if self.field in {"LOW_CHORD", "HIGH_CHORD"} and self.working_value is None:
            raise ValueError("Low/high chord clearing is not an existing supported working-table operation.")
        return self


class LabelOverride(StrictModel):
    row_identity: str
    field: Literal["LABEL_LETTER", "LABEL_TEXT"]
    value: str

    @field_validator("row_identity")
    @classmethod
    def row_present(cls, value: str) -> str:
        return _path_text(value, "Label row identity")


class HydraulicAdjustment(StrictModel):
    xs_identity: str
    profile_id: str
    original_value: float | None
    adjusted_value: float | None
    reason: Literal["USER_EDIT", "USER_FIX_DRAWDOWNS"]
    provenance: Literal["USER_ENTERED"] = "USER_ENTERED"

    @field_validator("xs_identity", "profile_id")
    @classmethod
    def adjustment_identity(cls, value: str) -> str:
        return _path_text(value, "Hydraulic adjustment identity")

    @field_validator("original_value", "adjusted_value")
    @classmethod
    def finite_hydraulic_value(cls, value: float | None) -> float | None:
        return value if value is None else _finite(value, "Hydraulic adjustment value")


class WorkingOverrides(StrictModel):
    channel_lengths: list[ChannelLengthOverride] = Field(default_factory=list)
    elevations: list[ElevationOverride] = Field(default_factory=list)
    labels: list[LabelOverride] = Field(default_factory=list)
    fix_drawdowns_applied: bool = False

    @model_validator(mode="after")
    def unique_cross_sections(self) -> "WorkingOverrides":
        identities = [item.xs_identity for item in self.channel_lengths]
        if len(identities) != len(set(identities)):
            raise ValueError("Channel-length override identities must be unique.")
        elevation_ids = [(item.row_identity, item.field) for item in self.elevations]
        if len(elevation_ids) != len(set(elevation_ids)):
            raise ValueError("Working elevation overrides must have unique row/field identities.")
        label_ids = [(item.row_identity, item.field) for item in self.labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("Working label overrides must have unique row/field identities.")
        return self


class ProfileMapping(StrictModel):
    profile_id: str
    display_name: str
    frequency: str = ""
    mapping_provenance: Literal["UNKNOWN", "USER_ENTERED", "SOURCE"] = "UNKNOWN"
    color: str = "#196bd6"
    linetype: Literal[
        "CONTINUOUS", "DASHED", "DOTTED", "DASHDOT", "CENTER", "HIDDEN", "PHANTOM"
    ] = "CONTINUOUS"
    lineweight: float = 0.3
    legend_text: str = ""
    layer: str

    @field_validator("profile_id")
    @classmethod
    def profile_present(cls, value: str) -> str:
        return _path_text(value, "Profile mapping identity")

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Profile color must use #RRGGBB notation.")
        return value.upper()

    @field_validator("lineweight")
    @classmethod
    def valid_lineweight(cls, value: float) -> float:
        _finite(value, "Profile lineweight")
        if value <= 0:
            raise ValueError("Profile lineweight must be positive.")
        return value

    @field_validator("layer")
    @classmethod
    def valid_layer(cls, value: str) -> str:
        if not value or re.search(r'[<>/\\":;?*|=\x00-\x1f]', value):
            raise ValueError("Profile DXF layer contains reserved characters.")
        return value


class BackwaterState(StrictModel):
    profile_id: str
    elevation: float | None = None
    text: str = ""
    provenance: Literal["USER_ENTERED_REFERENCE_ONLY"] = "USER_ENTERED_REFERENCE_ONLY"

    @field_validator("profile_id")
    @classmethod
    def profile_present(cls, value: str) -> str:
        return _path_text(value, "Backwater profile identity")

    @field_validator("elevation")
    @classmethod
    def finite_elevation(cls, value: float | None) -> float | None:
        return value if value is None else _finite(value, "Backwater elevation")


class ProfileState(StrictModel):
    selected_profile_ids: list[str] = Field(default_factory=list)
    mappings: list[ProfileMapping] = Field(default_factory=list)
    backwater: list[BackwaterState] = Field(default_factory=list)
    user_adjustments: list[HydraulicAdjustment] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_profiles(self) -> "ProfileState":
        if any(not value.strip() for value in self.selected_profile_ids):
            raise ValueError("Selected profile identities cannot be blank.")
        if len(self.selected_profile_ids) != len(set(self.selected_profile_ids)):
            raise ValueError("Selected profile identities must be unique.")
        mapping_ids = [item.profile_id for item in self.mappings]
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("Profile mapping identities must be unique.")
        backwater_ids = [item.profile_id for item in self.backwater]
        if len(backwater_ids) != len(set(backwater_ids)):
            raise ValueError("Backwater profile identities must be unique.")
        adjustment_ids = [(item.xs_identity, item.profile_id) for item in self.user_adjustments]
        if len(adjustment_ids) != len(set(adjustment_ids)):
            raise ValueError("Hydraulic adjustments must have unique XS/profile identities.")
        return self


class RevisionState(StrictModel):
    explicit: bool
    start_station: float
    end_station: float

    @model_validator(mode="after")
    def valid_bounds(self) -> "RevisionState":
        _finite(self.start_station, "Revision start")
        _finite(self.end_station, "Revision end")
        if self.end_station <= self.start_station:
            raise ValueError("Revision end must be greater than revision start.")
        return self


class ReviewEvidence(StrictModel):
    contract_version: Literal[
        LEGACY_REVIEW_CONTRACT_VERSION, REVIEW_CONTRACT_VERSION
    ] = LEGACY_REVIEW_CONTRACT_VERSION
    source_sha256: str
    dependency_sha256: str | None = None
    ordered_panel_definition_sha256: str | None = None
    panel_inputs_sha256: str | None = None
    engineering_version: str = ""

    @field_validator("source_sha256", "dependency_sha256",
                     "ordered_panel_definition_sha256", "panel_inputs_sha256")
    @classmethod
    def hash_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError("Review evidence hashes must be SHA-256 hexadecimal values.")
        return normalized

    @model_validator(mode="after")
    def required_contract_evidence(self) -> "ReviewEvidence":
        if self.contract_version == REVIEW_CONTRACT_VERSION:
            if self.dependency_sha256 is None or self.ordered_panel_definition_sha256 is None:
                raise ValueError(
                    "Review contract v1 requires dependency and ordered panel-definition digests.")
        elif self.panel_inputs_sha256 is None or not self.engineering_version:
            raise ValueError(
                "Legacy review evidence requires panel_inputs_sha256 and engineering_version.")
        return self


class SavedPanel(StrictModel):
    panel: int = Field(gt=0, strict=True)
    panel_suffix: str = ""
    start_station: float
    end_station: float
    start_elevation: float
    end_elevation: float
    panel_x: float | None = None
    panel_y: float | None = None
    h_scale: float
    v_scale: float
    review_state: Literal["REVIEWED", "REVIEW_REQUIRED"] = "REVIEW_REQUIRED"

    @field_validator("panel_suffix")
    @classmethod
    def valid_suffix(cls, value: str) -> str:
        suffix = value.strip()
        if suffix and (not suffix.isascii() or not suffix.isalpha() or len(suffix) > 3):
            raise ValueError("Panel suffix must contain up to three ASCII letters A-Z.")
        return suffix.upper()

    @model_validator(mode="after")
    def valid_geometry(self) -> "SavedPanel":
        values = (self.start_station, self.end_station, self.start_elevation,
                  self.end_elevation, self.h_scale, self.v_scale)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Panel engineering values must be finite.")
        if any(value is not None and not math.isfinite(value) for value in (self.panel_x, self.panel_y)):
            raise ValueError("Panel drawing origins must be finite when provided.")
        if self.end_station <= self.start_station or self.end_elevation <= self.start_elevation:
            raise ValueError("Panel station and elevation bounds must be increasing.")
        if self.h_scale <= 0 or self.v_scale <= 0:
            raise ValueError("Panel scales must be positive.")
        return self


class PanelSet(StrictModel):
    review_state: Literal["REVIEWED", "REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    review_evidence: ReviewEvidence | None = None
    panels: list[SavedPanel] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_panel_ids(self) -> "PanelSet":
        identities = [panel.panel for panel in self.panels]
        if len(identities) != len(set(identities)):
            raise ValueError("Panel IDs must be unique.")
        if self.review_state == "REVIEWED" and self.review_evidence is None:
            raise ValueError("Reviewed panels require review evidence.")
        if self.review_state == "REVIEWED" and not self.panels:
            raise ValueError("A reviewed panel set must contain at least one panel.")
        if any(panel.review_state != self.review_state for panel in self.panels):
            raise ValueError("Panel review states must match the enclosing panel-set state.")
        return self


class ReviewedAnnotation(StrictModel):
    annotation_id: int = Field(gt=0, strict=True)
    distance: float
    elevation: float
    text: str
    orientation: Literal["Horizontal", "Vertical"] = "Vertical"
    underline: bool = False
    adjusted: bool = False
    claimed_review_state: Literal["REVIEWED", "REVIEW_REQUIRED"] = "REVIEW_REQUIRED"

    @field_validator("distance", "elevation")
    @classmethod
    def finite_position(cls, value: float) -> float:
        return _finite(value, "Annotation position")

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or any(ord(character) < 32 and character not in "\t" for character in value):
            raise ValueError("Annotation text must be nonblank and contain no control characters.")
        return value


class DrawingSettings(StrictModel):
    page_size: str = "11x17"
    distance_unit: str = "Feet"
    default_h_scale: float = 500.0
    default_v_scale: float = 5.0
    station_origin: float = 0.0
    station_minor: float | None = None
    station_major: float | None = None
    elevation_origin: float = 0.0
    elevation_minor: float | None = None
    elevation_major: float | None = None
    agency: str = ""
    use_node_names: bool = False

    @model_validator(mode="after")
    def valid_grid(self) -> "DrawingSettings":
        values = (self.station_origin, self.elevation_origin, self.station_minor,
                  self.station_major, self.elevation_minor, self.elevation_major,
                  self.default_h_scale, self.default_v_scale)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("Drawing grid values must be finite.")
        intervals = (self.station_minor, self.station_major,
                     self.elevation_minor, self.elevation_major)
        if any(value is not None and value <= 0 for value in intervals):
            raise ValueError("Drawing grid intervals must be positive.")
        if self.default_h_scale <= 0 or self.default_v_scale <= 0:
            raise ValueError("Default drawing scales must be positive.")
        return self


class ProjectInformation(StrictModel):
    stream_name: str = ""
    community: str = ""
    county: str = ""
    state: str = ""
    vertical_datum: str = "UNKNOWN"
    datum_provenance: str = "UNKNOWN"
    require_known_datum: bool = False
    stream_distance_label: str = ""
    start_panel_no: int = Field(default=1, gt=0, strict=True)
    table_name: str = "Table 24"
    table_title: str = "FLOODWAY DATA"


class ProjectDocument(StrictModel):
    format: Literal[PROJECT_FORMAT] = PROJECT_FORMAT
    schema_version: Literal[PROJECT_SCHEMA_VERSION] = PROJECT_SCHEMA_VERSION
    project_id: str
    saved_at_utc: str
    created_with: ApplicationIdentity
    source: SourceReference
    engineering_context: EngineeringContext
    working_overrides: WorkingOverrides = Field(default_factory=WorkingOverrides)
    profiles: ProfileState = Field(default_factory=ProfileState)
    revision: RevisionState
    panel_set: PanelSet
    drawing_settings: DrawingSettings = Field(default_factory=DrawingSettings)
    project_information: ProjectInformation = Field(default_factory=ProjectInformation)
    reviewed_annotations: list[ReviewedAnnotation] = Field(default_factory=list)

    @field_validator("project_id")
    @classmethod
    def valid_project_id(cls, value: str) -> str:
        try:
            UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("Project ID must be a UUID.") from exc
        return value

    @model_validator(mode="after")
    def consistent_durable_state(self) -> "ProjectDocument":
        annotation_ids = [item.annotation_id for item in self.reviewed_annotations]
        if len(annotation_ids) != len(set(annotation_ids)):
            raise ValueError("Annotation identities must be unique.")
        reasons = {item.reason for item in self.profiles.user_adjustments}
        has_fix_adjustment = "USER_FIX_DRAWDOWNS" in reasons
        if self.working_overrides.fix_drawdowns_applied != has_fix_adjustment:
            raise ValueError("Fix Drawdowns state must match its saved hydraulic adjustments.")
        if self.engineering_context.stationing_state == "DIRTY" and self.panel_set.review_state == "REVIEWED":
            raise ValueError("Dirty stationing cannot claim reviewed panels.")
        if self.source.semantic_identity is not None:
            if self.source.semantic_identity.model_id != self.source.model_identity.model_id:
                raise ValueError("Semantic and source model identities must match.")
            known_profiles = {item.profile_id for item in self.source.semantic_identity.profiles}
            referenced_profiles = set(self.profiles.selected_profile_ids)
            referenced_profiles.update(item.profile_id for item in self.profiles.mappings)
            referenced_profiles.update(item.profile_id for item in self.profiles.backwater)
            referenced_profiles.update(item.profile_id for item in self.profiles.user_adjustments)
            if not referenced_profiles <= known_profiles:
                raise ValueError("Durable profile state references an unknown semantic profile identity.")
            known_xs = {xs for reach in self.source.semantic_identity.reaches
                        for xs in reach.ordered_xs_ids}
            referenced_xs = {item.xs_identity for item in self.working_overrides.channel_lengths}
            referenced_xs.update(item.xs_identity for item in self.profiles.user_adjustments)
            if not referenced_xs <= known_xs:
                raise ValueError("Durable working state references an unknown semantic cross section.")
        if (self.panel_set.review_evidence is not None
                and self.panel_set.review_evidence.source_sha256 != self.source.fingerprint.sha256):
            raise ValueError("Review evidence source SHA-256 must match the saved source fingerprint.")
        return self


def deserialize_project_document(data: str | bytes) -> ProjectDocument:
    """Parse and strictly validate a project document."""
    try:
        if isinstance(data, bytes):
            text = data.decode("utf-8-sig")
        elif isinstance(data, str):
            text = data
        else:
            raise TypeError("Project document must be UTF-8 text or bytes.")
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProjectFileError(f"Invalid RAS-Plot project JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectFileError("RAS-Plot project JSON must contain one object at its root.")
    try:
        return ProjectDocument.model_validate(raw)
    except ValueError as exc:
        raise ProjectFileError(f"Invalid RAS-Plot project document: {exc}") from exc


def serialize_project_document(document: ProjectDocument | dict[str, Any]) -> bytes:
    """Return canonical, finite UTF-8 JSON ending in one newline."""
    try:
        validated = document if isinstance(document, ProjectDocument) else ProjectDocument.model_validate(document)
        text = json.dumps(validated.model_dump(mode="json"), ensure_ascii=False,
                          indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ProjectFileError(f"Cannot serialize RAS-Plot project document: {exc}") from exc
    return text.encode("utf-8")


def read_project_file(path: str | Path, *, max_bytes: int = DEFAULT_MAX_PROJECT_BYTES) -> ProjectDocument:
    source = Path(path)
    if max_bytes <= 0:
        raise ValueError("Project-file size limit must be positive.")
    try:
        size = source.stat().st_size
        if size > max_bytes:
            raise ProjectFileTooLargeError(
                f"RAS-Plot project file is {size} bytes; limit is {max_bytes} bytes.")
        with source.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except ProjectFileTooLargeError:
        raise
    except OSError as exc:
        raise ProjectFileError(f"Cannot read RAS-Plot project file: {exc}") from exc
    if len(data) > max_bytes:
        raise ProjectFileTooLargeError(f"RAS-Plot project file exceeds {max_bytes} bytes.")
    return deserialize_project_document(data)


def write_project_file(path: str | Path, document: ProjectDocument | dict[str, Any], *,
                       overwrite: bool = False) -> Path:
    """Atomically write validated JSON beside its destination.

    With overwrite disabled, an existing destination is never replaced. The
    no-overwrite path uses an atomic hard-link creation to avoid a check/write
    race. With overwrite enabled, os.replace provides atomic replacement.
    """
    destination = Path(path)
    if destination.suffix.lower() != ".json" or not destination.name.lower().endswith(".rasplot.json"):
        raise ProjectFileError("Project file name must end with .rasplot.json.")
    if not destination.parent.is_dir():
        raise ProjectFileError("Project file directory does not exist.")
    payload = serialize_project_document(document)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, destination)
            temporary = None
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise ProjectFileError("Project file already exists; use explicit overwrite or Save As.")
            temporary.unlink()
            temporary = None
        return destination
    except ProjectFileError:
        raise
    except OSError as exc:
        raise ProjectFileError(f"Cannot write RAS-Plot project file atomically: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def fingerprint_source(path: str | Path, *,
                       chunk_bytes: int = DEFAULT_HASH_CHUNK_BYTES) -> SourceFingerprint:
    """Stream a stable SHA-256 fingerprint without loading the HDF into memory."""
    if chunk_bytes <= 0:
        raise ValueError("Hash chunk size must be positive.")
    source = Path(path)
    try:
        before = source.stat()
        if not source.is_file():
            raise ProjectFileError("HDF source fingerprint requires a regular file.")
        digest = hashlib.sha256()
        read_bytes = 0
        with source.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                read_bytes += len(chunk)
        after = source.stat()
    except ProjectFileError:
        raise
    except OSError as exc:
        raise ProjectFileError(f"Cannot fingerprint HDF source: {exc}") from exc
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if read_bytes != before.st_size or any(getattr(before, field) != getattr(after, field)
                                           for field in stable_fields):
        raise SourceModifiedError("HDF source changed while its fingerprint was being calculated.")
    return SourceFingerprint(sha256=digest.hexdigest(), size_bytes=read_bytes,
                             mtime_ns=after.st_mtime_ns)


def _candidate_path(value: str | Path, *, require_absolute: bool,
                    label: str) -> Path:
    text = _path_text(str(value), label)
    candidate = Path(text)
    if require_absolute and not candidate.is_absolute():
        raise ProjectFileError(f"{label} must be an absolute local path.")
    if not re.search(r"\.p[0-9]{2}\.hdf$", candidate.name, re.IGNORECASE):
        raise ProjectFileError(f"{label} must identify a HEC-RAS plan HDF like *.pXX.hdf.")
    try:
        return candidate.resolve(strict=False)
    except OSError as exc:
        raise ProjectFileError(f"Cannot normalize {label.lower()}: {exc}") from exc


def verify_project_source(
    project_path: str | Path,
    source: SourceReference,
    *,
    replacement_path: str | Path | None = None,
) -> SourceVerificationResult:
    """Resolve and fingerprint a project's HDF without parsing or modifying it.

    Stored candidates are considered in deterministic order: project-relative,
    then the absolute hint, then an explicitly caller-provided replacement.
    A candidate is accepted only when both byte size and SHA-256 digest match.
    Modification time is retained as useful evidence but never decides identity.
    """
    project = Path(project_path)
    if not project.name.lower().endswith(".rasplot.json"):
        raise ProjectFileError("Project path must end with .rasplot.json.")
    try:
        project = project.resolve(strict=False)
        project_root = project.parent.resolve(strict=True)
    except OSError as exc:
        raise ProjectFileError(f"Cannot normalize project path: {exc}") from exc

    candidates: list[tuple[Path, str]] = []
    attempts: list[SourceVerificationAttempt] = []
    warnings: list[str] = []

    if source.relative_path is not None:
        try:
            relative = _candidate_path(
                project_root / Path(source.relative_path), require_absolute=True,
                label="Project-relative HDF path")
            try:
                relative.relative_to(project_root)
            except ValueError as exc:
                raise ProjectFileError(
                    "Project-relative HDF path resolves outside the project directory.") from exc
            candidates.append((relative, "PROJECT_RELATIVE"))
        except ProjectFileError as exc:
            detail = str(exc)
            attempts.append(SourceVerificationAttempt(
                path=str(source.relative_path), origin="PROJECT_RELATIVE",
                status="SOURCE_UNREADABLE", detail=detail))
            warnings.append(detail)

    if source.absolute_path_hint is not None:
        try:
            candidates.append((_candidate_path(
                source.absolute_path_hint, require_absolute=True,
                label="Stored absolute HDF path"), "ABSOLUTE_HINT"))
        except ProjectFileError as exc:
            detail = str(exc)
            attempts.append(SourceVerificationAttempt(
                path=str(source.absolute_path_hint), origin="ABSOLUTE_HINT",
                status="SOURCE_UNREADABLE", detail=detail))
            warnings.append(detail)

    if replacement_path is not None:
        try:
            candidates.append((_candidate_path(
                replacement_path, require_absolute=True,
                label="Replacement HDF path"), "CALLER_REPLACEMENT"))
        except ProjectFileError as exc:
            detail = str(exc)
            attempts.append(SourceVerificationAttempt(
                path=str(replacement_path), origin="CALLER_REPLACEMENT",
                status="SOURCE_UNREADABLE", detail=detail))
            warnings.append(detail)

    unique: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for candidate, origin in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            unique.append((candidate, origin))
            seen.add(key)

    original_hint: Path | None = None
    if source.absolute_path_hint is not None:
        try:
            original_hint = _candidate_path(
                source.absolute_path_hint, require_absolute=True,
                label="Stored absolute HDF path")
        except ProjectFileError:
            pass

    for candidate, origin in unique:
        if not candidate.exists():
            detail = f"HDF source was not found at {candidate}."
            attempts.append(SourceVerificationAttempt(
                path=str(candidate), origin=origin,
                status="SOURCE_MISSING", detail=detail))
            warnings.append(detail)
            continue
        if not candidate.is_file():
            detail = f"HDF source path is not a regular file: {candidate}."
            attempts.append(SourceVerificationAttempt(
                path=str(candidate), origin=origin,
                status="SOURCE_UNREADABLE", detail=detail))
            warnings.append(detail)
            continue
        try:
            actual = fingerprint_source(candidate)
        except SourceModifiedError as exc:
            detail = str(exc)
            attempts.append(SourceVerificationAttempt(
                path=str(candidate), origin=origin,
                status="SOURCE_UNSTABLE", detail=detail))
            warnings.append(detail)
            continue
        except ProjectFileError as exc:
            detail = str(exc)
            attempts.append(SourceVerificationAttempt(
                path=str(candidate), origin=origin,
                status="SOURCE_UNREADABLE", detail=detail))
            warnings.append(detail)
            continue

        if (actual.size_bytes != source.fingerprint.size_bytes
                or actual.sha256 != source.fingerprint.sha256):
            detail = (
                f"HDF source fingerprint does not match the saved project: {candidate}. "
                "Treat this source as changed and require engineering review.")
            attempts.append(SourceVerificationAttempt(
                path=str(candidate), origin=origin,
                status="SOURCE_CHANGED", detail=detail))
            warnings.append(detail)
            continue

        if actual.mtime_ns != source.fingerprint.mtime_ns:
            warnings.append(
                "HDF source modification time differs, but byte size and SHA-256 match; "
                "modification time is informational only.")
        relocated = original_hint is not None and os.path.normcase(str(candidate)) != os.path.normcase(str(original_hint))
        if relocated:
            warnings.append(
                "HDF source was verified at a relocated path; Save can update the path reference later.")
        attempts.append(SourceVerificationAttempt(
            path=str(candidate), origin=origin, status="VERIFIED",
            detail="HDF byte size and SHA-256 match the saved project."))
        return SourceVerificationResult(
            status="VERIFIED", resolved_path=str(candidate),
            resolution_origin=origin, relocated=relocated,
            fingerprint=actual, warnings=tuple(warnings), attempts=tuple(attempts))

    precedence = ("SOURCE_UNSTABLE", "SOURCE_CHANGED", "SOURCE_UNREADABLE", "SOURCE_MISSING")
    status = next((value for value in precedence
                   if any(attempt.status == value for attempt in attempts)), "SOURCE_MISSING")
    representative = next((attempt for attempt in attempts if attempt.status == status), None)
    return SourceVerificationResult(
        status=status,
        resolved_path=representative.path if representative else None,
        resolution_origin=representative.origin if representative else None,
        relocated=False, fingerprint=None,
        warnings=tuple(warnings or ("No HDF source candidate was available.",)),
        attempts=tuple(attempts),
    )
