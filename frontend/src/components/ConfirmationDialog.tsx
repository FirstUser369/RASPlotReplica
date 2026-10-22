import { useEffect, useRef } from "react";

type Props = { open: boolean; title: string; message: string; confirmLabel?: string; onCancel: () => void; onConfirm: () => void };
export default function ConfirmationDialog({ open, title, message, confirmLabel = "Continue", onCancel, onConfirm }: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => confirmRef.current?.focus());
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);
  if (!open) return null;
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <section className="app-dialog app-dialog--confirm" role="alertdialog" aria-modal="true" aria-labelledby="confirmation-title" aria-describedby="confirmation-message">
      <header className="app-dialog__header"><div><span className="eyebrow">Confirmation required</span><h2 id="confirmation-title">{title}</h2></div></header>
      <div className="app-dialog__body"><p id="confirmation-message" className="dialog-description">{message}</p></div>
      <footer className="app-dialog__footer"><button type="button" className="button button--secondary" onClick={onCancel}>Cancel</button>
        <button ref={confirmRef} type="button" className="button button--primary" onClick={onConfirm}>{confirmLabel}</button></footer>
    </section></div>;
}
