import type { PanelInput } from "./api/client";

export type PanelRelationship = {
  fromPanel: number;
  toPanel: number;
  fromIndex: number;
  toIndex: number;
  kind: "SHARED" | "OVERLAP" | "GAP";
  start: number;
  end: number;
  verticalOffset: boolean;
};

export type PanelResizeEdge = "left" | "right" | "top" | "bottom";

export type PanelResizeLimits = {
  maxStationSpan: number;
  maxElevationSpan: number;
};

export function resizePanelEdge(
  panel: PanelInput,
  edge: PanelResizeEdge,
  coordinate: number,
  limits: PanelResizeLimits,
): PanelInput {
  const values = [
    panel.start_station, panel.end_station, panel.start_elevation, panel.end_elevation,
    coordinate, limits.maxStationSpan, limits.maxElevationSpan,
  ];
  if (!values.every(Number.isFinite) || limits.maxStationSpan <= 0 || limits.maxElevationSpan <= 0) {
    throw new Error("Panel resize coordinates and physical limits must be finite and positive.");
  }

  const resized = { ...panel };
  if (edge === "left") resized.start_station = coordinate;
  else if (edge === "right") resized.end_station = coordinate;
  else if (edge === "top") resized.end_elevation = coordinate;
  else resized.start_elevation = coordinate;

  const stationSpan = resized.end_station - resized.start_station;
  const elevationSpan = resized.end_elevation - resized.start_elevation;
  if (stationSpan <= 0) throw new Error("Ending Station must be greater than Starting Station.");
  if (elevationSpan <= 0) throw new Error("Ending Elevation must be greater than Starting Elevation.");
  if (stationSpan > limits.maxStationSpan + 1e-9) {
    throw new Error(`Panel station span exceeds the ${limits.maxStationSpan}-foot physical page capacity.`);
  }
  if (elevationSpan > limits.maxElevationSpan + 1e-9) {
    throw new Error(`Panel elevation span exceeds the ${limits.maxElevationSpan}-foot physical page capacity.`);
  }
  return resized;
}

export function translatePanel(panel: PanelInput, stationDelta: number, elevationDelta: number): PanelInput {
  if (![stationDelta, elevationDelta].every(Number.isFinite)) {
    throw new Error("Panel movement must use finite station and elevation offsets.");
  }
  const values = [panel.start_station, panel.end_station, panel.start_elevation, panel.end_elevation];
  if (!values.every(Number.isFinite) || panel.end_station <= panel.start_station || panel.end_elevation <= panel.start_elevation) {
    throw new Error(`Panel ${panel.panel} has invalid bounds and cannot be moved.`);
  }
  return {
    ...panel,
    start_station: panel.start_station + stationDelta,
    end_station: panel.end_station + stationDelta,
    start_elevation: panel.start_elevation + elevationDelta,
    end_elevation: panel.end_elevation + elevationDelta,
  };
}

export function panelRelationships(panels: PanelInput[], tolerance = 1e-7): PanelRelationship[] {
  return panels.slice(0, -1).map((panel, index) => {
    const next = panels[index + 1];
    const delta = next.start_station - panel.end_station;
    const kind = Math.abs(delta) <= tolerance ? "SHARED" : delta < 0 ? "OVERLAP" : "GAP";
    return {
      fromPanel: panel.panel,
      toPanel: next.panel,
      fromIndex: index,
      toIndex: index + 1,
      kind,
      start: kind === "OVERLAP" ? next.start_station : panel.end_station,
      end: kind === "OVERLAP" ? panel.end_station : next.start_station,
      verticalOffset: Math.abs(next.start_elevation - panel.start_elevation) > tolerance
        || Math.abs(next.end_elevation - panel.end_elevation) > tolerance,
    };
  });
}

export function panelOverlayTraces(panels: PanelInput[], selectedPanel: number | null, stale: boolean) {
  return panels.map((panel, index) => {
    const selected = panel.panel === selectedPanel;
    const status = stale ? "REVIEW REQUIRED" : "reviewed";
    return {
      type: "scatter",
      mode: "lines+text",
      x: [panel.start_station, panel.end_station, panel.end_station, panel.start_station, panel.start_station],
      y: [panel.start_elevation, panel.start_elevation, panel.end_elevation, panel.end_elevation, panel.start_elevation],
      fill: "toself",
      fillcolor: selected ? "rgba(25,107,214,0.22)" : stale ? "rgba(217,119,6,0.10)" : "rgba(25,107,214,0.08)",
      line: { color: selected ? "#0b4fa3" : stale ? "#a15c00" : "#196bd6", width: selected ? 3 : 1.5, dash: stale ? "dash" : "solid" },
      text: ["", "", `P${panel.panel}`, "", ""],
      textposition: "top center",
      textfont: { color: stale ? "#7a4300" : "#123d6b", size: 11 },
      name: `Panel ${panel.panel}${stale ? " — REVIEW REQUIRED" : ""}`,
      showlegend: false,
      hoveron: "fills+points",
      hovertemplate: `Panel ${panel.panel} (${index + 1} of ${panels.length})<br>`
        + `Station ${panel.start_station}–${panel.end_station} ft<br>`
        + `Elevation ${panel.start_elevation}–${panel.end_elevation} ft<br>`
        + `H 1\"=${panel.h_scale ?? "?"} ft · V 1\"=${panel.v_scale ?? "?"} ft<br>${status}<extra></extra>`,
      meta: { rasplotPanel: true, panelId: panel.panel },
    };
  });
}
