import type { RevisionLimitsResponse, StructurePreflightResponse } from "../api/client";

type Props = { source: string; profiles: string[]; datum?: string; stationingDirty: boolean; panelsStale: boolean;
  revision: RevisionLimitsResponse | null; unsaved: boolean; error: string | null;
  structurePreflight?: StructurePreflightResponse | null; onOpenStructure?: (identity: string) => void;
  onOpenPanelSetup?: () => void;
  panelStaleReasons?: Array<{category: string; affected_panels: string[]; detail: string;
    changes?: Array<{identity: string; field: string; previous: unknown; current: unknown}>;
    additional_change_count?: number}> };
export default function EngineeringStatusPanel({ source, profiles, datum, stationingDirty, panelsStale, revision, unsaved, error, structurePreflight, onOpenStructure, onOpenPanelSetup, panelStaleReasons = [] }: Props) {
  const structureFindings = structurePreflight?.structures.filter(item => item.affects_range && item.status !== "VALIDATED") ?? [];
  const blocked = stationingDirty || panelsStale || Boolean(error) || structureFindings.length > 0;
  return <aside className={`engineering-status ${blocked ? "engineering-status--attention" : ""}`} aria-label="Engineering status">
    <div className="engineering-status__heading"><strong>Engineering status</strong><span className={`status-badge ${blocked ? "status-badge--warning" : "status-badge--neutral"}`}>{blocked ? "Attention required" : "Current state"}</span></div>
    <div className="engineering-status__items">
      <span title={source}><b>Source</b>{source || "No source"}</span><span><b>Profiles</b>{profiles.length ? profiles.join(", ") : "None"}</span>
      <span><b>Datum</b>{datum || "UNKNOWN"}</span><span><b>Revision</b>{revision ? `${revision.revision_start_station.toFixed(2)}–${revision.revision_end_station.toFixed(2)} ft` : "Loading"}</span>
      <span><b>Panels</b>{stationingDirty ? "CUMDIST recalculation required" : panelsStale ? "Review required" : "No pending review warning"}</span>
      <span><b>Project</b>{unsaved ? "Unsaved changes" : "Saved state"}</span>
    </div>
    {error && <details className="engineering-status__details" open><summary>Current error</summary><p>{error}</p></details>}
    {panelsStale && <details className="engineering-status__details" open><summary>Why panel review is required</summary>
      {panelStaleReasons.length ? <ul>{panelStaleReasons.map((reason, index) => <li key={`${reason.category}-${index}`}><strong>{reason.category}</strong>: {reason.detail} Affected saved panels: {reason.affected_panels.join(", ") || "all"}.
        {reason.changes?.length ? <ul className="panel-review-changes">{reason.changes.map((change, changeIndex) => <li key={`${change.identity}-${change.field}-${changeIndex}`}><code>{change.identity}</code> · {change.field}: <span>{String(change.previous ?? "missing")}</span> → <span>{String(change.current ?? "missing")}</span></li>)}</ul> : null}
        {reason.additional_change_count ? <small>{reason.additional_change_count} additional change(s) are recorded but not expanded here.</small> : null}
      </li>)}</ul>
        : <p>Engineering dependencies changed after review. Exact field attribution is unavailable; review all saved panels.</p>}
      {onOpenPanelSetup && <button type="button" onClick={onOpenPanelSetup}>Open Panel Setup to review</button>}
    </details>}
    {structureFindings.length > 0 && <details className="engineering-status__details structure-findings" open>
      <summary>Structure validation blocks engineering export ({structureFindings.length})</summary>
      <p>Resolve each source-geometry or identity issue before final PDF/DXF export. Diagnostic profile data remains available for review.</p>
      <ul>{structureFindings.map((item) => <li key={item.identity}>
        <strong>{item.river} / {item.reach} / RS {item.rs} / {item.type}</strong>
        <span>{item.missing_fields?.length ? ` Missing: ${item.missing_fields.join(", ")}.` : ""} {item.reason || item.message}</span>
        {onOpenStructure && <button type="button" onClick={() => onOpenStructure(item.identity)}>Open in profile table</button>}
      </li>)}</ul>
    </details>}
  </aside>;
}
