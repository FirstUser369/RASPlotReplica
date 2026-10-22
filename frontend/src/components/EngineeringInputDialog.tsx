import { useEffect, useRef, useState } from "react";

type Props = {
  open: boolean;
  sessionKey: string;
  title: string;
  description: string;
  label: string;
  unit?: string;
  initialValue?: string;
  kind?: "number" | "text";
  validate?: (value: string) => string | null;
  onCancel: () => void;
  onApply: (value: string) => Promise<void> | void;
};

export default function EngineeringInputDialog({ open, sessionKey, title, description, label, unit,
  initialValue = "", kind = "text", validate, onCancel, onApply }: Props) {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const lifetime = useRef(0);

  useEffect(() => {
    lifetime.current += 1;
    if (open) { setValue(initialValue); setError(null); setBusy(false); queueMicrotask(() => inputRef.current?.focus()); }
    return () => { lifetime.current += 1; };
  }, [open, sessionKey, initialValue]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, busy, onCancel]);

  if (!open) return null;
  async function apply() {
    if (busy) return;
    const validation = validate?.(value) ?? null;
    if (validation) { setError(validation); return; }
    const token = lifetime.current;
    setBusy(true); setError(null);
    try { await onApply(value); if (token === lifetime.current) onCancel(); }
    catch (reason: any) { if (token === lifetime.current) setError(String(reason?.message ?? reason)); }
    finally { if (token === lifetime.current) setBusy(false); }
  }
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section className="app-dialog app-dialog--compact" role="dialog" aria-modal="true" aria-labelledby="engineering-input-title">
      <header className="app-dialog__header"><div><span className="eyebrow">Engineering input</span><h2 id="engineering-input-title">{title}</h2></div>
        <button className="icon-button" type="button" onClick={onCancel} disabled={busy} aria-label={`Close ${title}`}>×</button></header>
      <div className="app-dialog__body"><p className="dialog-description">{description}</p>
        <label className="field"><span>{label}{unit ? ` (${unit})` : ""}</span><input ref={inputRef} type={kind === "number" ? "text" : "text"}
          inputMode={kind === "number" ? "decimal" : "text"} value={value} onChange={event => { setValue(event.target.value); setError(null); }}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void apply(); } }} aria-invalid={Boolean(error)} /></label>
        {error && <div className="notice notice--error" role="alert">{error}</div>}</div>
      <footer className="app-dialog__footer"><button type="button" className="button button--secondary" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="button button--primary" onClick={() => void apply()} disabled={busy}>{busy ? "Applying…" : "Apply"}</button></footer>
    </section></div>;
}
