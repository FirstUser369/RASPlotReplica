import Plot from "react-plotly.js";
import { useEffect, useState } from "react";
import type { GetProfileResponse, PanelInput } from "../api/client";
import { panelOverlayTraces } from "../panelVisualization";

type Props = {
  profile: GetProfileResponse;
  selectedXsId: string | null;
  onSelectXs: (xsId: string) => void;
  landmarks?: Array<{ distance: number; elevation: number; text: string; orientation?: string }>;
  onAddLandmark?: (distance: number, elevation: number) => void;
  startStation?: number | null;
  endStation?: number | null;
  startElevation?: number | null;
  endElevation?: number | null;
  revisionStart?: number | null;
  revisionEnd?: number | null;
  panels?: PanelInput[];
  panelsStale?: boolean;
};

const PROFILE_COLORS = [
  "#196bd6",
  "#c1121f",
  "#2a9d8f",
  "#e9c46a",
  "#6a4c93",
  "#f4a261",
  "#264653",
  "#e76f51",
];

const displayNumber = (value: number) => String(Number(value.toFixed(2)));

export default function ProfileChart({
  profile, selectedXsId, onSelectXs, landmarks = [], onAddLandmark,
  startStation = null,
  endStation = null,
  startElevation = null,
  endElevation = null,
  revisionStart = null,
  revisionEnd = null,
  panels = [],
  panelsStale = false,
}: Props) {
  const [showPanels, setShowPanels] = useState(false);
  const [selectedPanel, setSelectedPanel] = useState<number | null>(null);
  // Keep Plotly bounded independently of the surrounding page height. Measuring
  // this element creates a feedback loop because Plotly contributes to its height.
  const chartHeight = 520;
  useEffect(() => {
    if (selectedPanel != null && !panels.some((panel) => panel.panel === selectedPanel)) setSelectedPanel(null);
  }, [panels, selectedPanel]);
  const xs = profile.cross_sections;
  const cumdist = xs.map((c) => c.cumdist);
  const minchel = xs.map((c) => c.minchel);
  const invertVals = minchel.filter((v): v is number => v != null && Number.isFinite(v));
  const invFloor = invertVals.length ? Math.min(...invertVals) - 2 : 0;

  const selectedNames =
    profile.profiles_selected?.length
      ? profile.profiles_selected
      : [profile.profile];

  const traces: any[] = [];
  selectedNames.forEach((name, i) => {
    const y = xs.map((c) => c.wsel_by_profile ? (c.wsel_by_profile[name] ?? null) : (name === profile.profile ? c.wsel : null));
    const meta = profile.profile_metadata?.find((p) => p.profile_id === name);
    traces.push({
      connectgaps: false,
      x: cumdist,
      y,
      mode: "lines+markers",
      name: `WSEL: ${meta?.legend_text || name}`,
      line: { color: meta?.color || PROFILE_COLORS[i % PROFILE_COLORS.length], dash: meta?.linetype === "DASHED" ? "dash" : meta?.linetype === "DOTTED" ? "dot" : meta?.linetype === "DASHDOT" ? "dashdot" : "solid", width: (meta?.lineweight ?? .3) * 4 },
      marker: { size: i === 0 ? 4 : 3 },
      text: xs.map((c) => `RS ${c.rs}${c.label_letter ? ` (${c.label_letter})` : ""}`),
    });
  });

  // channel invert trace filtered by visible domain
  traces.push({
    x: cumdist,
    y: minchel,
    mode: "lines",
    name: "Channel Invert (MINCHEL)",
    line: { color: "#333", width: 1, dash: "dot" },
    hoverinfo: "skip",
  });
  if (showPanels) traces.push(...panelOverlayTraces(panels, selectedPanel, panelsStale));

  const shapes: any[] = [];
  const annotations: any[] = [];
  const backwater = selectedNames.map(name => profile.profile_metadata?.find(p => p.profile_id === name)).find(p => p?.backwater_elev != null);
  if (backwater?.backwater_elev != null) {
    shapes.push({type:"line",xref:"paper",x0:0,x1:1,y0:backwater.backwater_elev,y1:backwater.backwater_elev,line:{color:"red",dash:"dash"}});
    annotations.push({xref:"paper",x:0,y:backwater.backwater_elev,text:`User-entered backwater reference (extent not calculated): ${backwater.backwater_text || ""}`,showarrow:false,xanchor:"left",yanchor:"bottom",font:{size:10,color:"red"}});
  }

  if (revisionStart != null && revisionEnd != null && revisionStart < revisionEnd) {
    shapes.push({
      type: "rect",
      x0: revisionStart,
      x1: revisionEnd,
      y0: 0,
      y1: 1,
      yref: "paper",
      fillcolor: "rgba(25, 107, 214, 0.08)",
      line: { color: "#196bd6", width: 1.5, dash: "dash" },
      layer: "below",
    });
    annotations.push({
      x: revisionStart,
      y: 1,
      yref: "paper",
      text: "Active revision",
      showarrow: false,
      xanchor: "left",
      yanchor: "bottom",
      font: { size: 10, color: "#196bd6" },
    });
  }

  // Letter labels (above invert or WSEL)
  for (const c of xs) {
    if (!c.label_letter) continue;
    const y = (c.wsel != null && Number.isFinite(c.wsel) ? c.wsel : c.minchel) ?? 0;
    annotations.push({
      x: c.cumdist,
      y,
      text: c.label_letter,
      showarrow: false,
      yanchor: "bottom",
      font: { size: 11, color: "#111", family: "Georgia, serif" },
      bgcolor: "rgba(255,255,255,0.65)",
      xanchor: "center",
      yshift: 8,
    });
  }

  const structureStart = Math.max(startStation ?? -Infinity, revisionStart ?? -Infinity);
  const structureEnd = Math.min(endStation ?? Infinity, revisionEnd ?? Infinity);
  const structureWarnings = (profile.structure_models ?? []).filter((s) =>
    s.status !== "VALIDATED" && (!s.bounds || (s.bounds[1] >= structureStart && s.bounds[0] <= structureEnd)));
  for (const s of profile.structures) {
    // The backend supplies source-derived faces. Never infer a station, width,
    // elevation or hydraulic value from a nearby cross section.
    if (s.support_status !== "VALIDATED" || s.geometry_role !== "FACE") continue;
    const x = s.cumdist;
    if (x == null || x < structureStart || x > structureEnd) continue;
    if (s.minchel == null || s.low_chord == null || s.high_chord == null) continue;
    shapes.push({type:"line", x0:x, x1:x, y0:s.minchel, y1:s.high_chord,
                 line:{color:"#8a6d3b",width:1}});
    shapes.push({type:"line", x0:x, x1:x, y0:s.low_chord, y1:s.high_chord,
                 line:{color:"#8a6d3b",width:2}});
    annotations.push({x, y:s.high_chord, text:`${s.type} ${s.rs}`, showarrow:false,
                      yanchor:"bottom",textangle:-90,font:{size:9,color:"#5c4a1f"}});
  }

  // Landmarks from session DB (filtered)
  for (const lm of landmarks) {
    const d = Number(lm.distance);
    if ((startStation != null && d < startStation) || (endStation != null && d > endStation)) continue;
    annotations.push({
      x: lm.distance,
      y: lm.elevation,
      text: lm.text || "•",
      showarrow: false,
      textangle: (lm.orientation || "Vertical").toLowerCase().startsWith("v") ? -90 : 0,
      font: { size: 10, color: "#333" },
      xanchor: "left",
      yanchor: "bottom",
    });
  }

  if (selectedXsId) {
    const sel = xs.find((c) => c.xs_id === selectedXsId);
    if (sel) {
      const d = Number(sel.cumdist);
      if ((startStation == null || d >= startStation) && (endStation == null || d <= endStation)) {
        shapes.push({
          type: "line",
          x0: sel.cumdist,
          x1: sel.cumdist,
          y0: 0,
          y1: 1,
          yref: "paper",
          line: { color: "#c1121f", width: 1, dash: "dot" },
        });
      }
    }
  }

  const hasElevationRange =
    startElevation != null && endElevation != null &&
    Number.isFinite(startElevation) && Number.isFinite(endElevation) &&
    startElevation < endElevation;

  const layout: any = {
    title: {
      text: `Flood Profile — ${profile.reach.river} \\ ${profile.reach.reach}`,
      font: { size: 14 },
    },
    xaxis: {
      title: "Cumulative Distance (ft)",
      gridcolor: "#eee",
      range: [(startStation ?? Math.min(...cumdist.filter((d) => Number.isFinite(d)))), (endStation ?? Math.max(...cumdist.filter((d) => Number.isFinite(d))))],
      autorange: false,
    },
    yaxis: {
      title: `Elevation (${profile.vertical_datum || "UNKNOWN"}, ${profile.working_units === "INTERNAL_US_CUSTOMARY" || profile.source_units === "US Customary" || profile.source_units === "INTERNAL_US_CUSTOMARY" ? "working ft" : "units UNKNOWN"})`,
      gridcolor: "#eee",
      ...(hasElevationRange ? { range: [startElevation, endElevation], autorange: false } : {}),
    },
    legend: { orientation: "h", y: -0.2 },
    height: chartHeight,
    margin: { l: 60, r: 20, t: 40, b: 60 },
    shapes,
    annotations,
  };

  return (
    <div className="profile-chart-surface">
    {profile.hydraulic_preflight && [...profile.hydraulic_preflight.errors, ...profile.hydraulic_preflight.warnings].length > 0 && (
      <details className="profile-chart-notice">
        <summary>Hydraulic validation messages ({[...profile.hydraulic_preflight.errors, ...profile.hydraulic_preflight.warnings].length})</summary>
        {[...profile.hydraulic_preflight.errors, ...profile.hydraulic_preflight.warnings].map((message, i) => <p role="status" key={`hydraulic-${i}`}>{message}</p>)}
      </details>
    )}
    {structureWarnings.length > 0 && <details className="profile-chart-notice profile-chart-notice--warning">
      <summary>Structure engineering validation required; affected PDF/DXF export is blocked ({structureWarnings.length})</summary>
      {structureWarnings.map((s) => <p role="status" key={s.identity}>{s.river} / {s.reach} / RS {s.rs} / {s.type}: {s.reason}</p>)}
    </details>}
    <div className="panel-overlay-controls" aria-label="Saved panel overlays">
      <label>
        <input type="checkbox" checked={showPanels} onChange={(event) => setShowPanels(event.target.checked)} />
        Show Panels
      </label>
      {showPanels && panelsStale && <strong className="panel-overlay-review">Review required</strong>}
      {showPanels && panels.length === 0 && <span>No saved panels</span>}
      {showPanels && panels.map((panel, index) => (
        <button key={panel.panel} type="button" className={selectedPanel === panel.panel ? "selected" : ""}
          aria-pressed={selectedPanel === panel.panel}
          onClick={() => setSelectedPanel(current => current === panel.panel ? null : panel.panel)}>
          P{panel.panel} ({index + 1}/{panels.length})
        </button>
      ))}
      {showPanels && selectedPanel != null && (() => {
        const panel = panels.find(item => item.panel === selectedPanel);
        if (!panel) return null;
        return <div className="panel-overlay-details" role="status">
          <strong>Panel {panel.panel}</strong>
          <span>Station {displayNumber(panel.start_station)}–{displayNumber(panel.end_station)} ft</span>
          <span>Elevation {displayNumber(panel.start_elevation)}–{displayNumber(panel.end_elevation)} ft</span>
          <span>H 1&quot;={panel.h_scale ?? "?"} ft · V 1&quot;={panel.v_scale ?? "?"} ft</span>
          <button type="button" onClick={() => setSelectedPanel(null)}>Deselect</button>
        </div>;
      })()}
    </div>
    <Plot
      data={traces}
      layout={layout}
      config={{ displaylogo: false, responsive: true, doubleClick: false }}
      onClick={(ev: any) => {
        const pts = ev?.points ?? [];
        const p = pts[0];
        const panelMeta = p?.data?.meta;
        if (panelMeta?.rasplotPanel) {
          setSelectedPanel(Number(panelMeta.panelId));
          return;
        }
        if (ev?.event?.shiftKey && onAddLandmark && p && p.x != null && p.y != null) {
          onAddLandmark(Number(p.x), Number(p.y));
          return;
        }
        if (p != null && p.pointNumber != null && xs[p.pointNumber]) {
          onSelectXs(xs[p.pointNumber].xs_id);
        }
      }}
      style={{ width: "100%", height: "100%" }}
    />
    </div>
  );
}
