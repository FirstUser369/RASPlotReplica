import { useEffect, useMemo, useRef, useState } from "react";
import { contextVersion, StaleResponse } from "../api/context";

export type ProfileMetaRow = {
  name: string;
  profile_id?: string;
  result_type?: string;
  frequency: string;
  backwater_elev: number | null;
  backwater_text: string;
  selected?: boolean;
  color?: string;
  linetype?: string;
  lineweight?: number;
  legend_text?: string;
  layer?: string;
};

const frequencyOptions: Array<{ value: string; label: string }> = [
  { value: "", label: "UNKNOWN / Unmapped" },
  { value: "1%", label: "1%-annual-chance" },
  { value: "1%+", label: "1%+" },
  { value: "0.2%", label: "0.2%-annual-chance" },
  { value: "2%", label: "2%-annual-chance" },
  { value: "4%", label: "4%-annual-chance" },
  { value: "10%", label: "10%-annual-chance" },
  { value: "20%", label: "20%-annual-chance" },
  { value: "50%", label: "50%-annual-chance" },
  { value: "1% Future", label: "1% Future" },
  { value: "Floodway", label: "Floodway" },
];

function toDisplayValue(value: string): string {
  if (!value) return "UNKNOWN / Unmapped";
  const match = frequencyOptions.find((opt) => opt.value === value);
  return match ? match.label : value;
}

function toStoredValue(value: string): string {
  const match = frequencyOptions.find((opt) => opt.label === value);
  return match ? match.value : value;
}

type Props = {
  open: boolean;
  sessionId: string;
  profiles: ProfileMetaRow[];
  onClose: () => void;
  onSave: (profiles: ProfileMetaRow[]) => Promise<void> | void;
};

export default function ProfileMappingDialog({
  open,
  sessionId,
  profiles,
  onClose,
  onSave,
}: Props) {
  const [drafts, setDrafts] = useState<ProfileMetaRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lifetime = useRef(0);
  useEffect(() => {
    lifetime.current++;
    setSaving(false); setError(null);
    return () => { lifetime.current++; };
  }, [open, sessionId]);

  useEffect(() => {
    if (!open) return;
    setDrafts(profiles.map((p) => ({ ...p, backwater_text: p.backwater_text ?? "" })));
  }, [open, profiles]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape" && !saving) onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, saving, onClose]);

  const rows = useMemo(() => drafts, [drafts]);

  if (!open) return null;

  function updateRow(index: number, patch: Partial<ProfileMetaRow>) {
    setDrafts((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function handleSave() {
    const version = contextVersion();
    const token = lifetime.current;
    if (saving) return;
    setError(null);
    setSaving(true);
    try {
      await onSave(rows);
      if (version === contextVersion() && token === lifetime.current) onClose();
    } catch (e: any) {
      if (!(e instanceof StaleResponse) && version === contextVersion() && token === lifetime.current) setError(String(e.message ?? e));
    } finally {
      if (version === contextVersion() && token === lifetime.current) setSaving(false);
    }
  }

  return (
    <div className="profile-mapping-backdrop" onClick={() => { if (!saving) onClose(); }}>
      <div className={`profile-mapping-modal ${rows.length <= 1 ? "profile-mapping-modal--compact" : ""}`} role="dialog" aria-modal="true" aria-labelledby="profile-mapping-title" onClick={(e) => e.stopPropagation()}>
        <header className="profile-mapping-header">
          <div><span className="eyebrow">Profile metadata</span><h2 id="profile-mapping-title">Profile Mapping</h2></div>
          <button className="profile-mapping-close" type="button" onClick={onClose} aria-label="Close profile mapping">×</button>
        </header>

        <div className="profile-mapping-context">
          <span>Map each source profile deliberately. Frequencies are never inferred from profile names.</span>
          <span className="status-badge status-badge--neutral">{rows.length} profile{rows.length === 1 ? "" : "s"}</span>
        </div>

        <div className="profile-mapping-body">
          <table className="profile-mapping-table">
            <colgroup>
              <col style={{ width: "22%" }} />
              <col style={{ width: "28%" }} />
              <col style={{ width: "22%" }} />
              <col style={{ width: "28%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>HEC-RAS Profile</th>
                <th>FEMA Frequency / Use</th>
                <th>Backwater Elevation (user reference)</th>
                <th>Backwater Text</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.name}-${index}`} className={!row.frequency ? "mapping-row--unmapped" : "mapping-row--mapped"}>
                  <td title={row.profile_id}>{row.name}{rows.filter(p => p.name === row.name).length > 1 ? ` [${row.profile_id}]` : ""}{row.result_type === "DERIVED_MAXIMUM_ENVELOPE" ? " (derived saved-timestep envelope)" : ""}</td>
                  <td>
                    <select
                      disabled={row.result_type === "DERIVED_MAXIMUM_ENVELOPE"}
                      aria-label={`Frequency or use for ${row.name}`}
                      value={toDisplayValue(row.frequency ?? "")}
                      onChange={(e) => {
                        const next = toStoredValue(e.target.value);
                        updateRow(index, { frequency: next });
                      }}
                    >
                      {frequencyOptions.map((opt) => (
                        <option key={`${row.name}-${opt.value || "unmapped"}`} value={opt.label}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      aria-label={`Backwater elevation for ${row.name}`}
                      value={row.backwater_elev ?? ""}
                      onChange={(e) => {
                        const v = e.target.value;
                        updateRow(index, {
                          backwater_elev: v === "" ? null : Number(v),
                        });
                      }}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`Backwater text for ${row.name}`}
                      value={row.backwater_text ?? ""}
                      onChange={(e) => updateRow(index, { backwater_text: e.target.value })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <footer className="profile-mapping-footer">
          {error && <span role="alert">{error}</span>}
          <button type="button" className="profile-mapping-secondary" onClick={onClose}>Cancel</button>
          <button type="button" disabled={saving} onClick={handleSave}>
            {saving ? "Saving…" : "Save / Apply"}
          </button>
        </footer>
      </div>
    </div>
  );
}
