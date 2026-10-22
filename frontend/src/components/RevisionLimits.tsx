import { StaleResponse, contextVersion } from "../api/context";
import { useEffect, useState } from "react";
import { api, type RevisionLimitsResponse } from "../api/client";
import { displayRevisionBoundary as display, prepareRevisionSubmission, revisionDraft } from "../revisionLimits";

type Props = {
  sessionId: string;
  refreshKey?: number;
  onChange?: (limits: RevisionLimitsResponse) => void;
  onCommit?: () => void;
};

export default function RevisionLimits({ sessionId, refreshKey = 0, onChange, onCommit }: Props) {
  const [limits, setLimits] = useState<RevisionLimitsResponse | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [startEdited, setStartEdited] = useState(false);
  const [endEdited, setEndEdited] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    api.getRevisionLimits(sessionId)
      .then((response) => {
        if (cancelled) return;
        setLimits(response);
        onChange?.(response);
        const draft = revisionDraft(response);
        setStartInput(draft.startInput);
        setEndInput(draft.endInput);
        setStartEdited(false);
        setEndEdited(false);
        setStatus(response.review_required ? "Revision Limits require review: apply limits within the current model extent or Reset to Full Model." : response.explicit ? "Explicit limits loaded" : "Using full model extent");
      })
      .catch((reason: any) => {
        if (!cancelled) setError(String(reason.message ?? reason));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey, onChange]);

  async function apply() {
    const version = contextVersion();
    if (!limits) return;
    const submission = prepareRevisionSubmission(limits, startInput, endInput, startEdited, endEdited);
    if (!submission.ok) {
      setError(submission.error);
      return;
    }
    if (submission.unchanged) {
      setError(null);
      setStatus("Revision limits unchanged");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await api.putRevisionLimits(sessionId, submission.start, submission.end);
      setLimits(response);
      onChange?.(response);
      const draft = revisionDraft(response);
      setStartInput(draft.startInput);
      setEndInput(draft.endInput);
      setStartEdited(false);
      setEndEdited(false);
      setStatus("Revision limits saved");
      onCommit?.();
    } catch (reason: any) {
      if (reason instanceof StaleResponse) return;
      setError(String(reason.message ?? reason));
    } finally {
      if (version === contextVersion()) setBusy(false);
    }
  }

  async function reset() {
    const version = contextVersion();
    setBusy(true);
    setError(null);
    try {
      const response = await api.clearRevisionLimits(sessionId);
      setLimits(response);
      onChange?.(response);
      const draft = revisionDraft(response);
      setStartInput(draft.startInput);
      setEndInput(draft.endInput);
      setStartEdited(false);
      setEndEdited(false);
      setStatus("Using full model extent");
      onCommit?.();
    } catch (reason: any) {
      if (reason instanceof StaleResponse) return;
      setError(String(reason.message ?? reason));
    } finally {
      if (version === contextVersion()) setBusy(false);
    }
  }

  return (
    <section aria-label="Revision Limits" className={`revision-limits ${expanded ? "is-expanded" : ""}`}>
      <div className="revision-limits__summary">
        <div><span className="eyebrow">Engineering extent</span><strong>Revision Limits</strong></div>
        {limits && (
          <>
            <span>Model Range: {display(limits.model_start_station)} - {display(limits.model_end_station)} ft</span>
            <strong className={limits.explicit ? "revision-limits__active" : ""}>
              Active Revision: {display(limits.revision_start_station)} - {display(limits.revision_end_station)} ft
            </strong>
          </>
        )}<button type="button" className="button button--quiet" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? "Collapse" : "Edit limits"}</button>
      </div>
      {expanded && <div className="revision-limits__editor">
        <label>
          <span>Start Station (ft)</span>
          <input type="number" value={startInput} onChange={(event) => { setStartInput(event.target.value); setStartEdited(true); }} disabled={busy} />
        </label>
        <label>
          <span>End Station (ft)</span>
          <input type="number" value={endInput} onChange={(event) => { setEndInput(event.target.value); setEndEdited(true); }} disabled={busy} />
        </label>
        <button type="button" onClick={apply} disabled={busy || !limits}>Apply</button>
        <button type="button" onClick={reset} disabled={busy || !limits || !limits.explicit}>Reset to Full Model</button>
      </div>}
      <div className={error ? "revision-limits__message is-error" : "revision-limits__message"} role={error ? "alert" : "status"}>
        {error || status}
      </div>
    </section>
  );
}
