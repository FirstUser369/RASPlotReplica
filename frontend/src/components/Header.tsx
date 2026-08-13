import { useEffect, useState } from "react";

type Props = {
  onOpen: (path: string) => void;
  busy: boolean;
  session: any;
  error: string | null;
  initialPath?: string;
};

export default function Header({ onOpen, busy, session, error, initialPath = "" }: Props) {
  const [path, setPath] = useState(initialPath);

  useEffect(() => {
    if (initialPath) setPath(initialPath);
  }, [initialPath]);

  return (
    <header className="header">
      <h1>RAS-Plot Replica</h1>
      <div className="open-bar">
        <input
          id="plan-path"
          type="text"
          placeholder="C:\path\to\Muncie\Muncie.p04.hdf"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && path) onOpen(path);
          }}
        />
        <button disabled={busy || !path} onClick={() => onOpen(path)}>
          {busy ? "Loading…" : "Open"}
        </button>
      </div>
      <div className="session">
        {session && (
          <>
            <span className="badge">{session.flow_type}</span>
            <span>{session.name}</span>
            <span>•</span>
            <span>{session.reaches?.length ?? 0} reach(es)</span>
            <span>•</span>
            <span>{session.profiles?.length ?? 0} profile(s)</span>
          </>
        )}
      </div>
      {error && (
        <div style={{ color: "#ff8c8c", fontSize: 12, marginLeft: 8 }} title={error}>
          {error.length > 80 ? error.slice(0, 80) + "…" : error}
        </div>
      )}
    </header>
  );
}
