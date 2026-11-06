"""Operation-scoped compatibility assessment for a parsed HEC-RAS plan."""
from __future__ import annotations

from typing import Any

VALIDATED = "SUPPORTED_AND_VALIDATED"
LIMITED = "SUPPORTED_WITH_LIMITATIONS"
UNSUPPORTED = "UNSUPPORTED"


def _finding(feature: str, status: str, stages: list[str], reason: str,
             entity: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"feature": feature, "status": status, "affected_stages": stages,
            "entity": entity or {}, "reason": reason}


def assess_plan_compatibility(plan: Any, db: Any | None = None) -> list[dict[str, Any]]:
    """Describe demonstrated workflow support without promoting dataset presence."""
    findings: list[dict[str, Any]] = []
    all_stages = ["IMPORT", "EDITING", "PANEL_GENERATION", "DIAGNOSTIC_PREVIEW", "AUTHORITATIVE_EXPORT"]
    findings.append(_finding(
        "source identity and hydraulic association", VALIDATED, all_stages,
        "Geometry and hydraulic results were joined by verified composite identities using "
        f"{getattr(plan.capabilities, 'schema_adapter', 'UNKNOWN')}."))

    units = getattr(plan, "source_units", "UNKNOWN") or "UNKNOWN"
    conversion_complete = getattr(plan, "unit_conversion_status", "UNVERIFIED") == "COMPLETE"
    if units in {"US Customary", "SI"} and conversion_complete:
        findings.append(_finding("project units", VALIDATED, all_stages,
                                 ("US Customary source quantities use canonical working feet without conversion."
                                  if units == "US Customary" else
                                  "SI source quantities were converted once to canonical working feet under the recorded unit contract; source values remain in provenance.")))
    else:
        findings.append(_finding(
            "project units", UNSUPPORTED, ["EDITING", "PANEL_GENERATION", "AUTHORITATIVE_EXPORT"],
            f"Source units {units!r} lack a complete verified conversion contract."))

    for reach in plan.reaches:
        identity = {"river": reach.river, "reach": reach.reach, "source_id": reach.source_id}
        status = getattr(reach, "source_order_status", "UNKNOWN")
        if status == "SOURCE_UPSTREAM_FIRST":
            support = VALIDATED
            reason = "Source XS order is upstream-first and composite identities are unique."
        elif status == "SOURCE_DOWNSTREAM_FIRST_NORMALIZED":
            support = LIMITED
            reason = "Source XS rows were downstream-first and were deterministically normalized by numeric river station; result association remains identity-based."
        elif status in {"SOURCE_NONNUMERIC_UPSTREAM_FIRST_VERIFIED",
                        "SOURCE_NONNUMERIC_DOWNSTREAM_FIRST_NORMALIZED"}:
            support = LIMITED
            reason = ("Nonnumeric river-station labels were retained verbatim; source-row orientation "
                      "was verified from consistent numeric reaches in the same HDF and result association "
                      "remains identity-based.")
        else:
            support = LIMITED
            reason = "Source XS row order was nonmonotonic and was deterministically normalized by numeric river station; review stationing before authoritative use."
        findings.append(_finding("cross-section ordering", support,
                                 ["EDITING", "PANEL_GENERATION", "AUTHORITATIVE_EXPORT"],
                                 reason, identity))

    if len(plan.reaches) > 1 and db is None:
        findings.append(_finding(
            "multi-reach engineering context", LIMITED,
            ["EDITING", "PANEL_GENERATION", "DIAGNOSTIC_PREVIEW", "AUTHORITATIVE_EXPORT"],
            "Select one reach or build one source-validated connected flooding source before engineering operations."))
    else:
        findings.append(_finding("engineering context", VALIDATED,
                                 ["EDITING", "PANEL_GENERATION", "DIAGNOSTIC_PREVIEW"],
                                 "A canonical working engineering context is available."))

    missing_values = sum(
        value.get("state") in {"MISSING", "DRY", "NONFINITE", "PARTIAL_ENVELOPE"}
        for profile in plan.profiles for value in profile.values)
    if missing_values:
        findings.append(_finding(
            "hydraulic completeness", LIMITED, ["DIAGNOSTIC_PREVIEW", "AUTHORITATIVE_EXPORT"],
            f"{missing_values} hydraulic values are explicitly missing/dry/partial; gaps are preserved and not interpolated."))
    else:
        findings.append(_finding("hydraulic completeness", VALIDATED,
                                 ["DIAGNOSTIC_PREVIEW", "AUTHORITATIVE_EXPORT"],
                                 "Source hydraulic values are finite or explicitly represented."))

    datum = getattr(plan, "source_datum", "UNKNOWN") or "UNKNOWN"
    findings.append(_finding(
        "vertical datum", VALIDATED if datum.upper() != "UNKNOWN" else LIMITED,
        ["AUTHORITATIVE_EXPORT"],
        f"Source datum is {datum}." if datum.upper() != "UNKNOWN"
        else "Source datum is UNKNOWN; output must disclose UNKNOWN or require deliberate user confirmation."))

    if db is not None:
        from .structure_model import models_from_rows
        for model in models_from_rows(db.working):
            findings.append(_finding(
                "structure geometry", VALIDATED if model.status == "VALIDATED" else UNSUPPORTED,
                ["AUTHORITATIVE_EXPORT"], model.reason,
                {"identity": model.identity, "river": model.river, "reach": model.reach,
                 "river_station": model.rs, "structure_type": model.type}))
    elif any(reach.structures for reach in plan.reaches):
        findings.append(_finding(
            "structure geometry", LIMITED, ["AUTHORITATIVE_EXPORT"],
            "Structure inventory is present; authoritative support is assessed after selecting an engineering context."))
    else:
        findings.append(_finding("structure geometry", VALIDATED, ["AUTHORITATIVE_EXPORT"],
                                 "The verified source inventory contains no structures."))
    return findings


def apply_plan_compatibility(plan: Any, db: Any | None = None) -> list[dict[str, Any]]:
    """Publish one internally consistent report on the parsed plan."""
    if getattr(plan, "capabilities", None) is None:
        from .model_topology import HdfCapabilities
        plan.capabilities = HdfCapabilities(
            ras_version=getattr(plan, "ras_version", "UNKNOWN"),
            unit_system=getattr(plan, "source_units", "UNKNOWN"))
    findings = assess_plan_compatibility(plan, db)
    plan.capabilities.workflow_capabilities = findings
    if db is not None:
        from .structure_model import models_from_rows
        models = models_from_rows(db.working)
        for detected in plan.capabilities.structures_detected:
            source_type = str(detected.get("type", "")).upper().replace(" ", "_")
            matches = [model for model in models
                       if model.type.upper().replace(" ", "_") == source_type]
            detected["status"] = (
                "SUPPORTED" if matches and all(model.status == "VALIDATED" for model in matches)
                else "UNVALIDATED"
            )
            detected["reason"] = (
                "All normalized source records of this type are validated in the active engineering context."
                if detected["status"] == "SUPPORTED"
                else "One or more source records require context-specific geometry validation."
            )
    return findings
