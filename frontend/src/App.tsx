import { useEffect, useState } from "react";
import Header from "./components/Header";
import ProfileChart from "./components/ProfileChart";
import ProfileTable from "./components/ProfileTable";
import MapView from "./components/MapView";
import ControlsRow from "./components/ControlsRow";
import ExportDialog from "./components/ExportDialog";
import ReachMergePanel from "./components/ReachMergePanel";
import LandmarksPanel from "./components/LandmarksPanel";
import ProfileMappingDialog from "./components/ProfileMappingDialog";
import PanelSetupDialog from "./components/PanelSetupDialog";
import RevisionLimits from "./components/RevisionLimits";
import { api, type GetProfileResponse, type ProfileMeta, type RevisionLimitsResponse } from "./api/client";

export type Session = {
  session_id: string;
  flow_type: string;
  name: string;
  reaches: Array<{ river: string; reach: string; cross_sections: any[]; structures: any[] }>;
  profiles: Array<{ name: string; wsel: number[] }>;
};

type ElevationBounds = { min: number; max: number };

function getProfileElevationBounds(profile: GetProfileResponse): ElevationBounds | null {
  const values: number[] = [];
  for (const row of profile.cross_sections ?? []) {
    const candidates = [row.wsel, row.minchel, ...Object.values(row.wsel_by_profile ?? {})];
    for (const value of candidates) {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) values.push(numeric);
    }
  }
  if (!values.length) return null;
  return { min: Math.min(...values), max: Math.max(...values) };
}

function safeElevationRange(bounds: ElevationBounds): ElevationBounds {
  if (bounds.min !== bounds.max) return bounds;
  const padding = Math.max(1, Math.abs(bounds.min) * 0.01);
  return { min: bounds.min - padding, max: bounds.max + padding };
}

function formatElevation(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "";
  return String(Number(value.toFixed(2)));
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([]);
  const [reachIdx, setReachIdx] = useState(0);
  const [profileMeta, setProfileMeta] = useState<ProfileMeta[]>([]);
  const [profileData, setProfileData] = useState<any>(null);
  const [startStation, setStartStation] = useState<number | null>(null);
  const [endStation, setEndStation] = useState<number | null>(null);
  const [availMinStation, setAvailMinStation] = useState<number | null>(null);
  const [availMaxStation, setAvailMaxStation] = useState<number | null>(null);
  const [startElevation, setStartElevation] = useState<number | null>(null);
  const [endElevation, setEndElevation] = useState<number | null>(null);
  const [availElevationBounds, setAvailElevationBounds] = useState<ElevationBounds | null>(null);
  const [startElevationInput, setStartElevationInput] = useState("");
  const [endElevationInput, setEndElevationInput] = useState("");
  const [elevationCustomized, setElevationCustomized] = useState(false);
  const [geojson, setGeojson] = useState<any>(null);
  const [selectedRs, setSelectedRs] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaultPath, setDefaultPath] = useState("");
  const [fisDefaults, setFisDefaults] = useState<Record<string, any>>({});
  const [exportOpen, setExportOpen] = useState(false);
  const [profileMappingOpen, setProfileMappingOpen] = useState(false);
  const [profileTableOpen, setProfileTableOpen] = useState(false);
  const [panelSetupOpen, setPanelSetupOpen] = useState(false);
  const [useNodeNames, setUseNodeNames] = useState(false);
  const [workingByRs, setWorkingByRs] = useState<Record<string, number>>({});
  const [landmarks, setLandmarks] = useState<any[]>([]);
  const [mergeOrder, setMergeOrder] = useState<number[]>([]);
  const [junctions, setJunctions] = useState<number[]>([]);
  const [sideTab, setSideTab] = useState<"table" | "landmarks" | "merge">("table");
  const [revisionRefreshKey, setRevisionRefreshKey] = useState(0);
  const [revisionLimits, setRevisionLimits] = useState<RevisionLimitsResponse | null>(null);

  useEffect(() => {
    api.getSettings().then((s) => {
      if (s.last_plan_path) setDefaultPath(s.last_plan_path);
      if (s.default_fis) setFisDefaults(s.default_fis);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const shouldLock = profileTableOpen || panelSetupOpen || profileMappingOpen;
    document.body.style.overflow = shouldLock ? "hidden" : previousOverflow;
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [profileTableOpen, panelSetupOpen, profileMappingOpen]);

  async function refreshDbMeta(sid: string) {
    try {
      const db = await api.getDb(sid);
      const map: Record<string, number> = {};
      for (const r of db.working || []) {
        if (r.kind === "xs") map[r.rs] = r.id;
      }
      setWorkingByRs(map);
      setLandmarks(db.landmarks || []);
      setProfileMeta((db.profiles as ProfileMeta[]) || []);
    } catch {
      /* ignore */
    }
  }

  async function loadProfile(sid: string, rIdx: number, profiles: string[], resetRange = false, resetElevation = false) {
    const p = await api.getProfile(sid, rIdx, profiles);
    setProfileData(p);

    const elevationBounds = getProfileElevationBounds(p);
    setAvailElevationBounds(elevationBounds);
    if (resetRange || resetElevation || !elevationCustomized) {
      const nextRange = elevationBounds ? safeElevationRange(elevationBounds) : null;
      setStartElevation(nextRange?.min ?? null);
      setEndElevation(nextRange?.max ?? null);
      setStartElevationInput(formatElevation(nextRange?.min ?? null));
      setEndElevationInput(formatElevation(nextRange?.max ?? null));
      setElevationCustomized(false);
    }

    // determine available cumulative distance extents for this reach
    try {
      const cds = (p.cross_sections || []).map((c: any) => Number(c.cumdist)).filter((v: number) => Number.isFinite(v));
      const min = cds.length ? Math.min(...cds) : 0;
      const max = cds.length ? Math.max(...cds) : 0;
      setAvailMinStation(min);
      setAvailMaxStation(max);

      if (resetRange || startStation == null || endStation == null) {
        setStartStation(min);
        setEndStation(max);
      } else {
        // clamp existing values to new extents but preserve if valid
        setStartStation((cur) => {
          if (cur == null) return min;
          if (cur < min) return min;
          if (cur >= max) return min;
          return cur;
        });
        setEndStation((cur) => {
          if (cur == null) return max;
          if (cur > max) return max;
          if (cur <= min) return max;
          return cur;
        });
      }
    } catch {
      /* ignore */
    }

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
      setElevationCustomized(false);
      setMergeOrder(s.reaches.map((_, i) => i));
      setJunctions(s.reaches.slice(1).map(() => 0));
      await loadProfile(s.session_id, 0, init, false, true);
      setRevisionRefreshKey((value) => value + 1);
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
      await loadProfile(session.session_id, reachIdx, names, false);
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
      await loadProfile(session.session_id, idx, selectedProfiles, true);
      setRevisionRefreshKey((value) => value + 1);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  function commitStartStation(raw: number | string) {
    if (availMinStation == null || availMaxStation == null) return;
    const v = Number(raw);
    if (!Number.isFinite(v)) {
      setError("Invalid start station");
      return;
    }
    if (v < availMinStation) {
      setStartStation(availMinStation);
      setError(null);
      return;
    }
    if (endStation != null && v >= endStation) {
      setError("Start must be less than End station");
      return;
    }
    setStartStation(v);
    setError(null);
  }

  async function onAutoEnd() {
    if (!session || availMinStation == null || availMaxStation == null) return;
    const v = Number(startStation);
    if (!Number.isFinite(v)) {
      setError("Invalid start station");
      return;
    }
    if (v < availMinStation) {
      setStartStation(availMinStation);
      setError(null);
      return;
    }
    if (v >= availMaxStation) {
      setError("Start station must be below the available profile maximum");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const result = await api.getAutoEnd(session.session_id, v);
      setEndStation(result.end_station);
    } catch (e: any) {
      setError(String(e.message ?? "Unable to calculate Auto End station"));
    } finally {
      setBusy(false);
    }
  }

  function commitEndStation(raw: number | string) {
    if (availMinStation == null || availMaxStation == null) return;
    const v = Number(raw);
    if (!Number.isFinite(v)) {
      setError("Invalid end station");
      return;
    }
    if (v > availMaxStation) {
      setEndStation(availMaxStation);
      setError(null);
      return;
    }
    if (startStation != null && v <= startStation) {
      setError("End must be greater than Start station");
      return;
    }
    setEndStation(v);
    setError(null);
  }

  function commitStartElevation(raw: number | string) {
    const text = String(raw);
    const v = Number(text);
    if (!text.trim() || !Number.isFinite(v)) {
      setStartElevationInput(formatElevation(startElevation));
      setError("Invalid start elevation");
      return;
    }
    if (endElevation != null && v >= endElevation) {
      setStartElevationInput(formatElevation(startElevation));
      setError("Start elevation must be less than End elevation");
      return;
    }
    setStartElevation(v);
    setStartElevationInput(formatElevation(v));
    setElevationCustomized(true);
    setError(null);
  }

  function commitEndElevation(raw: number | string) {
    const text = String(raw);
    const v = Number(text);
    if (!text.trim() || !Number.isFinite(v)) {
      setEndElevationInput(formatElevation(endElevation));
      setError("Invalid end elevation");
      return;
    }
    if (startElevation != null && v <= startElevation) {
      setEndElevationInput(formatElevation(endElevation));
      setError("End elevation must be greater than Start elevation");
      return;
    }
    setEndElevation(v);
    setEndElevationInput(formatElevation(v));
    setElevationCustomized(true);
    setError(null);
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

  async function saveProfileMapping(nextProfiles: ProfileMeta[]) {
    if (!session) return;
    setBusy(true);
    try {
      await api.putProfiles(session.session_id, nextProfiles.map((p) => ({
        name: p.name,
        frequency: p.frequency,
        backwater_elev: p.backwater_elev,
        backwater_text: p.backwater_text,
      })));
      await refreshDbMeta(session.session_id);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function editWorkingRowValue(
    rs: string,
    field: "length_chnl" | "minchel" | "low_chord" | "high_chord",
    value: number | null,
  ) {
    if (!session) return;
    const id = workingByRs[rs];
    if (id == null) {
      setError(`No working row found for RS ${rs}`);
      return;
    }
    setBusy(true);
    try {
      await api.patchWorking(session.session_id, [{ id, [field]: value }]);
      await reloadAfterDb();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function editProfileValue(rs: string, profileName: string, value: number | null) {
    if (!session) return;
    const id = workingByRs[rs];
    if (id == null) {
      setError(`No working row found for RS ${rs}`);
      return;
    }
    setBusy(true);
    try {
      await api.patchWorking(session.session_id, [{ id, profiles: { [profileName]: value } }]);
      await reloadAfterDb();
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
      await loadProfile(session.session_id, 0, selectedProfiles, false, true);
      setRevisionRefreshKey((value) => value + 1);
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
            onOpenProfileMapping={() => setProfileMappingOpen(true)}
            onOpenEditProfileTable={() => setProfileTableOpen(true)}
            onOpenPanelSetup={() => setPanelSetupOpen(true)}
            onFixDrawdowns={onFixDrawdowns}
            onAutoLetter={onAutoLetter}
            onRecalcCumdist={onRecalcCumdist}
            onNodeNames={onNodeNames}
            useNodeNames={useNodeNames}
            startStation={startStation}
            endStation={endStation}
            onStartChange={(v) => setStartStation(Number(v))}
            onEndChange={(v) => setEndStation(Number(v))}
            onStartCommit={commitStartStation}
            onEndCommit={commitEndStation}
            onAutoEnd={onAutoEnd}
            startElevationInput={startElevationInput}
            endElevationInput={endElevationInput}
            onStartElevationChange={setStartElevationInput}
            onEndElevationChange={setEndElevationInput}
            onStartElevationCommit={commitStartElevation}
            onEndElevationCommit={commitEndElevation}
          />
          <RevisionLimits
            sessionId={session.session_id}
            refreshKey={revisionRefreshKey}
            onChange={setRevisionLimits}
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
                    startStation={startStation}
                    endStation={endStation}
                    startElevation={startElevation}
                    endElevation={endElevation}
                    revisionStart={revisionLimits?.revision_start_station ?? null}
                    revisionEnd={revisionLimits?.revision_end_station ?? null}
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
                  onEditWorkingRow={editWorkingRowValue}
                  onEditProfileValue={editProfileValue}
                  workingByRs={workingByRs}
                  mode="compact"
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
          {profileTableOpen && profileData && (
            <div className="modal-backdrop" onClick={() => setProfileTableOpen(false)}>
              <div className="edit-table-modal" onClick={(e) => e.stopPropagation()}>
                <div className="edit-table-header">
                  <div>
                    <h3>Edit Profile Table</h3>
                  </div>
                  <button type="button" className="close-button" onClick={() => setProfileTableOpen(false)} aria-label="Close edit profile table">
                    ×
                  </button>
                </div>

                <div className="edit-table-toolbar">
                  <div className="edit-table-toolbar__row">
                    <span className="edit-table-meta">
                      {session.reaches[reachIdx]?.river} \ {session.reaches[reachIdx]?.reach} · {selectedProfiles.join(", ")}
                    </span>
                  </div>
                  <div className="edit-table-actions">
                    <button type="button" onClick={onFixDrawdowns}>Fix Drawdowns</button>
                    <button type="button" onClick={onAutoLetter}>Auto Letter</button>
                    <button type="button" onClick={onRecalcCumdist}>Recalculate CUMDIST</button>
                  </div>
                </div>

                <div className="edit-table-body">
                  <ProfileTable
                    profile={profileData}
                    selectedRs={selectedRs}
                    onSelectRs={setSelectedRs}
                    onEditLetter={editLetter}
                    onEditLabelText={editLabelText}
                    onEditWorkingRow={editWorkingRowValue}
                    onEditProfileValue={editProfileValue}
                    workingByRs={workingByRs}
                    mode="workspace"
                  />
                </div>

                <div className="edit-table-footer">
                  <span>{selectedRs ? `Selected RS: ${selectedRs}` : "No RS selected"}</span>
                  <button type="button" onClick={() => setProfileTableOpen(false)}>Done</button>
                </div>
              </div>
            </div>
          )}
          <ProfileMappingDialog
            open={profileMappingOpen}
            sessionId={session.session_id}
            profiles={profileMeta}
            onClose={() => setProfileMappingOpen(false)}
            onSave={saveProfileMapping}
          />
          <PanelSetupDialog
            open={panelSetupOpen}
            sessionId={session.session_id}
            profile={profileData}
            revisionStart={revisionLimits?.revision_start_station ?? null}
            revisionEnd={revisionLimits?.revision_end_station ?? null}
            onClose={() => setPanelSetupOpen(false)}
          />
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
