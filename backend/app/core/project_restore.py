"""Durable project capture and conservative candidate-session restoration."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .model_topology import stable_id
from .project_file import (
    ApplicationIdentity, BackwaterState, ChannelLengthOverride, DrawingSettings,
    EngineeringUnitManifest,
    ElevationOverride, EngineeringContext, FloodingSourceState, HydraulicAdjustment,
    LabelOverride, ModelIdentity, PanelSet, ProfileMapping, ProfileState,
    ProjectDocument, ProjectInformation, ReviewEvidence, ReviewedAnnotation,
    RevisionState, SavedPanel, SemanticConnectionIdentity, SemanticProfileIdentity,
    SemanticReachIdentity, SemanticSourceIdentity, SemanticStructureIdentity,
    SourceFingerprint, SourceReference, WorkingOverrides, REVIEW_CONTRACT_VERSION,
    fingerprint_source,
)
from .rasplot_db import Landmark, PanelDef, model_station_extent
from .state_contract import (
    mark_panels_review_required, ordered_panel_definition_digest,
    panel_dependency_digest, panels_stale, review_panels, stationing_dirty,
    validate_collection,
)


class ProjectRestoreError(ValueError):
    pass


def _text_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in sorted(value.items())
            if v is not None and not isinstance(v, (dict, list, tuple))}


def semantic_source_identity(plan: Any) -> SemanticSourceIdentity:
    topology = getattr(plan, "topology", None)
    reaches: list[SemanticReachIdentity] = []
    for reach in plan.reaches:
        rid = stable_id("reach", plan.model_id, reach.river, reach.reach)
        if topology:
            match = next((item for item in topology.reaches.values()
                          if item.river_name == reach.river and item.reach_name == reach.reach), None)
            if match:
                rid = match.id
        reaches.append(SemanticReachIdentity(
            reach_id=rid, river=reach.river, reach=reach.reach,
            ordered_xs_ids=[stable_id("xs", plan.model_id, xs.river, xs.reach, xs.rs)
                            for xs in reach.cross_sections]))
    profiles = [SemanticProfileIdentity(
        profile_id=p.profile_id, source_name=p.name, result_type=p.result_type,
        source_dataset=p.source_dataset or p.source_path or "UNKNOWN",
        ordered_xs_ids=[stable_id("xs", plan.model_id, *identity) for identity in p.xs_ids],
    ) for p in plan.profiles]
    structures = [SemanticStructureIdentity(
        structure_id=stable_id("structure", plan.model_id, s.river, s.reach, s.rs, s.type, s.index),
        river=s.river, reach=s.reach, river_station=s.rs, structure_type=s.type,
        source_record_id=s.source_id or f"{s.source_path}#{s.index}",
    ) for reach in plan.reaches for s in reach.structures]
    connections = []
    if topology:
        for item in topology.connections.values():
            if item.transition_distance is not None:
                connections.append(SemanticConnectionIdentity(
                    connection_id=item.id, upstream_reach_id=item.upstream_reach_id,
                    downstream_reach_id=item.downstream_reach_id,
                    transition_distance=float(item.transition_distance)))
    capabilities = getattr(plan, "capabilities", None)
    plan_identity = _text_dict(getattr(capabilities, "plan_identity", {}))
    geometry_identity = getattr(capabilities, "geometry_identity", {}) or {}
    datum_provenance = getattr(capabilities, "datum_provenance", {}) or {}
    return SemanticSourceIdentity(
        model_id=plan.model_id, ras_version=plan.ras_version,
        unit_system=plan.source_units, source_datum=plan.source_datum or "UNKNOWN",
        datum_provenance=str(datum_provenance.get("provenance", datum_provenance.get("source", "UNKNOWN"))),
        plan_identity=plan_identity,
        geometry_file_type=str(geometry_identity.get("file_type", "UNKNOWN")),
        geometry_file_version=str(geometry_identity.get("file_version", "UNKNOWN")),
        reaches=reaches, profiles=profiles, structures=structures,
        connections=connections)


def _relative_source(project_path: Path, source_path: Path) -> str | None:
    try:
        relative = Path(os.path.relpath(source_path, project_path.parent))
        return str(relative) if ".." not in relative.parts else None
    except ValueError:
        return None


def build_project_document(session: dict[str, Any], project_path: str | Path,
                           application_version: str = "", build_id: str = "") -> ProjectDocument:
    if "db" not in session:
        raise ProjectRestoreError("Select an engineering reach or flooding source before saving a project.")
    db, plan = session["db"], session["plan"]
    source_path = Path(session["path"]).resolve(strict=True)
    fingerprint = fingerprint_source(source_path)
    opened_fingerprint = session.get("source_fingerprint")
    if opened_fingerprint is not None and (
            fingerprint.sha256 != opened_fingerprint.sha256
            or fingerprint.size_bytes != opened_fingerprint.size_bytes):
        raise ProjectRestoreError(
            "The HDF source changed after this session opened. Reopen the HDF before saving the project.")
    semantic = semantic_source_identity(plan)
    extent = model_station_extent(db.working)
    if extent is None:
        raise ProjectRestoreError("The working profile has no finite station extent.")
    active = session.get("active_flooding_source")
    if active is None:
        reach_idx = session.get("reach_idx")
        if reach_idx is None or reach_idx >= len(plan.reaches):
            raise ProjectRestoreError("The active reach identity is unavailable.")
        reach = plan.reaches[reach_idx]
        reach_id = stable_id("reach", plan.model_id, reach.river, reach.reach)
        if plan.topology:
            match = next((r for r in plan.topology.reaches.values()
                          if r.river_name == reach.river and r.reach_name == reach.reach), None)
            if match:
                reach_id = match.id
        context = EngineeringContext(mode="single_reach", reach_id=reach_id,
            stationing_start=db.stationing_start,
            stationing_state="DIRTY" if stationing_dirty(db) else "FRESH")
    else:
        context = EngineeringContext(mode="flooding_source", stationing_start=db.stationing_start,
            stationing_state="DIRTY" if stationing_dirty(db) else "FRESH",
            flooding_source=FloodingSourceState(
                source_id=active.id, display_name=active.display_name,
                naming_provenance=str(active.provenance.get("naming", "USER_DEFINED")),
                direction=active.direction,
                topology_provenance=dict(active.provenance),
                ordered_reach_ids=[s.reach_id for s in active.ordered_segments],
                ordered_connection_ids=[s.connection_to_next for s in active.ordered_segments[:-1]]))

    lengths = [ChannelLengthOverride(
        xs_identity=row.source_identity,
        source_value=float(row.source_length_chnl if row.source_length_chnl is not None else row.length_chnl),
        working_value=float(row.length_chnl))
        for row in db.working if row.kind == "xs" and row.source_identity
        and row.source_length_chnl is not None and row.length_chnl != row.source_length_chnl]
    elevations: list[ElevationOverride] = []
    labels: list[LabelOverride] = []
    adjustments: dict[tuple[str, str], HydraulicAdjustment] = {}
    for row in db.working:
        if row.ground_provenance.get("method") == "USER_ENTERED":
            elevations.append(ElevationOverride(row_identity=row.row_identity,
                field="MINCHEL", original_value=row.ground_provenance.get("original_value"),
                working_value=row.minchel))
        if row.kind == "xs" and row.low_chord is not None:
            elevations.append(ElevationOverride(row_identity=row.row_identity,
                field="LOW_CHORD", original_value=None, working_value=row.low_chord))
        if row.kind == "xs" and row.high_chord is not None:
            elevations.append(ElevationOverride(row_identity=row.row_identity,
                field="HIGH_CHORD", original_value=None, working_value=row.high_chord))
        # Empty labels are the reproducible source default and do not need one
        # durable record per XS. Legacy files containing explicit empty values
        # remain accepted by the restoration loop below.
        if row.label_letter:
            labels.append(LabelOverride(row_identity=row.row_identity,
                                        field="LABEL_LETTER", value=row.label_letter))
        if row.label_text:
            labels.append(LabelOverride(row_identity=row.row_identity,
                                        field="LABEL_TEXT", value=row.label_text))
        for item in row.adjustments:
            key = (row.source_identity, str(item.get("profile_id", "")))
            adjustments[key] = HydraulicAdjustment(
                xs_identity=key[0], profile_id=key[1],
                original_value=item.get("original_value"), adjusted_value=item.get("adjusted_value"),
                reason=item.get("reason", "USER_EDIT"), provenance="USER_ENTERED")
    mappings = [ProfileMapping(
        profile_id=p.profile_id, display_name=p.display_name or p.name,
        frequency=p.frequency, mapping_provenance=p.mapping_provenance,
        color=p.color, linetype=p.linetype, lineweight=p.lineweight,
        legend_text=p.legend_text, layer=p.layer or f"PROF_{p.profile_id}") for p in db.profiles]
    backwater = [BackwaterState(profile_id=p.profile_id, elevation=p.backwater_elev,
                                text=p.backwater_text)
                 for p in db.profiles if p.backwater_elev is not None or p.backwater_text]
    reviewed = bool(db.panels) and not panels_stale(db)
    evidence = ReviewEvidence(
        contract_version=REVIEW_CONTRACT_VERSION, source_sha256=fingerprint.sha256,
        dependency_sha256=panel_dependency_digest(db),
        ordered_panel_definition_sha256=ordered_panel_definition_digest(db.panels)) if reviewed else None
    panel_state = "REVIEWED" if reviewed else "REVIEW_REQUIRED"
    panels = [SavedPanel(**asdict(p), review_state=panel_state) for p in db.panels]
    h = db.header
    explicit = h.revision_start_station is not None and h.revision_end_station is not None
    project = Path(project_path).resolve(strict=False)
    document = ProjectDocument(
        project_id=session.setdefault("project_id", str(uuid4())),
        saved_at_utc=datetime.now(timezone.utc).isoformat(),
        created_with=ApplicationIdentity(application_version=application_version, build_id=build_id),
        engineering_units=(EngineeringUnitManifest(**db.unit_contract)
                           if db.unit_contract else None),
        source=SourceReference(
            relative_path=_relative_source(project, source_path),
            absolute_path_hint=str(source_path), file_name=source_path.name,
            fingerprint=fingerprint,
            model_identity=ModelIdentity(model_id=plan.model_id, ras_version=plan.ras_version,
                plan_identity=_text_dict(getattr(getattr(plan, "capabilities", None), "plan_identity", {})),
                geometry_identity=_text_dict(getattr(getattr(plan, "capabilities", None), "geometry_identity", {}))),
            semantic_identity=semantic),
        engineering_context=context,
        working_overrides=WorkingOverrides(channel_lengths=lengths, elevations=elevations,
            labels=labels, fix_drawdowns_applied=any(a.reason == "USER_FIX_DRAWDOWNS" for a in adjustments.values())),
        profiles=ProfileState(selected_profile_ids=[p.profile_id for p in db.profiles if p.selected],
            mappings=mappings, backwater=backwater, user_adjustments=list(adjustments.values())),
        revision=RevisionState(explicit=explicit,
            start_station=h.revision_start_station if explicit else extent[0],
            end_station=h.revision_end_station if explicit else extent[1]),
        panel_set=PanelSet(review_state=panel_state, review_evidence=evidence, panels=panels),
        drawing_settings=DrawingSettings(page_size=h.page_size, distance_unit=h.unit,
            default_h_scale=h.h_scale, default_v_scale=h.v_scale,
            station_origin=db.stationing_start, use_node_names=db.use_node_names),
        project_information=ProjectInformation(stream_name=h.stream_name, community=h.community,
            county=h.county, state=h.state, vertical_datum=h.vertical_datum,
            datum_provenance=h.datum_provenance, require_known_datum=h.require_known_datum,
            stream_distance_label=h.stream_distance_label, start_panel_no=h.start_panel_no,
            table_name=h.table_name, table_title=h.table_title),
        reviewed_annotations=[ReviewedAnnotation(annotation_id=l.id, distance=l.distance,
            elevation=l.elevation, text=l.text, orientation=l.orientation,
            underline=l.underline, adjusted=l.adjusted,
            claimed_review_state=panel_state) for l in db.landmarks])
    return document


def restore_candidate_session(session_id: str, document: ProjectDocument) -> list[str]:
    """Apply durable state to an uncommitted HDF session or raise atomically."""
    from ..routers import hecras_api as api
    session = api._SESSIONS[session_id]
    actual_semantic = semantic_source_identity(session["plan"])
    actual_unit_contract = getattr(session["plan"], "unit_contract", {})
    if actual_semantic.unit_system == "SI":
        if document.engineering_units is None:
            raise ProjectRestoreError("Saved SI project lacks the required canonical unit manifest.")
        if document.engineering_units.model_dump(mode="json") != actual_unit_contract:
            raise ProjectRestoreError("Saved SI project unit contract is incompatible with the verified HDF source.")
    elif (document.engineering_units is not None
          and document.engineering_units.model_dump(mode="json") != actual_unit_contract):
        raise ProjectRestoreError("Saved project unit contract is incompatible with the verified HDF source.")
    legacy_semantic = document.source.semantic_identity is None
    if legacy_semantic:
        saved_model = document.source.model_identity
        if (actual_semantic.model_id != saved_model.model_id
                or (saved_model.ras_version not in ("", "UNKNOWN")
                    and actual_semantic.ras_version != saved_model.ras_version)):
            raise ProjectRestoreError("The verified HDF model identity is incompatible with the legacy saved project.")
    elif actual_semantic.model_dump(mode="json") != document.source.semantic_identity.model_dump(mode="json"):
        raise ProjectRestoreError("The verified HDF is semantically incompatible with the saved project.")

    ctx = document.engineering_context
    if ctx.mode == "single_reach":
        match_index = None
        for index, reach in enumerate(session["plan"].reaches):
            rid = stable_id("reach", session["plan"].model_id, reach.river, reach.reach)
            topology = session["plan"].topology
            if topology:
                found = next((r for r in topology.reaches.values()
                              if r.river_name == reach.river and r.reach_name == reach.reach), None)
                if found:
                    rid = found.id
            if rid == ctx.reach_id:
                match_index = index
                break
        if match_index is None:
            raise ProjectRestoreError("Saved reach identity is not present in the verified HDF.")
        api._rebuild_db(session_id, match_index)
    else:
        fs = ctx.flooding_source
        api.commit_flooding_source(session_id, api.FloodingSourceRequest(
            display_name=fs.display_name, reach_ids=fs.ordered_reach_ids,
            naming_provenance=fs.naming_provenance, start_cumdist=ctx.stationing_start,
            working_lengths={item.xs_identity: item.working_value
                             for item in document.working_overrides.channel_lengths}))
        restored_source = session.get("active_flooding_source")
        restored_connections = [segment.connection_to_next
                                for segment in restored_source.ordered_segments[:-1]]
        if (restored_source.id != fs.source_id
                or restored_source.direction != fs.direction
                or restored_connections != fs.ordered_connection_ids):
            raise ProjectRestoreError(
                "Saved flooding-source route identities do not match the verified topology.")
        saved_provenance = fs.topology_provenance
        if saved_provenance and any(
                restored_source.provenance.get(key) != value
                for key, value in saved_provenance.items()):
            raise ProjectRestoreError(
                "Saved flooding-source topology provenance does not match the verified route.")
    db = api._session_db(session_id)
    rows = {row.row_identity: row for row in db.working}
    xs_rows = {row.source_identity: row for row in db.working if row.kind == "xs"}
    for item in document.working_overrides.channel_lengths:
        row = xs_rows.get(item.xs_identity)
        if row is None:
            raise ProjectRestoreError(f"Saved channel-length cross section is missing: {item.xs_identity}")
        source_value = row.source_length_chnl if row.source_length_chnl is not None else row.length_chnl
        if float(source_value) != item.source_value:
            raise ProjectRestoreError(f"Saved channel-length source value changed: {item.xs_identity}")
        row.length_chnl = item.working_value
    if ctx.stationing_state == "FRESH":
        api.post_recalc_cumdist(session_id, api.CumdistRequest(start=ctx.stationing_start))
    for item in document.working_overrides.elevations:
        row = rows.get(item.row_identity)
        if row is None:
            raise ProjectRestoreError(f"Saved working row is missing: {item.row_identity}")
        setattr(row, {"MINCHEL":"minchel", "LOW_CHORD":"low_chord", "HIGH_CHORD":"high_chord"}[item.field], item.working_value)
        if item.field == "MINCHEL":
            row.ground_provenance = {
                "state": "VALID" if item.working_value is not None else "MISSING",
                "method": "USER_ENTERED", "original_value": item.original_value,
                "reason": "Restored user-authored working ground",
            }
    for item in document.working_overrides.labels:
        row = rows.get(item.row_identity)
        if row is None:
            raise ProjectRestoreError(f"Saved label row is missing: {item.row_identity}")
        setattr(row, "label_letter" if item.field == "LABEL_LETTER" else "label_text", item.value or None)
    profile_by = {p.profile_id: p for p in db.profiles}
    for mapping in document.profiles.mappings:
        profile = profile_by.get(mapping.profile_id)
        if profile is None:
            raise ProjectRestoreError(f"Saved profile is missing: {mapping.profile_id}")
        for name in ("display_name", "frequency", "mapping_provenance", "color", "linetype",
                     "lineweight", "legend_text", "layer"):
            setattr(profile, name, getattr(mapping, name))
    selected = set(document.profiles.selected_profile_ids)
    for profile in db.profiles:
        profile.selected = profile.profile_id in selected
    for item in document.profiles.backwater:
        profile = profile_by[item.profile_id]
        profile.backwater_elev, profile.backwater_text = item.elevation, item.text
    for item in document.profiles.user_adjustments:
        row = xs_rows.get(item.xs_identity)
        if row is None or item.profile_id not in profile_by:
            raise ProjectRestoreError("A saved hydraulic adjustment identity is missing from the verified HDF.")
        row.profiles[item.profile_id] = item.adjusted_value
        row.adjustments.append(item.model_dump(mode="json"))

    info, drawing = document.project_information, document.drawing_settings
    for name in ("stream_name", "community", "county", "state", "vertical_datum",
                 "datum_provenance", "require_known_datum", "stream_distance_label",
                 "start_panel_no", "table_name", "table_title"):
        setattr(db.header, name, getattr(info, name))
    db.header.page_size, db.header.unit = drawing.page_size, drawing.distance_unit
    db.header.h_scale, db.header.v_scale = drawing.default_h_scale, drawing.default_v_scale
    db.use_node_names = drawing.use_node_names
    if document.revision.explicit:
        extent = model_station_extent(db.working)
        if extent is None or not extent[0] <= document.revision.start_station < document.revision.end_station <= extent[1]:
            raise ProjectRestoreError("Saved Revision Limits are outside the restored stationing extent.")
        db.header.revision_start_station = document.revision.start_station
        db.header.revision_end_station = document.revision.end_station
    else:
        db.header.revision_start_station = db.header.revision_end_station = None
    db.landmarks = [Landmark(id=a.annotation_id, distance=a.distance, elevation=a.elevation,
        text=a.text, orientation=a.orientation, underline=a.underline, adjusted=a.adjusted)
        for a in document.reviewed_annotations]
    db.next_landmark_id = max([item.id for item in db.landmarks], default=0) + 1
    db.panels = [PanelDef(**p.model_dump(exclude={"review_state"})) for p in document.panel_set.panels]
    validate_collection(db.panels, (document.revision.start_station, document.revision.end_station))
    mark_panels_review_required(db)
    evidence = document.panel_set.review_evidence
    verified_review = bool(not legacy_semantic and
        document.panel_set.review_state == "REVIEWED" and evidence
        and evidence.contract_version == REVIEW_CONTRACT_VERSION
        and evidence.source_sha256 == document.source.fingerprint.sha256
        and evidence.dependency_sha256 == panel_dependency_digest(db)
        and evidence.ordered_panel_definition_sha256 == ordered_panel_definition_digest(db.panels)
        and ctx.stationing_state == "FRESH")
    warnings: list[str] = []
    if legacy_semantic:
        warnings.append("Legacy project source identity was byte-verified but lacks a semantic manifest; panels require review.")
    if verified_review:
        review_panels(db)
    elif db.panels:
        warnings.append("Saved panels were restored as REVIEW_REQUIRED; review and Apply them before export.")
    session["project_id"] = document.project_id
    session["project_path"] = None
    session["project_dirty"] = False
    return warnings
