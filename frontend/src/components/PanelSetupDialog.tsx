import { useEffect, useMemo, useRef, useState } from "react";
import { api, type GetProfileResponse } from "../api/client";

type DraftPanel = {
  panel: number;
  start_station: number;
  end_station: number;
  start_elevation: number;
  end_elevation: number;
  panel_x?: number;
  panel_y?: number;
  h_scale?: number;
  v_scale?: number;
};

type Props = {
  open: boolean;
  sessionId: string;
  profile: GetProfileResponse | null;
  onClose: () => void;
};

export default function PanelSetupDialog({ open, sessionId, profile, onClose }: Props) {
  const [draftPanels, setDraftPanels] = useState<DraftPanel[]>([]);
  const [hScale, setHScale] = useState(500);
  const [vScale, setVScale] = useState(5);
  const [pageSize, setPageSize] = useState("11x17");
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [fitVersion, setFitVersion] = useState(0);
  const [selectedPanelIndex, setSelectedPanelIndex] = useState<number | null>(null);
  const [dragState, setDragState] = useState<
    | { kind: "station"; boundaryType: "outer-start" | "inner" | "outer-end"; boundaryIndex: number }
    | null
  >(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [cursorPos, setCursorPos] = useState<{ station: number | null; elevation: number | null }>({ station: null, elevation: null });

  function formatDisplayNumber(value: number | null | undefined) {
    if (!Number.isFinite(Number(value))) return "";
    const numeric = Number(value);
    const rounded = Number(numeric.toFixed(2));
    return String(rounded);
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
    if (!open || !sessionId) return;
    let ignore = false;

    async function loadPanels() {
      try {
        setIsLoading(true);
        setError(null);
        const db = await api.getPanels(sessionId);
        const panels = (db.panels ?? []).map((p: any) => ({
          panel: Number(p.panel ?? 1),
          start_station: Number(p.start_station ?? 0),
          end_station: Number(p.end_station ?? 0),
          start_elevation: Number(p.start_elevation ?? 0),
          end_elevation: Number(p.end_elevation ?? 0),
          panel_x: Number(p.panel_x ?? 0),
          panel_y: Number(p.panel_y ?? 0),
          h_scale: Number(p.h_scale ?? 500),
          v_scale: Number(p.v_scale ?? 5),
        }));
        if (ignore) return;
        setDraftPanels(panels);
        const header = (await api.getDb(sessionId))?.header ?? {};
        setHScale(Number(header.h_scale ?? panels[0]?.h_scale ?? 500));
        setVScale(Number(header.v_scale ?? panels[0]?.v_scale ?? 5));
        setPageSize(String(header.page_size ?? "11x17"));
        setStatus(`${panels.length} panel${panels.length === 1 ? "" : "s"} loaded`);
      } catch (e: any) {
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
  }, [open, sessionId]);

  const preview = useMemo(() => {
    if (!profile || !draftPanels.length) return null;

    const xs = profile.cross_sections ?? [];
    if (!xs.length) return null;

    const bounds = buildPreviewBounds(draftPanels);

    // available engineering domain (from profile cumdist)
    const cumdists = xs.map((r) => Number(r.cumdist ?? NaN)).filter((v) => Number.isFinite(v));
    const availableMinStation = cumdists.length ? Math.min(...cumdists) : bounds.xMinRaw ?? bounds.xMin;
    const availableMaxStation = cumdists.length ? Math.max(...cumdists) : bounds.xMaxRaw ?? bounds.xMax;

    // plot margins in viewBox units (0..100) - keep small to maximize usable area
    const plot: { left: number; right: number; top: number; bottom: number; width: number; height: number } = {
      left: 6,
      right: 4,
      top: 5,
      bottom: 8,
      width: 100 - 6 - 4,
      height: 100 - 5 - 8,
    };

    // view domain: show full available profile extent (not just panel extents)
    const viewXMin = availableMinStation;
    const viewXMax = availableMaxStation;
    const viewYMin = bounds.yMin;
    const viewYMax = bounds.yMax;

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

    const primaryPoints = xs.map((row) => {
      const x = Number(row.cumdist ?? NaN);
      const yValue = Number(
        row.wsel ??
        (row.wsel_by_profile && Object.values(row.wsel_by_profile).find((v): v is number => typeof v === "number" && Number.isFinite(v))) ??
        row.minchel ??
        NaN,
      );
      if (!Number.isFinite(x) || !Number.isFinite(yValue)) return null;
      return `${stationToX(x)},${elevationToY(yValue)}`;
    }).filter((p): p is string => p !== null);

    const invertPoints = xs.map((row) => {
      const x = Number(row.cumdist ?? NaN);
      const y = Number(row.minchel ?? NaN);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return `${stationToX(x)},${elevationToY(y)}`;
    }).filter((p): p is string => p !== null);

    const xTicks = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      const value = viewXMin + ratio * (viewXMax - viewXMin);
      return { value, x: stationToX(value) };
    });

    const yTicks = Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      const value = viewYMin + (1 - ratio) * (viewYMax - viewYMin);
      return { value, y: elevationToY(value) };
    });

    const crossSections = xs.map((row) => {
      const x = Number(row.cumdist ?? NaN);
      if (!Number.isFinite(x)) return null;
      return { x: stationToX(x), label: row.rs ?? "XS" };
    }).filter((p): p is { x: number; label: string } => p !== null);

    // boundary values array: [P0.start, P0.end, P1.end, ..., Pn.end]
    const boundaryValues: number[] = [draftPanels[0].start_station, ...draftPanels.map((p) => p.end_station)];
    const majorStationBoundaries = boundaryValues.map((value, idx) => ({
      kind: idx === 0 ? ("outer-start" as const) : idx === boundaryValues.length - 1 ? ("outer-end" as const) : ("inner" as const),
      boundaryIndex: idx,
      x: stationToX(value),
      value,
    }));

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
      majorStationBoundaries,
      boundaryValues,
    };
  }, [draftPanels, fitVersion, profile]);

  function applyStationBoundaryUpdate(
    panels: DraftPanel[],
    boundaryType: "outer-start" | "inner" | "outer-end",
    panelIndex: number,
    value: number,
  ) {
    const nextPanels = panels.map((panel) => ({ ...panel }));
    const minWidth = 10;

    if (boundaryType === "outer-start") {
      const maxAllowed = nextPanels[0].end_station - minWidth;
      nextPanels[0].start_station = Math.min(value, maxAllowed);
      return nextPanels;
    }

    if (boundaryType === "outer-end") {
      const minAllowed = Math.min(nextPanels[nextPanels.length - 1].end_station, nextPanels[nextPanels.length - 1].start_station + minWidth);
      nextPanels[nextPanels.length - 1].end_station = Math.max(value, minAllowed);
      return nextPanels;
    }

    const leftPanel = nextPanels[panelIndex];
    const rightPanel = nextPanels[panelIndex + 1];
    const minAllowed = leftPanel.start_station + minWidth;
    const maxAllowed = rightPanel.end_station - minWidth;
    const clamped = Math.min(Math.max(value, minAllowed), maxAllowed);
    leftPanel.end_station = clamped;
    rightPanel.start_station = clamped;
    return nextPanels;
  }

  function updatePanel(index: number, field: keyof DraftPanel, rawValue: string) {
    if (rawValue.trim() === "") return;
    const numeric = Number(rawValue);
    if (!Number.isFinite(numeric)) return;

    setDraftPanels((prev) => {
      const next = prev.map((panel) => ({ ...panel }));
      const target = Number(numeric);
      const minWidth = 10;

      if (field === "panel") {
        next[index].panel = Number(rawValue) || 1;
        return next;
      }

      if (field === "start_station") {
        next[index].start_station = target;
        if (index > 0) {
          next[index - 1].end_station = target;
        }
        if (index < next.length - 1) {
          const minAllowed = next[index].start_station + minWidth;
          if (next[index].end_station < minAllowed) next[index].end_station = minAllowed;
          next[index + 1].start_station = next[index].end_station;
        }
        return next;
      }

      if (field === "end_station") {
        next[index].end_station = target;
        if (index < next.length - 1) {
          next[index + 1].start_station = target;
        }
        const minAllowed = next[index].start_station + minWidth;
        if (next[index].end_station < minAllowed) {
          next[index].end_station = minAllowed;
          if (index < next.length - 1) next[index + 1].start_station = minAllowed;
        }
        return next;
      }

      next[index][field] = target as never;
      return next;
    });
    setError(null);
  }

  function validatePanels() {
    if (!draftPanels.length) return "No panel definitions are available.";

    for (const panel of draftPanels) {
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
    }

    if (!Number.isFinite(Number(hScale)) || Number(hScale) <= 0) return "Horizontal Scale must be greater than 0.";
    if (!Number.isFinite(Number(vScale)) || Number(vScale) <= 0) return "Vertical Scale must be greater than 0.";

    return null;
  }

  async function reloadPanelsFromBackend() {
    const db = await api.getPanels(sessionId);
    const panels = (db.panels ?? []).map((p: any) => ({
      panel: Number(p.panel ?? 1),
      start_station: Number(p.start_station ?? 0),
      end_station: Number(p.end_station ?? 0),
      start_elevation: Number(p.start_elevation ?? 0),
      end_elevation: Number(p.end_elevation ?? 0),
      panel_x: Number(p.panel_x ?? 0),
      panel_y: Number(p.panel_y ?? 0),
      h_scale: Number(p.h_scale ?? 500),
      v_scale: Number(p.v_scale ?? 5),
    }));
    setDraftPanels(panels);
    setStatus(`${panels.length} panel${panels.length === 1 ? "" : "s"} refreshed`);
  }

  async function handleApply() {
    const msg = validatePanels();
    if (msg) {
      setError(msg);
      return;
    }

    setError(null);
    setIsLoading(true);
    try {
      const payload = {
        panels: draftPanels.map((p) => ({
          panel: Number(p.panel),
          start_station: Number(p.start_station),
          end_station: Number(p.end_station),
          start_elevation: Number(p.start_elevation),
          end_elevation: Number(p.end_elevation),
          panel_x: Number(p.panel_x ?? 0),
          panel_y: Number(p.panel_y ?? 0),
          h_scale: Number(hScale),
          v_scale: Number(vScale),
        })),
        auto: false,
        h_scale: Number(hScale),
        v_scale: Number(vScale),
        page_size: pageSize,
      };
      await api.putPanels(sessionId, payload);
      setStatus("Panel configuration applied");
      await reloadPanelsFromBackend();
      onClose();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setIsLoading(false);
    }
  }

  function handleRefreshFit() {
    setFitVersion((current) => current + 1);
    setStatus("Preview fit refreshed");
    setError(null);
  }

  async function handleAutoGenerate() {
    const hasDraftEdits = draftPanels.some((panel) => {
      const fields = ["start_station", "end_station", "start_elevation", "end_elevation"] as const;
      return fields.some((field) => Number(panel[field]) !== Number(panel[field]));
    });

    if (hasDraftEdits && !window.confirm("Auto Generate will replace the current panel boundaries. Continue?")) {
      return;
    }

    setError(null);
    setIsLoading(true);
    try {
      const payload = {
        panels: draftPanels.map((p) => ({
          panel: Number(p.panel ?? 1),
          start_station: Number(p.start_station ?? 0),
          end_station: Number(p.end_station ?? 0),
          start_elevation: Number(p.start_elevation ?? 0),
          end_elevation: Number(p.end_elevation ?? 0),
          panel_x: Number(p.panel_x ?? 0),
          panel_y: Number(p.panel_y ?? 0),
          h_scale: Number(hScale),
          v_scale: Number(vScale),
        })),
        auto: true,
        h_scale: Number(hScale),
        v_scale: Number(vScale),
        page_size: pageSize,
      };
      const res = await api.putPanels(sessionId, payload);
      const panels = (res.panels ?? []).map((p: any) => ({
        panel: Number(p.panel ?? 1),
        start_station: Number(p.start_station ?? 0),
        end_station: Number(p.end_station ?? 0),
        start_elevation: Number(p.start_elevation ?? 0),
        end_elevation: Number(p.end_elevation ?? 0),
        panel_x: Number(p.panel_x ?? 0),
        panel_y: Number(p.panel_y ?? 0),
        h_scale: Number(p.h_scale ?? hScale),
        v_scale: Number(p.v_scale ?? vScale),
      }));
      setDraftPanels(panels);
      setStatus(`${panels.length} auto-generated panel${panels.length === 1 ? "" : "s"}`);
    } catch (e: any) {
      console.error("Auto Generate failed:", e);
      setError("Unable to auto-generate panels. Please check the panel settings.");
    } finally {
      setIsLoading(false);
    }
  }

  // Pointer drag helpers using pointer capture and SVG coordinate transforms
  function getBoundaryValuesFromPanels(panels: DraftPanel[]) {
    return [panels[0].start_station, ...panels.map((p) => p.end_station)];
  }

  function handleBoundaryPointerDown(e: any, boundaryIndex: number, kind: "outer-start" | "inner" | "outer-end") {
    try {
      (e.currentTarget as Element).setPointerCapture((e.nativeEvent as PointerEvent).pointerId);
    } catch {}
    setDragState({ kind: "station", boundaryType: kind, boundaryIndex });
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

    const bvals = getBoundaryValuesFromPanels(draftPanels);
    const k = dragState.boundaryIndex;
    const minWidth = 10;
    let minAllowed = -Infinity;
    let maxAllowed = Infinity;

    if (k === 0) {
      minAllowed = preview.availableMinStation;
      maxAllowed = (bvals[1] ?? preview.availableMaxStation) - minWidth;
    } else if (k === bvals.length - 1) {
      minAllowed = (bvals[k - 1] ?? preview.availableMinStation) + minWidth;
      maxAllowed = preview.availableMaxStation;
    } else {
      minAllowed = (bvals[k - 1] ?? preview.availableMinStation) + minWidth;
      maxAllowed = (bvals[k + 1] ?? preview.availableMaxStation) - minWidth;
    }

    const allowed = Math.min(Math.max(stationRaw, minAllowed), maxAllowed);

    setDraftPanels((prev) => {
      const next = prev.map((p) => ({ ...p }));
      if (k === 0) {
        next[0].start_station = allowed;
      } else if (k === bvals.length - 1) {
        next[next.length - 1].end_station = allowed;
      } else {
        const leftIdx = k - 1;
        next[leftIdx].end_station = allowed;
        next[leftIdx + 1].start_station = allowed;
      }
      return next;
    });
  }

  function handleSvgPointerUp(e: any) {
    try {
      (e.currentTarget as Element).releasePointerCapture((e.nativeEvent as PointerEvent).pointerId);
    } catch {}
    setDragState(null);
  }

  

  if (!open) return null;

  return (
    <div className="panel-setup-backdrop" onClick={onClose}>
      <div className="panel-setup-modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-setup-header">
          <h3>PANEL SETUP</h3>
          <button type="button" className="panel-setup-close" onClick={onClose} aria-label="Close panel setup">×</button>
        </div>

        <div className="panel-setup-controls">
          <label>
            <span>Horizontal Scale</span>
            <div className="panel-setup-scale-input">
              <span>1&quot; =</span>
              <input type="number" value={hScale} min="0.01" step="0.1" onChange={(e) => setHScale(Number(e.target.value))} />
              <span>ft</span>
            </div>
          </label>

          <label>
            <span>Vertical Scale</span>
            <div className="panel-setup-scale-input">
              <span>1&quot; =</span>
              <input type="number" value={vScale} min="0.01" step="0.1" onChange={(e) => setVScale(Number(e.target.value))} />
              <span>ft</span>
            </div>
          </label>

          <div className="panel-setup-actions">
            <button type="button" onClick={handleAutoGenerate}>Auto Generate</button>
            <button type="button" onClick={handleRefreshFit}>Refresh / Fit</button>
          </div>
        </div>

        <div className="panel-setup-body">
          <div className="panel-setup-table-panel">
            <div className="panel-setup-table-wrap">
              <table className="panel-setup-table">
                <thead>
                  <tr>
                    <th>Panel</th>
                    <th>Starting Station</th>
                    <th>Ending Station</th>
                    <th>Starting Elevation</th>
                    <th>Ending Elevation</th>
                  </tr>
                </thead>
                <tbody>
                  {draftPanels.map((panel, index) => (
                    <tr key={panel.panel ?? index}>
                      <td>{panel.panel}</td>
                      <td>
                        <input
                          type="number"
                          step="0.01"
                          value={formatDisplayNumber(panel.start_station)}
                          onChange={(e) => updatePanel(index, "start_station", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          step="0.01"
                          value={formatDisplayNumber(panel.end_station)}
                          onChange={(e) => updatePanel(index, "end_station", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          step="0.01"
                          value={formatDisplayNumber(panel.start_elevation)}
                          onChange={(e) => updatePanel(index, "start_elevation", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          step="0.01"
                          value={formatDisplayNumber(panel.end_elevation)}
                          onChange={(e) => updatePanel(index, "end_elevation", e.target.value)}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel-setup-preview-panel">
            <div className="panel-setup-preview-header">Profile / Panel Preview</div>
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
                <rect x="0" y="0" width="100" height="100" fill="#f6f7f8" />

                <g>
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
                        />
                        <text x={centerX} y={Math.max(preview.plot.top + 6, centerY - 2)} fontSize="3.0" textAnchor="middle" fill="#1d2d40" fontWeight="500">P{panel.panel}</text>
                      </g>
                    );
                  })}
                </g>

                <g>
                  {preview.majorStationBoundaries.map((boundary) => (
                    <line
                      key={`boundary-${boundary.kind}-${boundary.boundaryIndex}`}
                      x1={boundary.x}
                      x2={boundary.x}
                      y1={preview.plot.top}
                      y2={100 - preview.plot.bottom}
                      stroke="#1d2d40"
                      strokeWidth={0.45}
                      style={{ cursor: "ew-resize", shapeRendering: "crispEdges" }}
                      onPointerDown={(event) => {
                        event.preventDefault();
                        handleBoundaryPointerDown(event, boundary.boundaryIndex, boundary.kind);
                      }}
                    />
                  ))}
                </g>

                <g>
                  <polyline points={preview.primaryPoints.join(" ")} fill="none" stroke="#1d2d40" strokeWidth="0.6" />
                  <polyline points={preview.invertPoints.join(" ")} fill="none" stroke="#4b5563" strokeWidth="0.4" strokeDasharray="2 2" />
                </g>

                <g>
                  {preview.xTicks.map((tick) => (
                    <line key={`gridx-${tick.value}`} x1={tick.x} x2={tick.x} y1={preview.plot.top} y2={100 - preview.plot.bottom} stroke="#eef2f6" strokeWidth="0.15" />
                  ))}
                  {preview.yTicks.map((tick) => (
                    <line key={`gridy-${tick.value}`} x1={preview.plot.left} x2={100 - preview.plot.right} y1={tick.y} y2={tick.y} stroke="#eef2f6" strokeWidth="0.15" />
                  ))}
                </g>

                <g>
                  {preview.crossSections.map((marker, index) => (
                    <line key={`xs-${marker.label ?? index}`} x1={marker.x} x2={marker.x} y1={100 - preview.plot.bottom} y2={100 - preview.plot.bottom + 2.2} stroke="#475569" strokeWidth="0.2" />
                  ))}
                </g>

                <g>
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
