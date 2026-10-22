import type { RevisionLimitsResponse } from "../api/client";

type Props = { source: string; profiles: string[]; datum?: string; stationingDirty: boolean; panelsStale: boolean;
  revision: RevisionLimitsResponse | null; unsaved: boolean; error: string | null };
export default function EngineeringStatusPanel({ source, profiles, datum, stationingDirty, panelsStale, revision, unsaved, error }: Props) {
  const blocked = stationingDirty || panelsStale || Boolean(error);
  return <aside className={`engineering-status ${blocked ? "engineering-status--attention" : ""}`} aria-label="Engineering status">
    <div className="engineering-status__heading"><strong>Engineering status</strong><span className={`status-badge ${blocked ? "status-badge--warning" : "status-badge--neutral"}`}>{blocked ? "Attention required" : "Current state"}</span></div>
    <div className="engineering-status__items">
      <span title={source}><b>Source</b>{source || "No source"}</span><span><b>Profiles</b>{profiles.length ? profiles.join(", ") : "None"}</span>
      <span><b>Datum</b>{datum || "UNKNOWN"}</span><span><b>Revision</b>{revision ? `${revision.revision_start_station.toFixed(2)}–${revision.revision_end_station.toFixed(2)} ft` : "Loading"}</span>
      <span><b>Panels</b>{stationingDirty ? "CUMDIST recalculation required" : panelsStale ? "Review required" : "No pending review warning"}</span>
      <span><b>Project</b>{unsaved ? "Unsaved changes" : "Saved state"}</span>
    </div>
    {error && <details className="engineering-status__details" open><summary>Current error</summary><p>{error}</p></details>}
  </aside>;
}
