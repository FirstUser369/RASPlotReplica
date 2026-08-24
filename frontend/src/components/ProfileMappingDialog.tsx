import { useEffect, useMemo, useState } from "react";

export type ProfileMetaRow = {
  name: string;
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
  { value: "", label: "Unmapped / Not Used" },
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
  if (!value) return "Unmapped / Not Used";
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

  useEffect(() => {
    if (!open) return;
    setDrafts(profiles.map((p) => ({ ...p, backwater_text: p.backwater_text ?? "" })));
  }, [open, profiles]);

  const rows = useMemo(() => drafts, [drafts]);

  if (!open) return null;

  function updateRow(index: number, patch: Partial<ProfileMetaRow>) {
    setDrafts((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onSave(rows);
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="profile-mapping-backdrop" onClick={onClose}>
      <div className="profile-mapping-modal" onClick={(e) => e.stopPropagation()}>
        <header className="profile-mapping-header">
          <h2>Profile Mapping</h2>
          <button className="profile-mapping-close" type="button" onClick={onClose} aria-label="Close profile mapping">×</button>
        </header>

        <div className="profile-mapping-context">
          <span>{rows.length} profile mappings</span>
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
                <th>Backwater Elevation</th>
                <th>Backwater Text</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.name}-${index}`}>
                  <td>{row.name}</td>
                  <td>
                    <select
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
          <button type="button" className="profile-mapping-secondary" onClick={onClose}>Cancel</button>
          <button type="button" disabled={saving} onClick={handleSave}>
            {saving ? "Saving…" : "Save / Apply"}
          </button>
        </footer>
      </div>
    </div>
  );
}
