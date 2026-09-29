import { useEffect, useState } from "react";
import { api, type RevisionLimitsResponse } from "../api/client";

type Props = {
  sessionId: string;
  refreshKey?: number;
  onChange?: (limits: RevisionLimitsResponse) => void;
};

function display(value: number) {
  return Number.isFinite(value) ? String(Number(value.toFixed(2))) : "";
}

export default function RevisionLimits({ sessionId, refreshKey = 0, onChange }: Props) {
  const [limits, setLimits] = useState<RevisionLimitsResponse | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    api.getRevisionLimits(sessionId)
      .then((response) => {
        if (cancelled) return;
        setLimits(response);
        onChange?.(response);
        setStartInput(display(response.revision_start_station));
        setEndInput(display(response.revision_end_station));
        setStatus(response.explicit ? "Explicit limits loaded" : "Using full model extent");
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
    if (!limits) return;
    const start = Number(startInput);
    const end = Number(endInput);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      setError("Revision limits must be numeric.");
      return;
    }
    if (start >= end) {
      setError("Revision start station must be less than end station.");
      return;
    }
    if (start < limits.model_start_station || end > limits.model_end_station) {
      setError("Revision limits must fall within the model extent.");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await api.putRevisionLimits(sessionId, start, end);
      setLimits(response);
      onChange?.(response);
      setStartInput(display(response.revision_start_station));
      setEndInput(display(response.revision_end_station));
      setStatus("Revision limits saved");
    } catch (reason: any) {
      setError(String(reason.message ?? reason));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      const response = await api.clearRevisionLimits(sessionId);
      setLimits(response);
      onChange?.(response);
      setStartInput(display(response.revision_start_station));
      setEndInput(display(response.revision_end_station));
      setStatus("Using full model extent");
    } catch (reason: any) {
      setError(String(reason.message ?? reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="Revision Limits"
      style={{
        margin: "0.75rem 1rem 0",
        padding: "0.75rem 1rem",
        border: "1px solid #cbd5e1",
        borderLeft: "4px solid #196bd6",
        background: "#f8fafc",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "1rem", flexWrap: "wrap" }}>
        <strong>Revision Limits</strong>
        {limits && (
          <>
            <span>Model Range: {display(limits.model_start_station)} - {display(limits.model_end_station)} ft</span>
            <strong style={{ color: limits.explicit ? "#196bd6" : "#475569" }}>
              Active Revision: {display(limits.revision_start_station)} - {display(limits.revision_end_station)} ft
            </strong>
          </>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "end", gap: "0.75rem", flexWrap: "wrap", marginTop: "0.5rem" }}>
        <label>
          <span style={{ display: "block" }}>Start Station</span>
          <input type="number" value={startInput} onChange={(event) => setStartInput(event.target.value)} disabled={busy} />
        </label>
        <label>
          <span style={{ display: "block" }}>End Station</span>
          <input type="number" value={endInput} onChange={(event) => setEndInput(event.target.value)} disabled={busy} />
        </label>
        <button type="button" onClick={apply} disabled={busy || !limits}>Apply</button>
        <button type="button" onClick={reset} disabled={busy || !limits || !limits.explicit}>Reset to Full Model</button>
      </div>
      <div style={{ minHeight: "1.25rem", marginTop: "0.35rem", color: error ? "#b91c1c" : "#475569" }}>
        {error || status}
      </div>
    </section>
  );
}
