import { profileSegments } from "../engineering";
import { StaleResponse } from "../api/context";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type GetProfileResponse, type PanelContentAnalysis, type PanelContentAnalysisResponse } from "../api/client";
import { panelRelationships, resizePanelEdge, translatePanel, type PanelResizeEdge } from "../panelVisualization";
import {
  createPanelNumericInputs,
  formatPanelDraftNumber,
  materializePanelNumericInputs,
  panelNumericInputKey,
  parseFinitePanelInput,
  synchronizePanelNumericInputs,
  type PanelNumericField,
  type PanelNumericInputs,
} from "../panelDraft";

type DraftPanel = {
  panel: number;
  panel_suffix?: string;
  display_id?: string;
  start_station: number;
  end_station: number;
  start_elevation: number;
  end_elevation: number;
  panel_x?: number | null;
  panel_y?: number | null;
  h_scale?: number;
  v_scale?: number;
};

const PAGE_DIMENSIONS: Record<string, [number, number]> = {
  "11x17": [17, 11], ANSI_A: [11, 8.5], ANSI_B: [17, 11], ANSI_C: [22, 17],
  ANSI_D: [34, 22], A3: [16.54, 11.69], A1: [33.11, 23.39], LETTER: [11, 8.5], TABLOID: [17, 11],
};

function displayTicks(min: number, max: number, preferredStep: number, snap: boolean) {
  if (!snap || !Number.isFinite(preferredStep) || preferredStep <= 0) {
    return Array.from({ length: 5 }, (_, index) => min + (index / 4) * (max - min));
  }
  const rough = Math.max(preferredStep, (max - min) / 8);
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const step = (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
  const values: number[] = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) values.push(value);
  return values.length >= 2 ? values : [min, max];
}

type Props = {
  open: boolean;
  sessionId: string;
  profile: GetProfileResponse | null;
  revisionStart?: number | null;
  revisionEnd?: number | null;
  onClose: () => void;
  onApplied?: (panels: DraftPanel[]) => void;
  stationingDirty?: boolean;
};

export default function PanelSetupDialog({
  open, sessionId, profile, revisionStart = null, revisionEnd = null, onClose, onApplied, stationingDirty = false,
}: Props) {
  const [draftPanels, setDraftPanels] = useState<DraftPanel[]>([]);
  const [numericInputs, setNumericInputs] = useState<PanelNumericInputs>({});
  const [hScale, setHScale] = useState(500);
  const [vScale, setVScale] = useState(5);
  const [hScaleInput, setHScaleInput] = useState("500");
  const [vScaleInput, setVScaleInput] = useState("5");
  const [pageSize, setPageSize] = useState("11x17");
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [fitVersion, setFitVersion] = useState(0);
  const [previewMode, setPreviewMode] = useState<"overview" | "selected">("overview");
  const [selectedPanelIndex, setSelectedPanelIndex] = useState<number | null>(null);
  const [excludedPanels, setExcludedPanels] = useState<DraftPanel[]>([]);
  const [snapPreviewGrid, setSnapPreviewGrid] = useState(true);
  const [contentAnalysis, setContentAnalysis] = useState<PanelContentAnalysisResponse | null>(null);
  const [analysisFingerprint, setAnalysisFingerprint] = useState("");
  const [fitProposal, setFitProposal] = useState<PanelContentAnalysis | null>(null);
  const [dragState, setDragState] = useState<
    | {
        panelIndex: number;
        kind: "move" | "resize";
        edge?: PanelResizeEdge;
        startSvgX: number;
        startSvgY: number;
        stationPerSvg: number;
        elevationPerSvg: number;
        original: DraftPanel;
        viewDomain: { xMin: number; xMax: number; yMin: number; yMax: number };
      }
    | null
  >(null);
  const epoch = useRef(0);
  useEffect(() => { epoch.current++; return () => { epoch.current++; }; }, [open, sessionId]);
  const committed = useRef("");
  const applying = useRef(false);
  const engineeringVersion = useRef<string>();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [cursorPos, setCursorPos] = useState<{ station: number | null; elevation: number | null }>({ station: null, elevation: null });
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape" && !applying.current) onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  function formatDisplayNumber(value: number | null | undefined) {
    return formatPanelDraftNumber(value);
  }

  function panelFingerprint(panels: DraftPanel[]) {
    return JSON.stringify(panels.map((panel) => ({
      panel: panel.panel, panel_suffix: panel.panel_suffix ?? "",
      start_station: panel.start_station, end_station: panel.end_station,
      start_elevation: panel.start_elevation, end_elevation: panel.end_elevation,
      h_scale: panel.h_scale, v_scale: panel.v_scale,
    })));
  }

  async function requestContentAnalysis(panels: DraftPanel[], requestedPageSize: string) {
    return api.analyzePanelContent(sessionId, panels, requestedPageSize, 5);
  }

  function storeContentAnalysis(response: PanelContentAnalysisResponse, panels: DraftPanel[]) {
    setContentAnalysis(response);
    setAnalysisFingerprint(panelFingerprint(panels));
  }

  function buildPreviewBounds(panels: DraftPanel[]) {
    if (!panels.length) {
      return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }

    const xValues = panels.flatMap((panel) => [Number(panel.start_station), Number(panel.end_station)])
      .filter((value) => Number.isFinite(value));
    const yValues = panels.flatMap((panel) => [Number(panel.start_elevation), Number(panel.end_elevation)])
      .filter((value) => Number.isFinite(value));

    if (!xValues.length || !yValues.length) {
      return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }

    const xMinRaw = Math.min(...xValues);
    const xMaxRaw = Math.max(...xValues);
    const yMin = Math.min(...yValues);
    const yMax = Math.max(...yValues);

    const xPad = Math.max(50, (xMaxRaw - xMinRaw) * 0.08);
    const yPad = Math.max(10, (yMax - yMin) * 0.12);

    return {
      xMinRaw,
      xMaxRaw,
      xMin: xMinRaw - xPad,
      xMax: xMaxRaw + xPad,
      yMin: yMin - yPad,
      yMax: yMax + yPad,
    };
  }

  useEffect(() => {
    if (!open || !sessionId || stationingDirty) return;
    let ignore = false;

    async function loadPanels() {
      try {
        setIsLoading(true);
        setError(null);
        const db = await api.getPanels(sessionId);
        const panels = (db.panels ?? []).map((p: any) => ({
          panel: Number(p.panel ?? NaN),
          panel_suffix: String(p.panel_suffix ?? ""),
          display_id: String(p.display_id ?? p.panel ?? ""),
          start_station: Number(p.start_station ?? NaN),
          end_station: Number(p.end_station ?? NaN),
          start_elevation: Number(p.start_elevation ?? NaN),
          end_elevation: Number(p.end_elevation ?? NaN),
          panel_x: p.panel_x ?? null,
          panel_y: p.panel_y ?? null,
          h_scale: Number(p.h_scale ?? NaN),
          v_scale: Number(p.v_scale ?? NaN),
        }));
        if (ignore) return;
        setDraftPanels(panels);
        setExcludedPanels([]);
        setNumericInputs(createPanelNumericInputs(panels));
        setPreviewMode("overview");
        setContentAnalysis(null);
        setAnalysisFingerprint("");
        setFitProposal(null);
        setSelectedPanelIndex(panels.length ? 0 : null);
        committed.current = JSON.stringify(panels);
        const fullDb = await api.getDb(sessionId);
        if (ignore) return;
        const header = fullDb?.header ?? {};
        engineeringVersion.current = fullDb.engineering_version;
        if (fullDb.stationing_dirty) throw new Error("CUMDIST is DIRTY. Recalculate before Panel Setup.");
        const loadedHScale = Number(header.h_scale ?? panels[0]?.h_scale ?? 500);
        const loadedVScale = Number(header.v_scale ?? panels[0]?.v_scale ?? 5);
        setHScale(loadedHScale);
        setVScale(loadedVScale);
        setHScaleInput(formatPanelDraftNumber(loadedHScale));
        setVScaleInput(formatPanelDraftNumber(loadedVScale));
        const loadedPageSize = String(header.page_size ?? "11x17");
        setPageSize(loadedPageSize);
        setStatus(fullDb.panels_stale ? "Saved panels need review after engineering changes. Review and Apply; Cancel leaves them unchanged." : `${panels.length} panels loaded`);
        try {
          const analysis = await requestContentAnalysis(panels, loadedPageSize);
          if (ignore) return;
          storeContentAnalysis(analysis, panels);
        } catch (analysisError: any) {
          if (!ignore) setStatus(`Panels loaded. Engineering-content analysis unavailable: ${String(analysisError.message ?? analysisError)}`);
        }
      } catch (e: any) {
      if (e instanceof StaleResponse) return;
        if (!ignore) {
          setError(String(e.message ?? e));
          setStatus("Unable to load panel definitions");
        }
      } finally {
        if (!ignore) setIsLoading(false);
      }
    }

    loadPanels();
    return () => {
      ignore = true;
    };
  }, [open, sessionId, stationingDirty]);

  const activeContentAnalysis = useMemo(() =>
    analysisFingerprint === panelFingerprint(draftPanels) ? contentAnalysis : null,
    [analysisFingerprint, contentAnalysis, draftPanels]);
  const analysisByPanel = useMemo(() => new Map(
    (activeContentAnalysis?.panels ?? []).map((item) => [item.panel, item])),
    [activeContentAnalysis]);

  const preview = useMemo(() => {
    if (!profile || !draftPanels.length) return null;

    const xs = profile.cross_sections ?? [];
    if (!xs.length) return null;

    const bounds = buildPreviewBounds(draftPanels);

    // available engineering domain (from profile cumdist)
    const cumdists = xs.map((r) => Number(r.cumdist ?? NaN)).filter((v) => Number.isFinite(v));
    const availableMinStation = cumdists.length ? Math.min(...cumdists) : bounds.xMinRaw ?? bounds.xMin;
    const availableMaxStation = cumdists.length ? Math.max(...cumdists) : bounds.xMaxRaw ?? bounds.xMax;
    const primaryRuns = profile.profile_segments?.[profile.profile] ?? profileSegments(xs.map(row => [row.cumdist, row.wsel]));
    const invertRuns = profileSegments(xs.map(row => [row.cumdist, row.minchel]));
    const profileElevations = [...primaryRuns, ...invertRuns]
      .flatMap((run) => run.map((point) => Number(point[1])))
      .filter((value) => Number.isFinite(value));

    // plot margins in viewBox units (0..100) - keep small to maximize usable area
    const plot: { left: number; right: number; top: number; bottom: number; width: number; height: number } = {
      left: 6,
      right: 4,
      top: 5,
      bottom: 8,
      width: 100 - 6 - 4,
      height: 100 - 5 - 8,
    };

    const selectedPanel = selectedPanelIndex == null ? null : draftPanels[selectedPanelIndex] ?? null;
    const selectedXPad = selectedPanel ? Math.max(1, (selectedPanel.end_station - selectedPanel.start_station) * 0.05) : 0;
    const selectedYPad = selectedPanel ? Math.max(0.5, (selectedPanel.end_elevation - selectedPanel.start_elevation) * 0.08) : 0;
    const overviewXMinRaw = Math.min(availableMinStation, bounds.xMinRaw ?? availableMinStation);
    const overviewXMaxRaw = Math.max(availableMaxStation, bounds.xMaxRaw ?? availableMaxStation);
    const overviewXPad = Math.max(1, (overviewXMaxRaw - overviewXMinRaw) * 0.03);
    const overviewYMinRaw = Math.min(bounds.yMin, ...(profileElevations.length ? profileElevations : [bounds.yMin]));
    const overviewYMaxRaw = Math.max(bounds.yMax, ...(profileElevations.length ? profileElevations : [bounds.yMax]));
    const overviewYPad = Math.max(0.5, (overviewYMaxRaw - overviewYMinRaw) * 0.08);
    const fittedDomain = previewMode === "selected" && selectedPanel ? {
      xMin: selectedPanel.start_station - selectedXPad,
      xMax: selectedPanel.end_station + selectedXPad,
      yMin: selectedPanel.start_elevation - selectedYPad,
      yMax: selectedPanel.end_elevation + selectedYPad,
    } : {
      xMin: overviewXMinRaw - overviewXPad,
      xMax: overviewXMaxRaw + overviewXPad,
      yMin: overviewYMinRaw - overviewYPad,
      yMax: overviewYMaxRaw + overviewYPad,
    };
    // Lock the displayed domain during a gesture so pointer deltas continue to
    // map to the same engineering coordinates. Refit only after pointer-up.
    const viewDomain = dragState?.viewDomain ?? fittedDomain;
    const viewXMin = viewDomain.xMin;
    const viewXMax = viewDomain.xMax;
    const viewYMin = viewDomain.yMin;
    const viewYMax = viewDomain.yMax;

    const stationToX = (station: number) => {
      const ratio = (Number(station) - viewXMin) / Math.max(1e-9, viewXMax - viewXMin);
      return plot.left + ratio * plot.width;
    };

    const xToStation = (x: number) => {
      const ratio = (x - plot.left) / Math.max(1e-9, plot.width);
      return viewXMin + ratio * (viewXMax - viewXMin);
    };

    const elevationToY = (elevation: number) => {
      const ratio = (Number(elevation) - viewYMin) / Math.max(1e-9, viewYMax - viewYMin);
      return 100 - plot.bottom - ratio * plot.height;
    };

    const yToElevation = (y: number) => {
      const ratio = (100 - plot.bottom - y) / Math.max(1e-9, plot.height);
      return viewYMin + ratio * (viewYMax - viewYMin);
    };

    const primaryPoints = primaryRuns
      .map(run => run.map(([x,y]) => `${stationToX(x)},${elevationToY(y)}`).join(" "));
    const invertPoints = invertRuns
      .map(run => run.map(([x,y]) => `${stationToX(x)},${elevationToY(y)}`).join(" "));

    const xTicks = displayTicks(viewXMin, viewXMax, Math.max(1, Number(selectedPanel?.h_scale ?? hScale)), snapPreviewGrid)
      .map(value => ({ value, x: stationToX(value) }));
    const yTicks = displayTicks(viewYMin, viewYMax, Math.max(0.1, Number(selectedPanel?.v_scale ?? vScale)), snapPreviewGrid)
      .sort((a,b) => b-a).map(value => ({ value, y: elevationToY(value) }));

    const crossSections = xs.map((row) => {
      const x = Number(row.cumdist ?? NaN);
      if (!Number.isFinite(x)) return null;
      return { x: stationToX(x), label: row.rs ?? "XS" };
    }).filter((p): p is { x: number; label: string } => p !== null);

    const panelBoundaries = draftPanels.flatMap((panel, panelIndex) => [
      {
        panelIndex,
        field: "start_station" as const,
        x: stationToX(Number(panel.start_station)),
        value: Number(panel.start_station),
      },
      {
        panelIndex,
        field: "end_station" as const,
        x: stationToX(Number(panel.end_station)),
        value: Number(panel.end_station),
      },
    ]);
    const drawingClip = selectedPanel && previewMode === "selected" ? {
      x: stationToX(selectedPanel.start_station),
      y: elevationToY(selectedPanel.end_elevation),
      width: stationToX(selectedPanel.end_station) - stationToX(selectedPanel.start_station),
      height: elevationToY(selectedPanel.start_elevation) - elevationToY(selectedPanel.end_elevation),
    } : { x: plot.left, y: plot.top, width: plot.width, height: plot.height };
    const outOfModelPanels = draftPanels.filter((panel) =>
      panel.start_station < availableMinStation || panel.end_station > availableMaxStation);

    return {
      bounds,
      availableMinStation,
      availableMaxStation,
      xTicks,
      yTicks,
      plot,
      primaryPoints,
      invertPoints,
      crossSections,
      stationToX,
      xToStation,
      elevationToY,
      yToElevation,
      panelBoundaries,
      viewDomain,
      drawingClip,
      outOfModelPanels,
    };
  }, [draftPanels, dragState, fitVersion, hScale, previewMode, profile, selectedPanelIndex, snapPreviewGrid, vScale]);
  const stitching = useMemo(() => panelRelationships(draftPanels), [draftPanels]);

  function materializeForInteraction() {
    const materialized = committedNumericDraft();
    if (!materialized.ok) {
      setError(`${materialized.error} Correct the value before moving or resizing the panel.`);
      return null;
    }
    return materialized.panels;
  }

  function resizeLimits(panel: DraftPanel) {
    const page = PAGE_DIMENSIONS[pageSize];
    const panelHScale = Number(panel.h_scale ?? hScale);
    const panelVScale = Number(panel.v_scale ?? vScale);
    if (!page) throw new Error(`Page size ${pageSize} is unsupported for interactive resizing.`);
    if (![panelHScale, panelVScale].every((value) => Number.isFinite(value) && value > 0)) {
      throw new Error("Saved panel scales must be finite and positive before resizing.");
    }
    return {
      maxStationSpan: (page[0] - 4) * panelHScale,
      maxElevationSpan: (page[1] - 2) * panelVScale,
    };
  }

  function movePanel(index: number, stationDelta: number, elevationDelta: number) {
    const materialized = materializeForInteraction();
    if (!materialized) return;
    try {
      const next = materialized.map((panel, panelIndex) =>
          panelIndex === index ? translatePanel(panel, stationDelta, elevationDelta) : panel);
      setDraftPanels(next);
      setNumericInputs((current) => synchronizePanelNumericInputs(current, next, [index]));
      setError(null);
    } catch (reason: any) {
      setError(String(reason.message ?? reason));
    }
  }

  function addPanelAfterSelected() {
    const materialized = materializeForInteraction();
    if (!materialized) return;
    const sourceIndex = selectedPanelIndex ?? Math.max(0, materialized.length - 1);
    const source = materialized[sourceIndex];
    if (!source) { setError("Create panels with Auto Generate before adding another panel."); return; }
    const width = Number(source.end_station) - Number(source.start_station);
    const nextId = Math.max(0, ...materialized.map(panel => Number(panel.panel))) + 1;
    const added = { ...source, panel: nextId, panel_suffix: "", display_id: String(nextId),
      start_station: Number(source.end_station), end_station: Number(source.end_station) + width };
    const next = [...materialized.slice(0, sourceIndex + 1), added, ...materialized.slice(sourceIndex + 1)];
    setDraftPanels(next); setNumericInputs(createPanelNumericInputs(next)); setSelectedPanelIndex(sourceIndex + 1);
    setStatus(`Panel ${nextId} added. Review coverage before Apply.`); setError(null);
  }

  function removeSelectedPanel() {
    if (selectedPanelIndex == null) return;
    const next = draftPanels.filter((_, index) => index !== selectedPanelIndex);
    setDraftPanels(next); setNumericInputs(createPanelNumericInputs(next));
    setSelectedPanelIndex(next.length ? Math.min(selectedPanelIndex, next.length - 1) : null);
    setStatus("Panel removed from the draft layout. Apply validates revision coverage."); setError(null);
  }

  function excludeSelectedPanel() {
    if (selectedPanelIndex == null) return;
    const excluded = draftPanels[selectedPanelIndex];
    const next = draftPanels.filter((_, index) => index !== selectedPanelIndex);
    setExcludedPanels((current) => [...current, excluded]);
    setDraftPanels(next); setNumericInputs(createPanelNumericInputs(next));
    setSelectedPanelIndex(next.length ? Math.min(selectedPanelIndex, next.length - 1) : null);
    setStatus(`Panel ${excluded.display_id ?? excluded.panel} excluded from the draft saved set. Restore it before Apply if needed.`);
  }

  function restoreExcludedPanel(panelId: number) {
    const restored = excludedPanels.find(panel => panel.panel === panelId);
    if (!restored) return;
    const next = [...draftPanels, restored];
    setExcludedPanels(current => current.filter(panel => panel.panel !== panelId));
    setDraftPanels(next); setNumericInputs(createPanelNumericInputs(next)); setSelectedPanelIndex(next.length - 1);
    setStatus(`Panel ${restored.display_id ?? restored.panel} restored to the draft saved set.`);
  }

  function reorderSelectedPanel(delta: number) {
    if (selectedPanelIndex == null) return;
    const target = selectedPanelIndex + delta;
    if (target < 0 || target >= draftPanels.length) return;
    const next = [...draftPanels]; [next[selectedPanelIndex], next[target]] = [next[target], next[selectedPanelIndex]];
    setDraftPanels(next); setNumericInputs(createPanelNumericInputs(next)); setSelectedPanelIndex(target);
    setStatus("Draft panel order changed. Numeric panel IDs and suffixes were preserved.");
  }

  function updatePanel(index: number, field: keyof DraftPanel, rawValue: string) {
    if (field === "panel_suffix") {
      setDraftPanels((previous) => previous.map((panel, panelIndex) =>
        panelIndex === index ? { ...panel, panel_suffix: rawValue } : panel));
      setError(null);
    }
  }

  function updateNumericInput(index: number, field: PanelNumericField, rawValue: string) {
    setNumericInputs((current) => ({
      ...current,
      [panelNumericInputKey(index, field)]: rawValue,
    }));
  }

  function commitNumericInput(index: number, field: PanelNumericField) {
    const materialized = materializePanelNumericInputs(draftPanels, numericInputs);
    if (!materialized.ok) {
      setError(materialized.error);
      return false;
    }
    // Commit all currently valid raw bounds together. This lets the user fix
    // either side of a temporarily invalid pair without previewing the invalid
    // geometry in between.
    setDraftPanels(materialized.panels);
    setError(null);
    return true;
  }

  function committedNumericDraft() {
    const materialized = materializePanelNumericInputs(draftPanels, numericInputs);
    if (!materialized.ok) return materialized;
    const parsedHScale = parseFinitePanelInput(hScaleInput, "Horizontal Scale");
    if (!parsedHScale.ok || parsedHScale.value <= 0) {
      return { ok: false as const, error: parsedHScale.ok ? "Horizontal Scale must be greater than 0." : parsedHScale.error };
    }
    const parsedVScale = parseFinitePanelInput(vScaleInput, "Vertical Scale");
    if (!parsedVScale.ok || parsedVScale.value <= 0) {
      return { ok: false as const, error: parsedVScale.ok ? "Vertical Scale must be greater than 0." : parsedVScale.error };
    }
    return { ok: true as const, panels: materialized.panels, hScale: parsedHScale.value, vScale: parsedVScale.value };
  }

  function commitScaleInput(axis: "horizontal" | "vertical") {
    const raw = axis === "horizontal" ? hScaleInput : vScaleInput;
    const label = axis === "horizontal" ? "Horizontal Scale" : "Vertical Scale";
    const parsed = parseFinitePanelInput(raw, label);
    if (!parsed.ok || parsed.value <= 0) {
      setError(parsed.ok ? `${label} must be greater than 0.` : parsed.error);
      return false;
    }
    if (axis === "horizontal") {
      setHScale(parsed.value);
      setHScaleInput(formatPanelDraftNumber(parsed.value));
    } else {
      setVScale(parsed.value);
      setVScaleInput(formatPanelDraftNumber(parsed.value));
    }
    setError(null);
    return true;
  }

  function validatePanels(panels = draftPanels, horizontalScale = hScale, verticalScale = vScale) {
    if (!panels.length) return "No panel definitions are available.";

    for (const panel of panels) {
      const suffix = (panel.panel_suffix ?? "").trim();
      if (suffix.length > 3 || (suffix.length > 0 && !/^[A-Za-z]+$/.test(suffix))) {
        return `Suffix for Panel ${panel.display_id ?? panel.panel} must contain up to three letters A-Z.`;
      }
      if (!Number.isFinite(panel.panel) || panel.panel < 1) {
        return `Panel number must be valid for panel ${panel.panel ?? "?"}.`;
      }
      if (!Number.isFinite(panel.start_station)) return `Start station is invalid for Panel ${panel.panel}.`;
      if (!Number.isFinite(panel.end_station)) return `End station is invalid for Panel ${panel.panel}.`;
      if (!Number.isFinite(panel.start_elevation)) return `Starting elevation is invalid for Panel ${panel.panel}.`;
      if (!Number.isFinite(panel.end_elevation)) return `Ending elevation is invalid for Panel ${panel.panel}.`;
      if (panel.end_station <= panel.start_station) {
        return `Panel ${panel.panel}: Ending Station must be greater than Starting Station.`;
      }
      if (panel.end_elevation <= panel.start_elevation) {
        return `Panel ${panel.panel}: Ending Elevation must be greater than Starting Elevation.`;
      }
      try {
        const limits = resizeLimits(panel);
        if (panel.end_station - panel.start_station > limits.maxStationSpan + 1e-9) {
          return `Panel ${panel.panel}: station span exceeds the physical page capacity at its saved horizontal scale.`;
        }
        if (panel.end_elevation - panel.start_elevation > limits.maxElevationSpan + 1e-9) {
          return `Panel ${panel.panel}: elevation span exceeds the physical page capacity at its saved vertical scale.`;
        }
      } catch (reason: any) {
        return String(reason.message ?? reason);
      }
      if (revisionStart != null && revisionEnd != null
          && (panel.end_station <= revisionStart || panel.start_station >= revisionEnd)) {
        return `Panel ${panel.panel} does not overlap the active revision range.`;
      }
    }

    if (!Number.isFinite(Number(horizontalScale)) || Number(horizontalScale) <= 0) return "Horizontal Scale must be greater than 0.";
    if (!Number.isFinite(Number(verticalScale)) || Number(verticalScale) <= 0) return "Vertical Scale must be greater than 0.";

    return null;
  }

  async function handleAnalyzeContent() {
    const token = epoch.current;
    const materialized = committedNumericDraft();
    if (!materialized.ok) { setError(materialized.error); return; }
    const validation = validatePanels(materialized.panels, materialized.hScale, materialized.vScale);
    if (validation) { setError(validation); return; }
    setIsLoading(true); setError(null); setFitProposal(null);
    try {
      const result = await requestContentAnalysis(materialized.panels, pageSize);
      if (token !== epoch.current) return;
      storeContentAnalysis(result, materialized.panels);
      setDraftPanels(materialized.panels);
      setNumericInputs(createPanelNumericInputs(materialized.panels));
      const invalid = result.panels.filter((item) => !item.existing_panel_valid);
      setStatus(invalid.length
        ? `${invalid.length} panel(s) need elevation review: ${invalid.map((item) => item.display_id).join(", ")}.`
        : "All panels contain the analyzed engineering content.");
    } catch (reason: any) {
      if (!(reason instanceof StaleResponse) && token === epoch.current) setError(String(reason.message ?? reason));
    } finally {
      if (token === epoch.current) setIsLoading(false);
    }
  }

  function proposeSelectedPanelFit() {
    if (selectedPanelIndex == null) return;
    const panel = draftPanels[selectedPanelIndex];
    const analysis = analysisByPanel.get(panel.panel);
    if (!analysis) {
      setError("Analyze Engineering Content for the current draft before fitting this panel.");
      return;
    }
    setFitProposal(analysis);
    setError(null);
  }

  function acceptFitProposal() {
    if (!fitProposal || selectedPanelIndex == null) return;
    if (fitProposal.requires_scale_or_page_change) {
      setError("This proposal exceeds the saved physical plot height. Choose and review a vertical scale or page-size change separately.");
      return;
    }
    const next = draftPanels.map((panel, index) => index === selectedPanelIndex ? {
      ...panel,
      start_elevation: fitProposal.suggested_minimum_elevation,
      end_elevation: fitProposal.suggested_maximum_elevation,
    } : panel);
    setDraftPanels(next);
    setNumericInputs((current) => synchronizePanelNumericInputs(current, next, [selectedPanelIndex]));
    setFitProposal(null);
    setContentAnalysis(null);
    setAnalysisFingerprint("");
    setStatus(`Suggested limits added to the draft for Panel ${fitProposal.display_id}. Review the preview, then Apply to approve them.`);
  }

  async function handleApply() {
    if (applying.current || isLoading) return;
    const token = epoch.current;
    const materialized = committedNumericDraft();
    if (!materialized.ok) {
      setError(materialized.error);
      return;
    }
    const msg = validatePanels(materialized.panels, materialized.hScale, materialized.vScale);
    if (msg) {
      setError(msg);
      return;
    }

    setError(null);
    setIsLoading(true);
    applying.current = true;
    try {
      const payload = {
        expected_engineering_version: engineeringVersion.current,
        panels: materialized.panels.map((p) => ({
          panel: Number(p.panel),
          panel_suffix: p.panel_suffix ?? "",
          start_station: Number(p.start_station),
          end_station: Number(p.end_station),
          start_elevation: Number(p.start_elevation),
          end_elevation: Number(p.end_elevation),
          panel_x: p.panel_x ?? null,
          panel_y: p.panel_y ?? null,
          h_scale: Number(p.h_scale ?? materialized.hScale),
          v_scale: Number(p.v_scale ?? materialized.vScale),
        })),
        auto: false,
        h_scale: materialized.hScale,
        v_scale: materialized.vScale,
        page_size: pageSize,
      };
      const result = await api.putPanels(sessionId, payload);
      if (token !== epoch.current) return;
      setDraftPanels(result.panels);
      setExcludedPanels([]);
      setNumericInputs(createPanelNumericInputs(result.panels));
      setHScale(materialized.hScale);
      setVScale(materialized.vScale);
      setHScaleInput(formatPanelDraftNumber(materialized.hScale));
      setVScaleInput(formatPanelDraftNumber(materialized.vScale));
      committed.current = JSON.stringify(result.panels);
      setStatus("Panel configuration applied");
      onApplied?.(result.panels);
      onClose();
    } catch (e: any) {
      if (e instanceof StaleResponse || token !== epoch.current) return;
      setError(String(e.message ?? e));
    } finally {
      applying.current = false;
      if (token === epoch.current) setIsLoading(false);
    }
  }

  function handleRefreshFit() {
    const materialized = committedNumericDraft();
    if (!materialized.ok) {
      setError(materialized.error);
      return;
    }
    setDraftPanels(materialized.panels);
    setNumericInputs(createPanelNumericInputs(materialized.panels));
    setHScale(materialized.hScale);
    setVScale(materialized.vScale);
    setHScaleInput(formatPanelDraftNumber(materialized.hScale));
    setVScaleInput(formatPanelDraftNumber(materialized.vScale));
    setFitVersion((current) => current + 1);
    setStatus("Preview fit refreshed");
    setError(null);
    setPreviewMode(selectedPanelIndex == null ? "overview" : "selected");
  }

  async function handleAutoGenerate() {
    const token = epoch.current;
    const materialized = committedNumericDraft();
    if (!materialized.ok) {
      setError(materialized.error);
      return;
    }
    const hasDraftEdits = JSON.stringify(draftPanels) !== committed.current
      || JSON.stringify(numericInputs) !== JSON.stringify(createPanelNumericInputs(draftPanels))
      || hScaleInput !== formatPanelDraftNumber(hScale)
      || vScaleInput !== formatPanelDraftNumber(vScale);

    if (hasDraftEdits && !window.confirm("Auto Generate will replace the current panel boundaries. Continue?")) {
      return;
    }

    setError(null);
    setIsLoading(true);
    try {
      const payload = {
        expected_engineering_version: engineeringVersion.current,
        panels: materialized.panels.map((p) => ({
          panel: Number(p.panel ?? NaN),
          panel_suffix: p.panel_suffix ?? "",
          start_station: Number(p.start_station ?? NaN),
          end_station: Number(p.end_station ?? NaN),
          start_elevation: Number(p.start_elevation ?? NaN),
          end_elevation: Number(p.end_elevation ?? NaN),
          panel_x: p.panel_x ?? null,
          panel_y: p.panel_y ?? null,
          h_scale: Number(p.h_scale ?? materialized.hScale),
          v_scale: Number(p.v_scale ?? materialized.vScale),
        })),
        auto: true,
        draft: true,
        h_scale: materialized.hScale,
        v_scale: materialized.vScale,
        page_size: pageSize,
      };
      const res = await api.putPanels(sessionId, payload);
      if (token !== epoch.current) return;
      const panels = (res.panels ?? []).map((p: any) => ({
        panel: Number(p.panel ?? NaN),
        panel_suffix: String(p.panel_suffix ?? ""),
        display_id: String(p.display_id ?? p.panel ?? ""),
        start_station: Number(p.start_station ?? NaN),
        end_station: Number(p.end_station ?? NaN),
        start_elevation: Number(p.start_elevation ?? NaN),
        end_elevation: Number(p.end_elevation ?? NaN),
        panel_x: p.panel_x ?? null,
        panel_y: p.panel_y ?? null,
        h_scale: Number(p.h_scale ?? hScale),
        v_scale: Number(p.v_scale ?? vScale),
      }));
      setDraftPanels(panels);
      setExcludedPanels([]);
      setNumericInputs(createPanelNumericInputs(panels));
      setHScale(materialized.hScale);
      setVScale(materialized.vScale);
      setHScaleInput(formatPanelDraftNumber(materialized.hScale));
      setVScaleInput(formatPanelDraftNumber(materialized.vScale));
      setSelectedPanelIndex(panels.length ? 0 : null);
      setPreviewMode("overview");
      setStatus(`${panels.length} auto-generated panel${panels.length === 1 ? "" : "s"}`);
    } catch (e: any) {
      if (e instanceof StaleResponse || token !== epoch.current) return;
      console.error("Auto Generate failed:", e);
      setError(String(e.message ?? "Unable to auto-generate panels. Check the panel settings."));
    } finally {
      if (token === epoch.current) setIsLoading(false);
    }
  }

  // Translate a whole panel in engineering coordinates; saved dimensions and
  // scales remain unchanged until authoritative backend validation on Apply.
  function handlePanelPointerDown(e: any, panelIndex: number, edge?: PanelResizeEdge) {
    if (!preview || !svgRef.current) return;
    const materialized = materializeForInteraction();
    if (!materialized) return;
    try {
      (e.currentTarget as Element).setPointerCapture((e.nativeEvent as PointerEvent).pointerId);
    } catch {}
    const point = svgRef.current.createSVGPoint();
    point.x = (e.nativeEvent as PointerEvent).clientX;
    point.y = (e.nativeEvent as PointerEvent).clientY;
    const ctm = svgRef.current.getScreenCTM();
    if (!ctm) return;
    const svgPoint = point.matrixTransform(ctm.inverse());
    setDraftPanels(materialized);
    setSelectedPanelIndex(panelIndex);
    setDragState({
      panelIndex,
      kind: edge ? "resize" : "move",
      edge,
      startSvgX: svgPoint.x,
      startSvgY: svgPoint.y,
      stationPerSvg: (preview.viewDomain.xMax - preview.viewDomain.xMin) / preview.plot.width,
      elevationPerSvg: (preview.viewDomain.yMax - preview.viewDomain.yMin) / preview.plot.height,
      original: { ...materialized[panelIndex] },
      viewDomain: { ...preview.viewDomain },
    });
  }

  function handleSvgPointerMove(e: any) {
    if (!preview || !svgRef.current) return;
    const svg = svgRef.current;
    const pt = svg.createSVGPoint();
    pt.x = (e.nativeEvent as PointerEvent).clientX;
    pt.y = (e.nativeEvent as PointerEvent).clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const svgP = pt.matrixTransform(ctm.inverse());
    const stationRaw = preview.xToStation(svgP.x);
    const elevRaw = preview.yToElevation(svgP.y);

    setCursorPos({ station: stationRaw, elevation: elevRaw });

    if (!dragState) return;

    const stationDelta = (svgP.x - dragState.startSvgX) * dragState.stationPerSvg;
    const elevationDelta = -(svgP.y - dragState.startSvgY) * dragState.elevationPerSvg;
    let moved: DraftPanel;
    try {
      if (dragState.kind === "move") {
        moved = translatePanel(dragState.original, stationDelta, elevationDelta);
      } else {
        const edge = dragState.edge!;
        const coordinate = edge === "left" ? dragState.original.start_station + stationDelta
          : edge === "right" ? dragState.original.end_station + stationDelta
          : edge === "top" ? dragState.original.end_elevation + elevationDelta
          : dragState.original.start_elevation + elevationDelta;
        moved = resizePanelEdge(dragState.original, edge, coordinate, resizeLimits(dragState.original));
      }
    } catch (reason: any) {
      setError(String(reason.message ?? reason));
      return;
    }
    setDraftPanels((previous) => {
      const next = previous.map((panel, index) => index === dragState.panelIndex ? moved : panel);
      setNumericInputs((current) => synchronizePanelNumericInputs(current, next, [dragState.panelIndex]));
      return next;
    });
    setError(null);
  }

  function handleSvgPointerUp(e: any) {
    try {
      (e.currentTarget as Element).releasePointerCapture((e.nativeEvent as PointerEvent).pointerId);
    } catch {}
    setDragState(null);
    if (selectedPanelIndex != null) setPreviewMode("selected");
  }

  

  if (!open) return null;
  if (stationingDirty) return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="panel-setup-modal panel-setup-modal--blocked" role="dialog" aria-modal="true" aria-labelledby="panel-setup-blocked-title" onClick={(event) => event.stopPropagation()}>
        <div className="panel-setup-header"><div><h3 id="panel-setup-blocked-title">Panel Setup</h3><p>Saved panels are preserved.</p></div><button type="button" className="close-button" onClick={onClose} aria-label="Close Panel Setup">×</button></div>
        <div className="panel-dirty-message"><strong>Recalculate CUMDIST to continue</strong><p>Panel geometry cannot be edited or applied while stationing is dirty. Recalculate CUMDIST, then reopen Panel Setup and review the saved panels.</p></div>
        <div className="panel-setup-footer"><button type="button" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );

  return (
    <div className="panel-setup-backdrop" onClick={() => { if (!applying.current) onClose(); }}>
      <div className="panel-setup-modal" role="dialog" aria-modal="true" aria-labelledby="panel-setup-title" onClick={(e) => e.stopPropagation()}>
        <div className="panel-setup-header">
          <div>
            <span className="eyebrow">Panels and drawing extent</span><h3 id="panel-setup-title">Panel Setup</h3>
            {revisionStart != null && revisionEnd != null && (
              <div className="hint">
                Active revision: {formatDisplayNumber(revisionStart)} - {formatDisplayNumber(revisionEnd)} ft
              </div>
            )}
          </div>
          <button type="button" className="panel-setup-close" disabled={isLoading} onClick={onClose} aria-label="Close panel setup">×</button>
        </div>

        <div className="panel-setup-controls">
          <label>
            <span>Auto Generate horizontal scale</span>
            <div className="panel-setup-scale-input">
              <span>1&quot; =</span>
              <input type="text" inputMode="decimal" value={hScaleInput} onChange={(e) => setHScaleInput(e.target.value)} onBlur={() => commitScaleInput("horizontal")} />
              <span>ft</span>
            </div>
          </label>

          <label>
            <span>Auto Generate vertical scale</span>
            <div className="panel-setup-scale-input">
              <span>1&quot; =</span>
              <input type="text" inputMode="decimal" value={vScaleInput} onChange={(e) => setVScaleInput(e.target.value)} onBlur={() => commitScaleInput("vertical")} />
              <span>ft</span>
            </div>
          </label>

          <label>
            <span>Engineering sheet size</span>
            <select value={pageSize} onChange={(event) => {
              setPageSize(event.target.value);
              setContentAnalysis(null);
              setAnalysisFingerprint("");
              setFitProposal(null);
              setStatus(`Draft sheet changed to ${event.target.value}. Analyze content, then Apply to save and review.`);
            }}>
              <option value="11x17">11×17 landscape</option>
              <option value="ANSI_C">ANSI C — 22×17 landscape</option>
            </select>
            <small>Changing the sheet does not change panel bounds or scales. Apply is required.</small>
          </label>

          <div className="panel-setup-actions">
            <button type="button" disabled={isLoading} onClick={handleAutoGenerate}>Auto Generate</button>
            <button type="button" disabled={isLoading || draftPanels.length === 0} onClick={addPanelAfterSelected}>Add Panel</button>
            <button type="button" disabled={isLoading || selectedPanelIndex == null} onClick={removeSelectedPanel}>Remove Selected</button>
            <button type="button" disabled={isLoading || selectedPanelIndex == null} onClick={excludeSelectedPanel}>Exclude Selected</button>
            <button type="button" disabled={isLoading || selectedPanelIndex == null || selectedPanelIndex === 0} onClick={() => reorderSelectedPanel(-1)}>Move Earlier</button>
            <button type="button" disabled={isLoading || selectedPanelIndex == null || selectedPanelIndex === draftPanels.length - 1} onClick={() => reorderSelectedPanel(1)}>Move Later</button>
            <button type="button" onClick={handleRefreshFit}>Refresh / Fit</button>
            <button type="button" aria-pressed={previewMode === "overview"}
              onClick={() => { setPreviewMode("overview"); setStatus("Full profile overview"); }}>Full Profile Overview</button>
            <button type="button" aria-pressed={previewMode === "selected"} disabled={selectedPanelIndex == null}
              onClick={() => { setPreviewMode("selected"); setStatus("Selected panel preview"); }}>Selected Panel Preview</button>
            <button type="button" disabled={isLoading || draftPanels.length === 0} onClick={handleAnalyzeContent}>Analyze Engineering Content</button>
            <button type="button" disabled={isLoading || selectedPanelIndex == null || !activeContentAnalysis} onClick={proposeSelectedPanelFit}>Fit Panel to Engineering Content</button>
            <label className="panel-grid-snap"><input type="checkbox" checked={snapPreviewGrid} onChange={event => setSnapPreviewGrid(event.target.checked)} /> Snap preview grid labels to scale-aware intervals</label>
          </div>
          {excludedPanels.length > 0 && <div className="excluded-panel-tray" aria-label="Excluded draft panels">
            <strong>Excluded from draft saved set</strong>
            {excludedPanels.map(panel => <button type="button" key={panel.panel} onClick={() => restoreExcludedPanel(panel.panel)}>Restore Panel {panel.display_id ?? panel.panel}</button>)}
            <span>Exclusions are reversible until Apply. Export selection is configured separately in Export.</span>
          </div>}
          <p className="panel-snap-policy">Grid snapping changes preview tick labels only. Exact Revision Limits and saved panel bounds are never rounded or moved.</p>
          {fitProposal && (
            <section className={`panel-fit-proposal ${fitProposal.requires_scale_or_page_change ? "panel-fit-proposal--blocked" : ""}`} aria-label={`Fit proposal for Panel ${fitProposal.display_id}`}>
              <div>
                <strong>Proposed limits for Panel {fitProposal.display_id}</strong>
                <p>Saved: {formatDisplayNumber(fitProposal.existing_minimum_elevation)}–{formatDisplayNumber(fitProposal.existing_maximum_elevation)} ft · Required content: {formatDisplayNumber(fitProposal.required_minimum_elevation)}–{formatDisplayNumber(fitProposal.required_maximum_elevation)} ft · Proposed outward-rounded limits: {formatDisplayNumber(fitProposal.suggested_minimum_elevation)}–{formatDisplayNumber(fitProposal.suggested_maximum_elevation)} ft.</p>
                <p>Saved scales remain 1″={formatDisplayNumber(fitProposal.saved_horizontal_scale)} ft horizontal and 1″={formatDisplayNumber(fitProposal.saved_vertical_scale)} ft vertical. Page: {fitProposal.page_size}.</p>
                <p><strong>Physical sheet:</strong> {formatDisplayNumber(fitProposal.sheet_capacity.paper_width_inches)}×{formatDisplayNumber(fitProposal.sheet_capacity.paper_height_inches)} in; usable plot {formatDisplayNumber(fitProposal.sheet_capacity.usable_plot_width_inches)}×{formatDisplayNumber(fitProposal.sheet_capacity.usable_plot_height_inches)} in. The proposed limits require {formatDisplayNumber(fitProposal.sheet_capacity.required_plot_width_inches)}×{formatDisplayNumber(fitProposal.suggested_required_plot_height_inches)} in, leaving {formatDisplayNumber(fitProposal.sheet_capacity.remaining_horizontal_clearance_inches)} in horizontal and {formatDisplayNumber(fitProposal.suggested_vertical_clearance_inches)} in vertical clearance.</p>
                {fitProposal.requires_scale_or_page_change ? (
                  <p className="panel-fit-tradeoff"><strong>Separate engineering decision required.</strong> The {formatDisplayNumber(fitProposal.suggested_span)} ft proposed span exceeds the {formatDisplayNumber(fitProposal.physical_vertical_capacity)} ft physical capacity. Minimum vertical scale for this rounded proposal is approximately 1″={formatDisplayNumber(fitProposal.minimum_vertical_scale_for_suggestion)} ft, or select a taller page. No scale will be changed here.</p>
                ) : <p>This only updates the draft elevation limits. It does not approve the panel until you review and click Apply. Alternatives remain explicit: select a larger sheet, change scale, or split/redesign the panel.</p>}
                {fitProposal.responsible_content.length > 0 && <details><summary>Content outside saved limits ({fitProposal.responsible_content.length})</summary><ul>
                  {fitProposal.responsible_content.map((item) => <li key={item.identity}>{item.label}: {formatDisplayNumber(item.minimum_elevation)}–{formatDisplayNumber(item.maximum_elevation)} ft</li>)}
                </ul></details>}
              </div>
              <div className="panel-fit-proposal-actions">
                <button type="button" onClick={() => setFitProposal(null)}>Cancel Proposal</button>
                <button type="button" disabled={fitProposal.requires_scale_or_page_change} onClick={acceptFitProposal}>Use Suggested Limits</button>
              </div>
            </section>
          )}
          {selectedPanelIndex != null && draftPanels[selectedPanelIndex] && (
            <div className="panel-move-controls" aria-label={`Move Panel ${draftPanels[selectedPanelIndex].panel}`}>
              <strong>Move P{draftPanels[selectedPanelIndex].panel}</strong>
              <button type="button" onClick={() => movePanel(selectedPanelIndex, -10, 0)} aria-label="Move selected panel 10 feet downstream">← 10 ft</button>
              <button type="button" onClick={() => movePanel(selectedPanelIndex, 10, 0)} aria-label="Move selected panel 10 feet upstream">10 ft →</button>
              <button type="button" onClick={() => movePanel(selectedPanelIndex, 0, -1)} aria-label="Move selected panel down 1 foot">↓ 1 ft</button>
              <button type="button" onClick={() => movePanel(selectedPanelIndex, 0, 1)} aria-label="Move selected panel up 1 foot">1 ft ↑</button>
              <span>Dragging or moving preserves its width, height, and saved scales.</span>
            </div>
          )}
        </div>

        <div className="panel-setup-body">
          <div className="panel-setup-table-panel">
            <div className="panel-setup-table-wrap">
              <table className="panel-setup-table">
                <thead>
                  <tr>
                    <th>Panel</th>
                    <th>Suffix</th>
                    <th>Starting Station</th>
                    <th>Ending Station</th>
                    <th>Starting Elevation</th>
                    <th>Ending Elevation</th>
                    <th>Content Review</th>
                  </tr>
                </thead>
                <tbody>
                  {draftPanels.map((panel, index) => (
                    <tr key={`${panel.panel}-${index}`} className={selectedPanelIndex === index ? "selected" : ""}
                      tabIndex={0} role="button" aria-label={`Select Panel ${panel.display_id ?? panel.panel}`}
                      onClick={() => setSelectedPanelIndex(index)}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedPanelIndex(index); } }}>
                      <td>Panel {panel.display_id ?? panel.panel}</td>
                      <td>
                        <input
                          disabled={isLoading}
                          type="text"
                          maxLength={3}
                          value={panel.panel_suffix ?? ""}
                          aria-label={`Suffix for panel ${panel.panel}`}
                          onChange={(e) => updatePanel(index, "panel_suffix", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          disabled={isLoading}
                          type="text"
                          inputMode="decimal"
                          value={numericInputs[panelNumericInputKey(index, "start_station")] ?? ""}
                          onChange={(e) => updateNumericInput(index, "start_station", e.target.value)}
                          onBlur={() => commitNumericInput(index, "start_station")}
                        />
                      </td>
                      <td>
                        <input
                          disabled={isLoading}
                          type="text"
                          inputMode="decimal"
                          value={numericInputs[panelNumericInputKey(index, "end_station")] ?? ""}
                          onChange={(e) => updateNumericInput(index, "end_station", e.target.value)}
                          onBlur={() => commitNumericInput(index, "end_station")}
                        />
                      </td>
                      <td>
                        <input
                          disabled={isLoading}
                          type="text"
                          inputMode="decimal"
                          value={numericInputs[panelNumericInputKey(index, "start_elevation")] ?? ""}
                          onChange={(e) => updateNumericInput(index, "start_elevation", e.target.value)}
                          onBlur={() => commitNumericInput(index, "start_elevation")}
                        />
                      </td>
                      <td>
                        <input
                          disabled={isLoading}
                          type="text"
                          inputMode="decimal"
                          value={numericInputs[panelNumericInputKey(index, "end_elevation")] ?? ""}
                          onChange={(e) => updateNumericInput(index, "end_elevation", e.target.value)}
                          onBlur={() => commitNumericInput(index, "end_elevation")}
                        />
                      </td>
                      <td className="panel-content-status">
                        {analysisByPanel.get(panel.panel)
                          ? analysisByPanel.get(panel.panel)!.existing_panel_valid
                            ? <span className="panel-content-status--valid">Fits content</span>
                            : <span className="panel-content-status--review">Review required</span>
                          : <span>Not analyzed</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel-setup-preview-panel">
            <div className="panel-setup-preview-header">
              <span><strong>{previewMode === "overview" ? "Full Profile Overview" : "Selected Panel Preview"}</strong> · Selected WSEL (solid, gaps retained) / Ground (dashed)</span>
              <span>Drag inside to move. Drag an edge to resize its engineering bounds. Saved scales remain fixed.</span>
            </div>
            {preview && preview.outOfModelPanels.length > 0 && (
              <div className="panel-extent-warning" role="status">
                Panel {preview.outOfModelPanels.map((panel) => panel.display_id ?? panel.panel).join(", ")} extends beyond the available model station range {formatDisplayNumber(preview.availableMinStation)}–{formatDisplayNumber(preview.availableMaxStation)} ft. This is not automatically clamped; complete Revision Limits coverage is still required.
              </div>
            )}
            <div className="panel-stitch-summary" aria-label="Panel stitching status">
              {stitching.length === 0 ? <span>One panel; no stitch boundaries.</span> : stitching.map((item) => (
                <span key={`${item.fromPanel}-${item.toPanel}`} className={`panel-stitch panel-stitch--${item.kind.toLowerCase()}`}>
                  P{item.fromPanel}→P{item.toPanel}: {item.kind === "SHARED" ? `shared at ${formatDisplayNumber(item.start)} ft` : item.kind === "OVERLAP" ? `overlap ${formatDisplayNumber(item.start)}–${formatDisplayNumber(item.end)} ft` : `GAP ${formatDisplayNumber(item.start)}–${formatDisplayNumber(item.end)} ft`}
                  {item.verticalOffset ? " · different elevation windows" : ""}
                </span>
              ))}
            </div>
            {preview ? (
              <svg
                ref={svgRef}
                viewBox="0 0 100 100"
                preserveAspectRatio="xMidYMid meet"
                className="panel-setup-svg"
                onPointerMove={handleSvgPointerMove}
                onPointerUp={handleSvgPointerUp}
                onPointerCancel={handleSvgPointerUp}
              >
                <defs>
                  <clipPath id="panel-setup-drawing-clip">
                    <rect x={preview.drawingClip.x} y={preview.drawingClip.y} width={preview.drawingClip.width} height={preview.drawingClip.height} />
                  </clipPath>
                </defs>
                <rect x="0" y="0" width="100" height="100" fill="#f6f7f8" />

                {preview.viewDomain.xMin < preview.availableMinStation && (
                  <rect className="panel-outside-model" x={preview.plot.left} y={preview.plot.top}
                    width={Math.max(0, preview.stationToX(preview.availableMinStation) - preview.plot.left)} height={preview.plot.height} />
                )}
                {preview.viewDomain.xMax > preview.availableMaxStation && (
                  <rect className="panel-outside-model" x={preview.stationToX(preview.availableMaxStation)} y={preview.plot.top}
                    width={Math.max(0, 100 - preview.plot.right - preview.stationToX(preview.availableMaxStation))} height={preview.plot.height} />
                )}

                {revisionStart != null && revisionEnd != null && (
                  <rect
                    clipPath="url(#panel-setup-drawing-clip)"
                    x={preview.stationToX(revisionStart)}
                    y={preview.plot.top}
                    width={Math.max(0, preview.stationToX(revisionEnd) - preview.stationToX(revisionStart))}
                    height={preview.plot.height}
                    fill="rgba(25,107,214,0.08)"
                    stroke="#196bd6"
                    strokeWidth="0.35"
                    strokeDasharray="1.5 1"
                  />
                )}

                <g aria-label="Panel stitching indicators">
                  {stitching.map((item) => item.kind === "GAP" ? (
                    <rect key={`stitch-${item.fromPanel}-${item.toPanel}`}
                      x={preview.stationToX(item.start)} y={preview.plot.top}
                      width={Math.max(0, preview.stationToX(item.end) - preview.stationToX(item.start))}
                      height={preview.plot.height} fill="rgba(185,28,28,0.12)" stroke="#b91c1c" strokeDasharray="1 1" strokeWidth="0.35" />
                  ) : item.kind === "OVERLAP" ? (
                    <rect key={`stitch-${item.fromPanel}-${item.toPanel}`}
                      x={preview.stationToX(item.start)} y={preview.plot.top}
                      width={Math.max(0, preview.stationToX(item.end) - preview.stationToX(item.start))}
                      height={preview.plot.height} fill="rgba(109,40,217,0.08)" stroke="#6d28d9" strokeWidth="0.3" />
                  ) : (
                    <line key={`stitch-${item.fromPanel}-${item.toPanel}`}
                      x1={preview.stationToX(item.start)} x2={preview.stationToX(item.start)}
                      y1={preview.plot.top} y2={100 - preview.plot.bottom}
                      stroke="#087f5b" strokeWidth="0.55" />
                  ))}
                </g>

                <g clipPath="url(#panel-setup-drawing-clip)">
                  {preview.yTicks.map((tick) => (
                    <g key={`y-${tick.value}`}>
                      <line x1={preview.plot.left} x2={100 - preview.plot.right} y1={tick.y} y2={tick.y} stroke="#e6edf3" strokeWidth="0.15" />
                      <text x={2} y={tick.y + 1.2} fontSize="3.0" fill="#475569">{formatDisplayNumber(tick.value)}</text>
                    </g>
                  ))}

                  {preview.xTicks.map((tick) => (
                    <g key={`x-${tick.value}`}>
                      <line x1={tick.x} x2={tick.x} y1={preview.plot.top} y2={100 - preview.plot.bottom} stroke="#e6edf3" strokeWidth="0.15" />
                      <text x={tick.x} y={90} fontSize="3.0" textAnchor="middle" fill="#475569">{formatDisplayNumber(tick.value)}</text>
                    </g>
                  ))}
                </g>

                <g>
                  {draftPanels.map((panel, index) => {
                    const startX = preview.stationToX(Number(panel.start_station));
                    const endX = preview.stationToX(Number(panel.end_station));
                    const startY = preview.elevationToY(Number(panel.start_elevation));
                    const endY = preview.elevationToY(Number(panel.end_elevation));
                    const x = Math.min(startX, endX);
                    const y = Math.min(startY, endY);
                    const width = Math.max(1, Math.abs(endX - startX));
                    const height = Math.max(1, Math.abs(endY - startY));
                    const centerX = x + width / 2;
                    const centerY = y + height / 2;

                    return (
                      <g key={`${panel.panel ?? index}-panel`}>
                        <rect 
                          x={x} 
                          y={y} 
                          width={width} 
                          height={height} 
                          fill={selectedPanelIndex === index ? "#e6f0ff" : "rgba(25,107,214,0.03)"} 
                          stroke={selectedPanelIndex === index ? "#196bd6" : "none"}
                          strokeWidth="1.5"
                          onClick={() => setSelectedPanelIndex(index)}
                          tabIndex={0}
                          role="button"
                          aria-label={`Panel ${panel.display_id ?? panel.panel}; drag to move`}
                          style={{cursor: "move"}}
                          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedPanelIndex(index); } }}
                          onPointerDown={(event) => { event.preventDefault(); handlePanelPointerDown(event, index); }}
                        />
                        {selectedPanelIndex === index && (
                          <g className="panel-resize-handles" aria-label={`Resize Panel ${panel.display_id ?? panel.panel}`}>
                            <line className="panel-resize-handle panel-resize-handle--horizontal" data-resize-edge="left"
                              x1={x} x2={x} y1={y} y2={y + height}
                              aria-label="Resize starting station" role="slider"
                              onPointerDown={(event) => { event.preventDefault(); event.stopPropagation(); handlePanelPointerDown(event, index, "left"); }} />
                            <line className="panel-resize-handle panel-resize-handle--horizontal" data-resize-edge="right"
                              x1={x + width} x2={x + width} y1={y} y2={y + height}
                              aria-label="Resize ending station" role="slider"
                              onPointerDown={(event) => { event.preventDefault(); event.stopPropagation(); handlePanelPointerDown(event, index, "right"); }} />
                            <line className="panel-resize-handle panel-resize-handle--vertical" data-resize-edge="top"
                              x1={x} x2={x + width} y1={y} y2={y}
                              aria-label="Resize ending elevation" role="slider"
                              onPointerDown={(event) => { event.preventDefault(); event.stopPropagation(); handlePanelPointerDown(event, index, "top"); }} />
                            <line className="panel-resize-handle panel-resize-handle--vertical" data-resize-edge="bottom"
                              x1={x} x2={x + width} y1={y + height} y2={y + height}
                              aria-label="Resize starting elevation" role="slider"
                              onPointerDown={(event) => { event.preventDefault(); event.stopPropagation(); handlePanelPointerDown(event, index, "bottom"); }} />
                            <rect className="panel-resize-grip panel-resize-grip--horizontal" x={x - 0.75} y={centerY - 2} width="1.5" height="4" />
                            <rect className="panel-resize-grip panel-resize-grip--horizontal" x={x + width - 0.75} y={centerY - 2} width="1.5" height="4" />
                            <rect className="panel-resize-grip panel-resize-grip--vertical" x={centerX - 2} y={y - 0.75} width="4" height="1.5" />
                            <rect className="panel-resize-grip panel-resize-grip--vertical" x={centerX - 2} y={y + height - 0.75} width="4" height="1.5" />
                          </g>
                        )}
                        <text x={centerX} y={Math.max(preview.plot.top + 6, centerY - 2)} fontSize="3.0" textAnchor="middle" fill="#1d2d40" fontWeight="500">{panel.display_id ?? panel.panel}</text>
                      </g>
                    );
                  })}
                </g>

                <g>
                  {preview.panelBoundaries.map((boundary) => (
                    <line
                      key={`boundary-${boundary.panelIndex}-${boundary.field}`}
                      x1={boundary.x}
                      x2={boundary.x}
                      y1={preview.plot.top}
                      y2={100 - preview.plot.bottom}
                      stroke="#1d2d40"
                      strokeWidth={0.45}
                      style={{ pointerEvents: "none", shapeRendering: "crispEdges" }}
                    />
                  ))}
                </g>

                <g clipPath="url(#panel-setup-drawing-clip)">
                  {preview.primaryPoints.map((points,i) => <polyline key={`wsel-${i}`} data-profile-segment="wsel" points={points} fill="none" stroke="#1d2d40" strokeWidth="0.6" />)}
                  {preview.invertPoints.map((points,i) => <polyline key={`ground-${i}`} points={points} fill="none" stroke="#4b5563" strokeWidth="0.4" strokeDasharray="2 2" />)}
                </g>

                <g clipPath="url(#panel-setup-drawing-clip)">
                  {preview.xTicks.map((tick) => (
                    <line key={`gridx-${tick.value}`} x1={tick.x} x2={tick.x} y1={preview.plot.top} y2={100 - preview.plot.bottom} stroke="#eef2f6" strokeWidth="0.15" />
                  ))}
                  {preview.yTicks.map((tick) => (
                    <line key={`gridy-${tick.value}`} x1={preview.plot.left} x2={100 - preview.plot.right} y1={tick.y} y2={tick.y} stroke="#eef2f6" strokeWidth="0.15" />
                  ))}
                </g>

                <g clipPath="url(#panel-setup-drawing-clip)">
                  {preview.crossSections.map((marker, index) => (
                    <line key={`xs-${marker.label ?? index}`} x1={marker.x} x2={marker.x} y1={100 - preview.plot.bottom} y2={100 - preview.plot.bottom + 2.2} stroke="#475569" strokeWidth="0.2" />
                  ))}
                </g>

                <g clipPath="url(#panel-setup-drawing-clip)">
                  <line x1={preview.plot.left} x2={100 - preview.plot.right} y1={100 - preview.plot.bottom} y2={100 - preview.plot.bottom} stroke="#475569" strokeWidth="0.35" />
                  <line x1={preview.plot.left} x2={preview.plot.left} y1={preview.plot.top} y2={100 - preview.plot.bottom} stroke="#475569" strokeWidth="0.35" />
                  <text x={preview.plot.left + 1} y={100 - preview.plot.bottom + 3.2} textAnchor="start" fontSize="2.6" fill="#374151">Station: {formatDisplayNumber(cursorPos.station)}</text>
                  <text x={preview.plot.left + 1} y={100 - preview.plot.bottom + 6.2} textAnchor="start" fontSize="2.6" fill="#374151">Elevation: {formatDisplayNumber(cursorPos.elevation)}</text>
                </g>
              </svg>
            ) : (
              <div className="panel-setup-empty">No profile data available.</div>
            )}
          </div>
        </div>

        <div className="panel-setup-footer">
          <div className="panel-setup-status">
            {error ? <span className="panel-setup-error">{error}</span> : status || "Ready"}
          </div>
          <div className="panel-setup-footer-actions">
            <button type="button" className="ghost" onClick={onClose} disabled={isLoading}>Cancel</button>
            <button type="button" onClick={handleApply} disabled={isLoading}>Apply</button>
          </div>
        </div>
      </div>
    </div>
  );
}
