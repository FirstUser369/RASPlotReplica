import { useEffect, useState } from "react";
import Header from "./components/Header";
import ProfileChart from "./components/ProfileChart";
import ProfileTable from "./components/ProfileTable";
import MapView from "./components/MapView";
import ControlsRow from "./components/ControlsRow";
import ExportDialog from "./components/ExportDialog";
import ReachMergePanel from "./components/ReachMergePanel";
import LandmarksPanel from "./components/LandmarksPanel";
import { api } from "./api/client";

export type Session = {
  session_id: string;
  flow_type: string;
  name: string;
  reaches: Array<{ river: string; reach: string; cross_sections: any[]; structures: any[] }>;
  profiles: Array<{ name: string; wsel: number[] }>;
};

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([]);
  const [reachIdx, setReachIdx] = useState(0);
  const [profileData, setProfileData] = useState<any>(null);
  const [geojson, setGeojson] = useState<any>(null);
  const [selectedRs, setSelectedRs] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaultPath, setDefaultPath] = useState("");
  const [fisDefaults, setFisDefaults] = useState<Record<string, any>>({});
  const [exportOpen, setExportOpen] = useState(false);
  const [useNodeNames, setUseNodeNames] = useState(false);
  const [workingByRs, setWorkingByRs] = useState<Record<string, number>>({});
  const [landmarks, setLandmarks] = useState<any[]>([]);
  const [mergeOrder, setMergeOrder] = useState<number[]>([]);
  const [junctions, setJunctions] = useState<number[]>([]);
  const [sideTab, setSideTab] = useState<"table" | "landmarks" | "merge">("table");

  useEffect(() => {
    api.getSettings().then((s) => {
      if (s.last_plan_path) setDefaultPath(s.last_plan_path);
      if (s.default_fis) setFisDefaults(s.default_fis);
    }).catch(() => {});
  }, []);

  async function refreshDbMeta(sid: string) {
    try {
      const db = await api.getDb(sid);
      const map: Record<string, number> = {};
      for (const r of db.working || []) {
        if (r.kind === "xs") map[r.rs] = r.id;
      }
      setWorkingByRs(map);
      setLandmarks(db.landmarks || []);
    } catch {
      /* ignore */
    }
  }

  async function loadProfile(sid: string, rIdx: number, profiles: string[]) {
    const p = await api.getProfile(sid, rIdx, profiles);
    setProfileData(p);
    await refreshDbMeta(sid);
  }

  async function openPlan(path: string) {
    setError(null);
    setBusy(true);
    try {
      const s: Session = await api.openSource(path);
      setSession(s);
      setDefaultPath(path);
      const first = s.profiles[0]?.name ?? "";
      const init = first ? [first] : [];
      setSelectedProfiles(init);
      setReachIdx(0);
      setUseNodeNames(false);
      setMergeOrder(s.reaches.map((_, i) => i));
      setJunctions(s.reaches.slice(1).map(() => 0));
      await loadProfile(s.session_id, 0, init);
      setGeojson(await api.getMap(s.session_id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function changeProfiles(names: string[]) {
    if (!session || !names.length) return;
    setSelectedProfiles(names);
    try {
      await loadProfile(session.session_id, reachIdx, names);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function changeReach(idx: number) {
    if (!session) return;
    setReachIdx(idx);
    setBusy(true);
    try {
      await api.selectReach(session.session_id, { reach_idx: idx });
      await loadProfile(session.session_id, idx, selectedProfiles);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function reloadAfterDb() {
    if (!session) return;
    await loadProfile(session.session_id, reachIdx, selectedProfiles);
  }

  async function onFixDrawdowns() {
    if (!session) return;
    setBusy(true);
    try {
      await api.fixDrawdowns(session.session_id);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function onAutoLetter() {
    if (!session) return;
    setBusy(true);
    try {
      await api.autoLetter(session.session_id, 5);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function onRecalcCumdist() {
    if (!session) return;
    const raw = window.prompt("Starting cumulative distance (ft):", "0");
    if (raw == null) return;
    const start = parseFloat(raw) || 0;
    setBusy(true);
    try {
      await api.recalcCumdist(session.session_id, start);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function onNodeNames(on: boolean) {
    if (!session) return;
    setUseNodeNames(on);
    setBusy(true);
    try {
      await api.nodeNames(session.session_id, on);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function editLetter(rs: string, letter: string) {
    if (!session) return;
    const id = workingByRs[rs];
    if (id == null) return;
    try {
      await api.patchWorking(session.session_id, [{ id, label_letter: letter }]);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function editLabelText(rs: string, text: string) {
    if (!session) return;
    const id = workingByRs[rs];
    if (id == null) return;
    try {
      await api.patchWorking(session.session_id, [{ id, label_text: text }]);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  function addLandmark(distance: number, elevation: number) {
    const text = window.prompt("Landmark text:", "") ?? "";
    if (!text.trim()) return;
    setLandmarks((prev) => [
      ...prev,
      { distance, elevation, text: text.trim(), orientation: "Vertical" },
    ]);
    setSideTab("landmarks");
  }

  async function saveLandmarks() {
    if (!session) return;
    setBusy(true);
    try {
      await api.putLandmarks(session.session_id, landmarks);
      await refreshDbMeta(session.session_id);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function applyMerge() {
    if (!session) return;
    setBusy(true);
    try {
      await api.selectReach(session.session_id, {
        order: mergeOrder,
        junction_distances: junctions,
        start_cumdist: 0,
      });
      // after multi-merge, profile endpoint still wants reach_idx 0 of synthetic
      setReachIdx(0);
      await loadProfile(session.session_id, 0, selectedProfiles);
      setGeojson(await api.getMap(session.session_id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-grid">
      <Header
        onOpen={openPlan}
        busy={busy}
        session={session}
        error={error}
        initialPath={defaultPath}
      />
      {!session ? (
        <div className="empty">
          Open a HEC-RAS Plan (*.pXX.hdf) file using the bar above to begin.
        </div>
      ) : (
        <>
          <ControlsRow
            session={session}
            profileNames={session.profiles.map((p) => p.name)}
            selectedProfiles={selectedProfiles}
            onProfilesChange={changeProfiles}
            reachIdx={reachIdx}
            onReachChange={changeReach}
            onOpenExport={() => setExportOpen(true)}
            onFixDrawdowns={onFixDrawdowns}
            onAutoLetter={onAutoLetter}
            onRecalcCumdist={onRecalcCumdist}
            onNodeNames={onNodeNames}
            useNodeNames={useNodeNames}
          />
          <div className="main">
            <div className="main__chart">
              {profileData && (
                <ProfileChart
                  profile={profileData}
                  selectedRs={selectedRs}
                  onSelectRs={setSelectedRs}
                  landmarks={landmarks}
                  onAddLandmark={addLandmark}
                />
              )}
              <div className="chart-hint">Shift+click a point to add a landmark</div>
            </div>
            <div className="main__side">
              <div className="main__map">
                {geojson && <MapView geojson={geojson} onClickRs={setSelectedRs} />}
              </div>
              <div className="side-tabs">
                <button
                  type="button"
                  className={sideTab === "table" ? "active" : ""}
                  onClick={() => setSideTab("table")}
                >
                  Table
                </button>
                <button
                  type="button"
                  className={sideTab === "landmarks" ? "active" : ""}
                  onClick={() => setSideTab("landmarks")}
                >
                  Landmarks
                </button>
                {session.reaches.length > 1 && (
                  <button
                    type="button"
                    className={sideTab === "merge" ? "active" : ""}
                    onClick={() => setSideTab("merge")}
                  >
                    Reaches
                  </button>
                )}
              </div>
              {sideTab === "table" && profileData && (
                <ProfileTable
                  profile={profileData}
                  selectedRs={selectedRs}
                  onSelectRs={setSelectedRs}
                  onEditLetter={editLetter}
                  onEditLabelText={editLabelText}
                />
              )}
              {sideTab === "landmarks" && (
                <LandmarksPanel
                  landmarks={landmarks}
                  onChange={setLandmarks}
                  onSave={saveLandmarks}
                />
              )}
              {sideTab === "merge" && (
                <ReachMergePanel
                  reaches={session.reaches}
                  order={mergeOrder}
                  junctions={junctions}
                  onOrderChange={setMergeOrder}
                  onJunctionsChange={setJunctions}
                  onApply={applyMerge}
                />
              )}
            </div>
          </div>
          <ExportDialog
            open={exportOpen}
            onClose={() => setExportOpen(false)}
            sessionId={session.session_id}
            selectedProfiles={selectedProfiles}
            defaults={fisDefaults}
            onBusy={setBusy}
            onError={setError}
          />
        </>
      )}
      <div className="footer">
        RAS-Plot Replica v0.2 • HEC-RAS HDF → FIS profile PDF/DXF, FDT, FIRM shapefiles
      </div>
    </div>
  );
}
