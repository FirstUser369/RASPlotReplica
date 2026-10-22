import { useEffect, useState } from "react";
import { api } from "../api/client";

type Props = {
  onOpen: (path: string) => void;
  onOpenProject: () => void;
  onSaveProject: (saveAs: boolean) => void;
  busy: boolean;
  session: any;
  error: string | null;
  initialPath?: string;
  projectPath: string | null;
  unsaved: boolean;
};

export default function Header({ onOpen, onOpenProject, onSaveProject, busy, session, error,
  initialPath = "", projectPath, unsaved }: Props) {
  const [path, setPath] = useState(initialPath);
  const [browsing, setBrowsing] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);

  async function browse() {
    if (browsing || busy) return;
    setBrowsing(true); setBrowseError(null);
    try {
      const result = await api.browseSource();
      if (result.path) setPath(result.path);
    } catch (e: any) {
      setBrowseError(String(e.message ?? e));
    } finally { setBrowsing(false); }
  }

  useEffect(() => {
    if (initialPath) setPath(initialPath);
  }, [initialPath]);

  return (
    <header className="header">
      <div className="brand"><span className="brand__mark" aria-hidden="true">RP</span><div><h1>RAS-Plot</h1><small>Engineering profile workspace</small></div></div>
      <div className="project-command-bar"><div className="open-bar">
        <label htmlFor="plan-path">HEC-RAS Plan HDF</label>
        <input
          id="plan-path"
          type="text"
          placeholder="Select a local HEC-RAS plan HDF"
          disabled={busy || browsing}
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && path && !busy && !browsing) onOpen(path);
          }}
        />
        <button className="button button--secondary" disabled={busy || browsing} onClick={browse}>{browsing ? "Browsing…" : "Browse…"}</button>
        <button className="button button--primary" disabled={busy || browsing || !path} onClick={() => onOpen(path)}>
          {busy ? "Loading…" : "Open"}
        </button>
        <button className="button button--secondary" disabled={busy || browsing} onClick={onOpenProject}>Open Project…</button>
        <button className="button button--secondary" disabled={busy || !session} onClick={() => onSaveProject(false)}>Save</button>
        <button className="button button--quiet" disabled={busy || !session} onClick={() => onSaveProject(true)}>Save As…</button>
      </div></div>
      <div className="session">
        {session && (
          <>
            <span className="badge">{session.flow_type}</span>
            <span className="session-name" title={session.name}>{session.name}</span>
            <span>•</span>
            <span>{session.reaches?.length ?? 0} reach(es)</span>
            <span>•</span>
            <span>{session.profiles?.length ?? 0} profile(s)</span>
          </>
        )}
      </div>
      {session && <div className={`project-save-status ${unsaved ? "is-dirty" : ""}`} title={projectPath ?? ""}>
        {unsaved ? "● Unsaved changes" : projectPath ? "Project saved" : "Not saved as project"}
      </div>}
      {(error || browseError) && (
        <div className="header-error" role="alert">
          {error || browseError}
        </div>
      )}
    </header>
  );
}
