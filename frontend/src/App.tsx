import { advanceContext, assertContext, contextModel, contextVersion, setContextModel, setContextReach, StaleResponse } from "./api/context";
import { finiteElevation } from "./engineering";
import { useEffect, useRef, useState } from "react";
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
import EngineeringActionsDialog, { type EngineeringAction } from "./components/EngineeringActionsDialog";
import EngineeringInputDialog from "./components/EngineeringInputDialog";
import ConfirmationDialog from "./components/ConfirmationDialog";
import EngineeringStatusPanel from "./components/EngineeringStatusPanel";
import RevisionLimits from "./components/RevisionLimits";
import ModelWorkspace from "./components/ModelWorkspace";
import { api, type FloodingSourceResponse, type GetProfileResponse, type PanelInput, type ProfileMeta, type RevisionLimitsResponse } from "./api/client";
import type {ModelInventory} from "./modelWorkspace";
import {crossSectionByIdentity, workingRowsByIdentity} from "./xsIdentity";
import {activeReachContext, engineeringStateFromDb, mergeWorkingRows, profileTableVisible} from "./workflowState";

export type Session = {
  session_id: string;
  flow_type: string;
  name: string;
  reaches: Array<{ river: string; reach: string; cross_sections: any[]; structures: any[] }>;
  profiles: Array<{ name: string; profile_id: string; display_name?: string; wsel: (number | null)[] }>;
};

type ElevationBounds = { min: number; max: number };
type ModelContext = { generation: number; modelId: string; session: Session };

function getProfileElevationBounds(profile: GetProfileResponse): ElevationBounds | null {
  const values: number[] = [];
  for (const row of profile.cross_sections ?? []) {
    const candidates = [row.wsel, row.minchel, ...Object.values(row.wsel_by_profile ?? {})];
    for (const value of candidates) {
      if (finiteElevation(value)) values.push(value);
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
  const [modelContext, setModelContext] = useState<ModelContext | null>(null);
  const session = modelContext?.session ?? null;
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([]);
  const [reachIdx, setReachIdx] = useState(0);
  const [profileMeta, setProfileMeta] = useState<ProfileMeta[]>([]);
  const [profileData, setProfileData] = useState<any>(null);
  const [workingTableData, setWorkingTableData] = useState<GetProfileResponse | null>(null);
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
  const [selectedXsId, setSelectedXsId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaultPath, setDefaultPath] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [profileMappingOpen, setProfileMappingOpen] = useState(false);
  const [profileTableOpen, setProfileTableOpen] = useState(false);
  const [panelSetupOpen, setPanelSetupOpen] = useState(false);
  const [useNodeNames, setUseNodeNames] = useState(false);
  const [workingByIdentity, setWorkingByIdentity] = useState<Record<string, number>>({});
  const [landmarks, setLandmarks] = useState<any[]>([]);
  const [mergeOrder, setMergeOrder] = useState<number[]>([]);
  const [junctions, setJunctions] = useState<number[]>([]);
  const [sideTab, setSideTab] = useState<"table" | "landmarks" | "merge">("table");
  const [revisionRefreshKey, setRevisionRefreshKey] = useState(0);
  const [revisionLimits, setRevisionLimits] = useState<RevisionLimitsResponse | null>(null);
  const [modelInventory, setModelInventory] = useState<ModelInventory | null>(null);
  const [workspaceMode, setWorkspaceMode] = useState<"model" | "engineering">("engineering");
  const [stationingDirty, setStationingDirty] = useState(false);
  const [stationingNotice, setStationingNotice] = useState<string | null>(null);
  const [panelsStale, setPanelsStale] = useState(false);
  const [savedPanels, setSavedPanels] = useState<PanelInput[]>([]);
  const [activeFloodingSourceName, setActiveFloodingSourceName] = useState<string | null>(null);
  const [projectPath, setProjectPath] = useState<string | null>(null);
  const [unsavedProject, setUnsavedProject] = useState(false);
  const [engineeringAction, setEngineeringAction] = useState<EngineeringAction | null>(null);
  const [engineeringInput, setEngineeringInput] = useState<null | {kind: "cumdist"; initial: string} | {kind: "landmark"; distance: number; elevation: number}>(null);
  const [confirmation, setConfirmation] = useState<null | {title: string; message: string; confirmLabel: string}>(null);
  const confirmationResolver = useRef<((confirmed: boolean) => void) | null>(null);
  const [supportPaneOpen, setSupportPaneOpen] = useState(true);

  useEffect(() => {
    api.getSettings().then((s) => {
      if (s.last_plan_path) setDefaultPath(s.last_plan_path);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const changed = () => { if (session) setUnsavedProject(true); };
    const unload = (event: BeforeUnloadEvent) => {
      if (!unsavedProject) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("rasplot:committed-mutation", changed);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("rasplot:committed-mutation", changed);
      window.removeEventListener("beforeunload", unload);
    };
  }, [session, unsavedProject]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const shouldLock = profileTableOpen || panelSetupOpen || profileMappingOpen || engineeringAction != null || engineeringInput != null || confirmation != null;
    document.body.style.overflow = shouldLock ? "hidden" : previousOverflow;
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [profileTableOpen, panelSetupOpen, profileMappingOpen, engineeringAction, engineeringInput, confirmation]);

  useEffect(() => {
    setEngineeringAction(null);
    setEngineeringInput(null);
    if (confirmationResolver.current) confirmationResolver.current(false);
    confirmationResolver.current = null;
    setConfirmation(null);
  }, [session?.session_id]);

  useEffect(() => {
    if (!profileTableOpen) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") setProfileTableOpen(false); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [profileTableOpen]);

  function askConfirmation(title: string, message: string, confirmLabel = "Continue") {
    if (confirmationResolver.current) confirmationResolver.current(false);
    return new Promise<boolean>((resolve) => {
      confirmationResolver.current = resolve;
      setConfirmation({title, message, confirmLabel});
    });
  }

  function resolveConfirmation(confirmed: boolean) {
    const resolve = confirmationResolver.current;
    confirmationResolver.current = null;
    setConfirmation(null);
    resolve?.(confirmed);
  }

  async function allowProjectSwitch() {
    if (!unsavedProject) return true;
    return askConfirmation("Discard unsaved project changes?", "Opening another source or project will discard unsaved changes in the current workspace.", "Discard and continue");
  }

  async function refreshDbMeta(sid: string) {
    let requestVersion = contextVersion();
    try {
      const db = await api.getDb(sid);
      setWorkingByIdentity(workingRowsByIdentity(db.working || []));
      setLandmarks(db.landmarks || []);
      setProfileMeta((db.profiles as ProfileMeta[]) || []);
      setSavedPanels((db.panels as PanelInput[]) || []);
      return db;
    } catch (e) {
      throw e;
    }
  }

  async function loadProfile(sid: string, rIdx: number, profiles: string[], resetRange = false, resetElevation = false) {
    let requestVersion = contextVersion();
    const p = await api.getProfile(sid, rIdx, profiles);
    setProfileData(p);
    setWorkingTableData(p);

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
    if (!await allowProjectSwitch()) return;
    const previousSession = session;
    const previousModel = contextModel();
    const previousReach = reachIdx;
    const requestVersion = advanceContext();
    setError(null);
    setBusy(true);
    let candidate: Session | null = null;
    try {
      const s = await api.openSource(path, requestVersion) as Session & {model_id?: string; model_context?: {model_id: string}; requires_reach_selection?: boolean};
      candidate = s;
      const candidateModel = s.model_context?.model_id ?? s.model_id ?? s.session_id;
      setContextModel(candidateModel);
      setContextReach(0);
      const first = s.profiles[0]?.profile_id ?? "";
      const init = first ? [first] : [];
      if (s.requires_reach_selection) {
        const inventory = await api.getModelInventory(s.session_id) as ModelInventory;
        await api.commitSession(s.session_id);
        assertContext(requestVersion);
        setModelContext({generation: requestVersion, modelId: candidateModel, session: s});
        setModelInventory(inventory); setWorkspaceMode("model"); setDefaultPath(path);
        setSelectedProfiles(init); setReachIdx(0); setProfileData(null); setWorkingTableData(null); setGeojson(null);
        setWorkingByIdentity({}); setProfileMeta([]); setLandmarks([]); setSelectedXsId(null);
        setRevisionLimits(null); setProfileTableOpen(false); setPanelSetupOpen(false);
        setExportOpen(false); setProfileMappingOpen(false); setError(null);
        setStationingDirty(false); setStationingNotice(null); setPanelsStale(false); setSavedPanels([]); setActiveFloodingSourceName(null);
        setRevisionRefreshKey(value => value + 1);
        setProjectPath(null); setUnsavedProject(false);
        if (previousSession) void api.closeSession(previousSession.session_id).catch(() => {});
        return;
      }
      const [profile, db, map] = await Promise.all([
        api.getProfile(s.session_id, 0, init),
        api.getDb(s.session_id),
        api.getMap(s.session_id),
      ]);
      await api.commitSession(s.session_id);
      assertContext(requestVersion);

      const elevationBounds = getProfileElevationBounds(profile);
      const elevationRange = elevationBounds ? safeElevationRange(elevationBounds) : null;
      const cds = (profile.cross_sections || []).map((c: any) => Number(c.cumdist)).filter((v: number) => Number.isFinite(v));
      const min = cds.length ? Math.min(...cds) : 0;
      const max = cds.length ? Math.max(...cds) : 0;
      const workingMap = workingRowsByIdentity(db.working || []);

      // Commit the completely initialized candidate as one React update batch.
      setModelContext({generation: requestVersion, modelId: candidateModel, session: s});
      setModelInventory(null); setWorkspaceMode("engineering");
      setDefaultPath(path); setSelectedProfiles(init); setReachIdx(0);
      setProfileData(profile); setWorkingTableData(profile); setGeojson(map); setWorkingByIdentity(workingMap);
      setProfileMeta((db.profiles as ProfileMeta[]) || []); setLandmarks(db.landmarks || []);
      setSavedPanels((db.panels as PanelInput[]) || []);
      setStartStation(min); setEndStation(max); setAvailMinStation(min); setAvailMaxStation(max);
      setAvailElevationBounds(elevationBounds); setStartElevation(elevationRange?.min ?? null); setEndElevation(elevationRange?.max ?? null);
      setStartElevationInput(formatElevation(elevationRange?.min ?? null)); setEndElevationInput(formatElevation(elevationRange?.max ?? null));
      setSelectedXsId(null); setUseNodeNames(false); setElevationCustomized(false);
      setMergeOrder(s.reaches.map((_, i) => i)); setJunctions(s.reaches.slice(1).map(() => 0));
      setRevisionLimits(null); setProfileTableOpen(false); setPanelSetupOpen(false);
      setExportOpen(false); setProfileMappingOpen(false);
      setStationingDirty(false); setStationingNotice(null); setPanelsStale(Boolean(db.panels_stale)); setActiveFloodingSourceName(null);
      setRevisionRefreshKey((value) => value + 1);
      setProjectPath(null); setUnsavedProject(false);
      if (previousSession) void api.closeSession(previousSession.session_id).catch(() => {});
    } catch (e: any) {
      if (candidate) void api.abandonSession(candidate.session_id).catch(() => {});
      if (requestVersion !== contextVersion()) return;
      setContextModel(previousModel); setContextReach(previousReach);
      if (e instanceof StaleResponse) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function openSavedProject() {
    if (!await allowProjectSwitch()) return;
    let selected: {path: string | null};
    try { selected = await api.browseProjectOpen(); }
    catch (e: any) { setError(String(e.message ?? e)); return; }
    if (!selected.path) return;
    const previousSession = session;
    const previousModel = contextModel();
    const previousReach = reachIdx;
    const requestVersion = advanceContext();
    setBusy(true); setError(null);
    let candidate: Session | null = null;
    try {
      let opened;
      try {
        opened = await api.openProject(selected.path, null, requestVersion);
      } catch (first: any) {
        let sourceStatus = "";
        try { sourceStatus = JSON.parse(String(first.message ?? first)).source_status ?? ""; } catch { /* plain error */ }
        if (!sourceStatus || !await askConfirmation("Locate the saved project's HDF source?", "The saved HDF source could not be verified. Select the original matching HDF to continue safely.", "Locate HDF")) throw first;
        const replacement = await api.browseSource();
        if (!replacement.path) return;
        opened = await api.openProject(selected.path, replacement.path, requestVersion);
      }
      const s = opened as Session & {
        model_id?: string; model_context?: {model_id: string; source_path?: string}; db?: any;
        project_path: string; warnings?: string[];
      };
      candidate = s;
      const candidateModel = s.model_context?.model_id ?? s.model_id ?? s.session_id;
      setContextModel(candidateModel); setContextReach(0);
      const db = s.db;
      if (!db) throw new Error("The saved project did not restore an engineering workspace.");
      const init = (db.profiles ?? []).filter((p: any) => p.selected).map((p: any) => p.profile_id);
      const chosen = init.length ? init : (s.profiles[0]?.profile_id ? [s.profiles[0].profile_id] : []);
      const [profile, map] = await Promise.all([
        api.getProfile(s.session_id, 0, chosen), api.getMap(s.session_id),
      ]);
      assertContext(requestVersion);
      const elevationBounds = getProfileElevationBounds(profile);
      const elevationRange = elevationBounds ? safeElevationRange(elevationBounds) : null;
      const cds = profile.cross_sections.map(item => Number(item.cumdist)).filter(Number.isFinite);
      const min = cds.length ? Math.min(...cds) : 0;
      const max = cds.length ? Math.max(...cds) : 0;
      setModelContext({generation: requestVersion, modelId: candidateModel, session: s});
      setModelInventory(null); setWorkspaceMode("engineering"); setDefaultPath(s.model_context?.source_path ?? "");
      setSelectedProfiles(chosen); setReachIdx(0); setProfileData(profile); setWorkingTableData(profile); setGeojson(map);
      setWorkingByIdentity(workingRowsByIdentity(db.working ?? [])); setProfileMeta(db.profiles ?? []);
      setLandmarks(db.landmarks ?? []); setSavedPanels(db.panels ?? []);
      setStartStation(min); setEndStation(max); setAvailMinStation(min); setAvailMaxStation(max);
      setAvailElevationBounds(elevationBounds); setStartElevation(elevationRange?.min ?? null); setEndElevation(elevationRange?.max ?? null);
      setStartElevationInput(formatElevation(elevationRange?.min ?? null)); setEndElevationInput(formatElevation(elevationRange?.max ?? null));
      setSelectedXsId(null); setUseNodeNames(Boolean(db.use_node_names)); setElevationCustomized(false);
      setRevisionLimits(null); setProfileTableOpen(false); setPanelSetupOpen(false); setExportOpen(false); setProfileMappingOpen(false);
      setStationingDirty(Boolean(db.stationing_dirty)); setStationingNotice(db.stationing_dirty ? "Recalculate CUMDIST before preview or export." : null);
      setPanelsStale(Boolean(db.panels_stale)); setActiveFloodingSourceName(db.flooding_source?.display_name ?? null);
      setProjectPath(s.project_path); setUnsavedProject(false); setRevisionRefreshKey(value => value + 1);
      if (s.warnings?.length) setError(s.warnings.join(" "));
      if (previousSession) void api.closeSession(previousSession.session_id).catch(() => {});
    } catch (e: any) {
      if (candidate) void api.abandonSession(candidate.session_id).catch(() => {});
      if (requestVersion !== contextVersion() || e instanceof StaleResponse) return;
      setContextModel(previousModel); setContextReach(previousReach);
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function saveProject(saveAs: boolean) {
    if (!session) return;
    setBusy(true); setError(null);
    try {
      let destination = projectPath;
      let pickerConfirmedDestination = false;
      if (saveAs || !destination) {
        const chosen = await api.browseProjectSave();
        if (!chosen.path) return;
        destination = chosen.path;
        pickerConfirmedDestination = true;
      }
      const result = await api.saveProject(session.session_id, destination,
        pickerConfirmedDestination || Boolean(projectPath && destination === projectPath));
      setProjectPath(result.path); setUnsavedProject(false);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally { setBusy(false); }
  }

  async function enterBuiltProfile(result: FloodingSourceResponse) {
    if (!session || !result.db) return;
    const requestVersion = contextVersion();
    const init = session.profiles[0]?.profile_id ? [session.profiles[0].profile_id] : [];
    const [profile, map] = await Promise.all([api.getProfile(session.session_id, 0, init), api.getMap(session.session_id)]);
    assertContext(requestVersion);
    const bounds = getProfileElevationBounds(profile);
    const range = bounds ? safeElevationRange(bounds) : null;
    const distances = profile.cross_sections.map(item => Number(item.cumdist)).filter(Number.isFinite);
    const min = distances.length ? Math.min(...distances) : 0;
    const max = distances.length ? Math.max(...distances) : 0;
    const workingMap = workingRowsByIdentity(result.db.working ?? []);
    setSelectedProfiles(init); setProfileData(profile); setWorkingTableData(profile); setGeojson(map); setProfileMeta(result.db.profiles ?? []);
    setLandmarks(result.db.landmarks ?? []); setWorkingByIdentity(workingMap); setReachIdx(0);
    setAvailMinStation(min); setAvailMaxStation(max); setStartStation(min); setEndStation(max);
    setAvailElevationBounds(bounds); setStartElevation(range?.min ?? null); setEndElevation(range?.max ?? null);
    setStartElevationInput(formatElevation(range?.min ?? null)); setEndElevationInput(formatElevation(range?.max ?? null));
    setStationingDirty(false); setStationingNotice(null); setPanelsStale(Boolean(result.db.panels_stale));
    setSavedPanels((result.db.panels as PanelInput[]) || []);
    setActiveFloodingSourceName(result.flooding_source.display_name);
    setWorkspaceMode("engineering"); setRevisionRefreshKey(value => value + 1);
  }

  async function changeProfiles(names: string[]) {
    let requestVersion = contextVersion();
    if (!session || !names.length) return;
    requestVersion = advanceContext();
    setProfileData(null);
    setSelectedProfiles(names);
    setBusy(true);
    try {
      await api.putProfiles(session.session_id, session.profiles.map(p => ({profile_id:p.profile_id, selected:names.includes(p.profile_id)})));
      await loadProfile(session.session_id, reachIdx, names, false);
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function changeReach(idx: number) {
    let requestVersion = contextVersion();
    if (!session) return;
    requestVersion = advanceContext();
    setProfileData(null); setWorkingTableData(null); setWorkingByIdentity({}); setRevisionLimits(null); setProfileTableOpen(false);
    setPanelSetupOpen(false); setExportOpen(false); setProfileMappingOpen(false);
    setContextReach(idx);
    setReachIdx(idx);
    setBusy(true);
    let reachCommitted = false;
    try {
      await api.selectReach(session.session_id, { reach_idx: idx });
      reachCommitted = true;
      await loadProfile(session.session_id, idx, selectedProfiles, true);
      setRevisionRefreshKey((value) => value + 1);
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      if (!reachCommitted) { setContextReach(reachIdx); setReachIdx(reachIdx); }
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
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
    let requestVersion = contextVersion();
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
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? "Unable to calculate Auto End station"));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
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

  function onAutoEndElevation() {
    if (!profileData) return;
    const bounds = getProfileElevationBounds(profileData);
    if (!bounds) {
      setError("No current profile elevations are available");
      return;
    }
    const nextRange = safeElevationRange(bounds);
    setEndElevation(nextRange.max);
    setEndElevationInput(formatElevation(nextRange.max));
    setElevationCustomized(false);
    setError(null);
  }

  async function reloadAfterDb() {
    if (!session) return;
    const db = await refreshDbMeta(session.session_id);
    const next = engineeringStateFromDb(db, error);
    setStationingDirty(next.stationingDirty);
    setStationingNotice(next.notice);
    setPanelsStale(next.panelsStale);
    setError(current => engineeringStateFromDb(db, current).error);
    setProfileData((current: GetProfileResponse | null) => mergeWorkingRows(current, db));
    setWorkingTableData(current => mergeWorkingRows(current, db));
    if (next.stationingDirty) {
      return;
    }
    await loadProfile(session.session_id, reachIdx, selectedProfiles);
  }

  async function recalcCumdist(start: number) {
    let requestVersion = contextVersion();
    if (!session) return;
    setBusy(true);
    try {
      await api.recalcCumdist(session.session_id, start);
      await reloadAfterDb();
      setRevisionRefreshKey((value) => value + 1);
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
      throw e;
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  function onRecalcCumdist() {
    if (!session) return;
    setEngineeringInput({kind: "cumdist", initial: "0"});
  }

  async function onNodeNames(on: boolean) {
    let requestVersion = contextVersion();
    if (!session) return;
    setUseNodeNames(on);
    setBusy(true);
    try {
      await api.nodeNames(session.session_id, on);
      await reloadAfterDb();
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function editLetter(xsId: string, letter: string) {
    let requestVersion = contextVersion();
    if (!session) return;
    const id = workingByIdentity[xsId];
    if (id == null) return;
    try {
      await api.patchWorking(session.session_id, [{ id, xs_id: xsId, label_letter: letter }]);
      await reloadAfterDb();
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    }
  }

  async function editLabelText(xsId: string, text: string) {
    let requestVersion = contextVersion();
    if (!session) return;
    const id = workingByIdentity[xsId];
    if (id == null) return;
    try {
      await api.patchWorking(session.session_id, [{ id, xs_id: xsId, label_text: text }]);
      await reloadAfterDb();
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    }
  }

  function addLandmark(distance: number, elevation: number) {
    setEngineeringInput({kind: "landmark", distance, elevation});
  }

  async function saveLandmarks() {
    let requestVersion = contextVersion();
    if (!session) return;
    setBusy(true);
    try {
      await api.putLandmarks(session.session_id, landmarks);
      await refreshDbMeta(session.session_id);
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function saveProfileMapping(nextProfiles: ProfileMeta[]) {
    let requestVersion = contextVersion();
    if (!session) return;
    setBusy(true);
    try {
      await api.putProfiles(session.session_id, nextProfiles.map((p) => ({
        name: p.name,
        profile_id: p.profile_id,
        frequency: p.frequency,
        backwater_elev: p.backwater_elev,
        backwater_text: p.backwater_text,
      })));
      await refreshDbMeta(session.session_id);
      await loadProfile(session.session_id, reachIdx, selectedProfiles);
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) throw e;
      setError(String(e.message ?? e));
      throw e;
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function editWorkingRowValue(
    xsId: string,
    field: "length_chnl" | "minchel" | "low_chord" | "high_chord",
    value: number | null,
  ) {
    let requestVersion = contextVersion();
    if (!session) return;
    const id = workingByIdentity[xsId];
    if (id == null) {
      setError("The selected cross section is no longer present. Reload the profile before editing.");
      return;
    }
    setBusy(true);
    try {
      await api.patchWorking(session.session_id, [{ id, xs_id: xsId, [field]: value }]);
      await reloadAfterDb();
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function editProfileValue(xsId: string, profileName: string, value: number | null) {
    let requestVersion = contextVersion();
    if (!session) return;
    const id = workingByIdentity[xsId];
    if (id == null) {
      setError("The selected cross section is no longer present. Reload the profile before editing.");
      return;
    }
    setBusy(true);
    try {
      await api.patchWorking(session.session_id, [{ id, xs_id: xsId, profiles: { [profileName]: value } }]);
      await reloadAfterDb();
    } catch (e: any) {
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  async function applyMerge() {
    let requestVersion = contextVersion();
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
      if (e instanceof StaleResponse || requestVersion !== contextVersion()) return;
      setError(String(e.message ?? e));
    } finally {
      if (requestVersion === contextVersion()) setBusy(false);
    }
  }

  return (
    <div className="app-grid">
      <Header
        onOpen={openPlan}
        onOpenProject={openSavedProject}
        onSaveProject={saveProject}
        busy={busy}
        session={session}
        error={error}
        initialPath={defaultPath}
        projectPath={projectPath}
        unsaved={unsavedProject}
      />
      {!session ? (
        <div className="empty">
          Open a HEC-RAS Plan (*.pXX.hdf) file using the bar above to begin.
        </div>
      ) : workspaceMode === "model" && modelInventory ? (
        <ModelWorkspace key={modelInventory.model_context.model_id} sessionId={session.session_id} generation={modelContext!.generation} inventory={modelInventory} onBuild={enterBuiltProfile} />
      ) : (
        <>
          <ControlsRow
            session={session}
            profileNames={session.profiles.map((p) => p.profile_id)}
            selectedProfiles={selectedProfiles}
            onProfilesChange={changeProfiles}
            reachIdx={reachIdx}
            onReachChange={changeReach}
            onOpenExport={() => setExportOpen(true)}
            onOpenProfileMapping={() => setProfileMappingOpen(true)}
            onOpenEditProfileTable={() => setProfileTableOpen(true)}
            onOpenPanelSetup={() => setPanelSetupOpen(true)}
            onFixDrawdowns={() => setEngineeringAction("fix-drawdowns")}
            onAutoLetter={() => setEngineeringAction("auto-letters")}
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
            onAutoEndElevation={onAutoEndElevation}
            activeFloodingSourceName={activeFloodingSourceName}
          />
          <RevisionLimits key={`revision:${session.session_id}:${reachIdx}`}
            sessionId={session.session_id}
            refreshKey={revisionRefreshKey}
            onChange={setRevisionLimits}
            onCommit={() => { void refreshDbMeta(session.session_id).then((db) => setPanelsStale(Boolean(db.panels_stale))).catch(() => {}); }}
          />
          <EngineeringStatusPanel source={defaultPath} profiles={selectedProfiles} datum={profileData?.vertical_datum}
            stationingDirty={stationingDirty} panelsStale={panelsStale} revision={revisionLimits} unsaved={unsavedProject} error={error} />
          <div className={`main ${supportPaneOpen ? "" : "main--chart-only"}`}>
            <div className="main__chart" aria-label="Flood profile drawing workspace">
              <div className="workspace-heading"><div><span className="eyebrow">Flood profile workspace</span><strong>{activeFloodingSourceName ?? activeReachContext(activeFloodingSourceName, session, reachIdx).label}</strong></div>
                <button type="button" className="button button--secondary" onClick={() => setSupportPaneOpen(value => !value)} aria-expanded={supportPaneOpen}>{supportPaneOpen ? "Hide supporting panes" : "Show supporting panes"}</button></div>
              {stationingDirty ? (
                <div className="stationing-blocked" role="status"><strong>Profile preview paused</strong><p>{stationingNotice}</p><button type="button" onClick={onRecalcCumdist}>Recalculate CUMDIST</button></div>
              ) : profileData && (
                <ProfileChart
                  profile={profileData}
                  selectedXsId={selectedXsId}
                  onSelectXs={setSelectedXsId}
                  landmarks={landmarks}
                  onAddLandmark={addLandmark}
                    startStation={startStation}
                    endStation={endStation}
                    startElevation={startElevation}
                    endElevation={endElevation}
                    revisionStart={revisionLimits?.revision_start_station ?? null}
                    revisionEnd={revisionLimits?.revision_end_station ?? null}
                    panels={savedPanels}
                    panelsStale={panelsStale}
                />
              )}
              {!stationingDirty && panelsStale && <div className="panel-review-notice">Saved panels require review after the stationing change.</div>}
              <div className="chart-hint">Shift+click a point to add a landmark</div>
            </div>
            {supportPaneOpen && <aside className="main__side" aria-label="Supporting engineering panes">
              <div className="pane-heading"><strong>Supporting data</strong><span>Map and working records</span></div>
              <div className="main__map">
                {geojson && <MapView geojson={geojson} onClickXs={setSelectedXsId} />}
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
                  selectedXsId={selectedXsId}
                  onSelectXs={setSelectedXsId}
                  onEditLetter={editLetter}
                  onEditLabelText={editLabelText}
                  onEditWorkingRow={editWorkingRowValue}
                  onEditProfileValue={editProfileValue}
                  workingByIdentity={workingByIdentity}
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
            </aside>}
          </div>
          {profileTableVisible(profileTableOpen, workingTableData) && workingTableData && (
            <div className="modal-backdrop" onClick={() => setProfileTableOpen(false)}>
              <div className="edit-table-modal" role="dialog" aria-modal="true" aria-labelledby="edit-table-title" onClick={(e) => e.stopPropagation()}>
                <div className="edit-table-header">
                  <div>
                    <h3 id="edit-table-title">Edit Profile Table</h3>
                  </div>
                  <button type="button" className="close-button" onClick={() => setProfileTableOpen(false)} aria-label="Close edit profile table">
                    ×
                  </button>
                </div>

                <div className="edit-table-toolbar">
                  <div className="edit-table-toolbar__row">
                    <span className="edit-table-meta">
                      {activeReachContext(activeFloodingSourceName, session, reachIdx).label} · {selectedProfiles.join(", ")}
                    </span>
                  </div>
                  <div className="edit-table-actions">
                    <button type="button" onClick={() => setEngineeringAction("fix-drawdowns")}>Fix Drawdowns</button>
                    <button type="button" onClick={() => setEngineeringAction("auto-letters")}>Auto Letters</button>
                    <button type="button" onClick={onRecalcCumdist}>Recalculate CUMDIST</button>
                  </div>
                </div>

                <div className="edit-table-body">
                  <ProfileTable
                    profile={workingTableData}
                    selectedXsId={selectedXsId}
                    onSelectXs={setSelectedXsId}
                    onEditLetter={editLetter}
                    onEditLabelText={editLabelText}
                    onEditWorkingRow={editWorkingRowValue}
                    onEditProfileValue={editProfileValue}
                    workingByIdentity={workingByIdentity}
                    mode="workspace"
                  />
                </div>

                <div className="edit-table-footer">
                  <span>{selectedXsId ? `Selected XS: ${crossSectionByIdentity<any>(workingTableData.cross_sections, selectedXsId)?.rs ?? "structure"}` : "No XS selected"}</span>
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
          <EngineeringActionsDialog
            key={`engineering-action:${session.session_id}`}
            action={engineeringAction}
            sessionId={session.session_id}
            onClose={() => setEngineeringAction(null)}
            onSuccess={reloadAfterDb}
          />
          <EngineeringInputDialog key={`engineering-input:${session.session_id}:${engineeringInput?.kind ?? "closed"}`}
            open={engineeringInput != null} sessionKey={session.session_id}
            title={engineeringInput?.kind === "cumdist" ? "Recalculate CUMDIST" : "Add Profile Landmark"}
            description={engineeringInput?.kind === "cumdist" ? "Recalculate continuous stationing from the selected starting distance. Existing engineering safeguards remain active." : "Add a user-authored annotation at the selected profile coordinate."}
            label={engineeringInput?.kind === "cumdist" ? "Starting cumulative distance" : "Landmark text"}
            unit={engineeringInput?.kind === "cumdist" ? "ft" : undefined} kind={engineeringInput?.kind === "cumdist" ? "number" : "text"}
            initialValue={engineeringInput?.kind === "cumdist" ? engineeringInput.initial : ""}
            validate={(value) => engineeringInput?.kind === "cumdist"
              ? (!value.trim() || !Number.isFinite(Number(value)) ? "Starting CUMDIST must be a finite number." : null)
              : (!value.trim() ? "Landmark text is required." : null)}
            onCancel={() => setEngineeringInput(null)}
            onApply={async value => {
              if (engineeringInput?.kind === "cumdist") await recalcCumdist(Number(value));
              else if (engineeringInput?.kind === "landmark") {
                setLandmarks(prev => [...prev, {distance: engineeringInput.distance, elevation: engineeringInput.elevation, text: value.trim(), orientation: "Vertical"}]);
                setSideTab("landmarks");
              }
            }} />
          <PanelSetupDialog key={`panels:${session?.session_id}:${reachIdx}`}
            open={panelSetupOpen}
            sessionId={session.session_id}
            profile={profileData}
            revisionStart={revisionLimits?.revision_start_station ?? null}
            revisionEnd={revisionLimits?.revision_end_station ?? null}
            onClose={() => setPanelSetupOpen(false)}
            onApplied={(panels) => { setSavedPanels(panels); setPanelsStale(false); }}
            stationingDirty={stationingDirty}
          />
          <ExportDialog
            key={`export:${session.session_id}:${reachIdx}`}
            open={exportOpen}
            onClose={() => setExportOpen(false)}
            sessionId={session.session_id}
            selectedProfiles={selectedProfiles}
            onBusy={setBusy}
            onError={setError}
          />
        </>
      )}
      <div className="footer">
        RAS-Plot Replica v0.2 • HEC-RAS HDF → FIS profile PDF/DXF (FDT/FIRM disabled pending validation)
      </div>
      <ConfirmationDialog open={confirmation != null} title={confirmation?.title ?? "Confirm"} message={confirmation?.message ?? ""}
        confirmLabel={confirmation?.confirmLabel} onCancel={() => resolveConfirmation(false)} onConfirm={() => resolveConfirmation(true)} />
    </div>
  );
}
