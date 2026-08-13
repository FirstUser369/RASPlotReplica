import Plot from "react-plotly.js";
import type { GetProfileResponse } from "../api/client";

type Props = {
  profile: GetProfileResponse;
  selectedRs: string | null;
  onSelectRs: (rs: string) => void;
  landmarks?: Array<{ distance: number; elevation: number; text: string; orientation?: string }>;
  onAddLandmark?: (distance: number, elevation: number) => void;
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

function nearestCumdist(xs: GetProfileResponse["cross_sections"], rs: string): number | null {
  const target = parseFloat(rs);
  if (!Number.isFinite(target) || !xs.length) return null;
  let best = xs[0];
  let bestD = Math.abs(parseFloat(best.rs) - target);
  for (const c of xs) {
    const d = Math.abs(parseFloat(c.rs) - target);
    if (d < bestD) {
      best = c;
      bestD = d;
    }
  }
  return best.cumdist;
}

export default function ProfileChart({
  profile, selectedRs, onSelectRs, landmarks = [], onAddLandmark,
}: Props) {
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
    const y = xs.map((c) => c.wsel_by_profile?.[name] ?? (name === profile.profile ? c.wsel : null));
    traces.push({
      x: cumdist,
      y,
      mode: "lines+markers",
      name: `WSEL: ${name}`,
      line: { color: PROFILE_COLORS[i % PROFILE_COLORS.length], width: i === 0 ? 2.2 : 1.6 },
      marker: { size: i === 0 ? 4 : 3 },
      text: xs.map((c) => `RS ${c.rs}${c.label_letter ? ` (${c.label_letter})` : ""}`),
    });
  });

  traces.push({
    x: cumdist,
    y: minchel,
    mode: "lines",
    name: "Channel Invert (MINCHEL)",
    line: { color: "#333", width: 1, dash: "dot" },
    hoverinfo: "skip",
  });

  const shapes: any[] = [];
  const annotations: any[] = [];

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

  for (const s of profile.structures) {
    const x = s.cumdist != null ? s.cumdist : nearestCumdist(xs, s.rs);
    if (x == null) continue;

    const isBridgeLike = /bridge|culvert/i.test(s.type || "");
    const yHigh = s.high_chord;
    const yLow = s.low_chord;
    const yBot = s.minchel ?? invFloor;
    const wselNums = xs
      .map((c) => c.wsel)
      .filter((v): v is number => v != null && Number.isFinite(v));
    const yTop =
      yHigh ?? yLow ?? (wselNums.length ? Math.max(...wselNums) : yBot + 10);

    shapes.push({
      type: "line",
      x0: x,
      x1: x,
      y0: yBot,
      y1: yTop,
      line: { color: "#8a6d3b", width: 1.5 },
    });

    if (isBridgeLike && yLow != null && yHigh != null) {
      shapes.push({
        type: "rect",
        x0: x - 40,
        x1: x + 40,
        y0: yLow,
        y1: yHigh,
        fillcolor: "rgba(233, 196, 106, 0.55)",
        line: { color: "#8a6d3b", width: 1 },
      });
    } else if (yHigh != null || yLow != null) {
      const crest = yHigh ?? yLow!;
      shapes.push({
        type: "line",
        x0: x - 50,
        x1: x + 50,
        y0: crest,
        y1: crest,
        line: { color: "#8a6d3b", width: 2 },
      });
    }

    annotations.push({
      x,
      y: yTop,
      text: `${s.type || "STR"} ${s.rs}`,
      showarrow: false,
      yanchor: "bottom",
      textangle: -90,
      font: { size: 9, color: "#5c4a1f" },
      xanchor: "left",
    });
  }

  // Landmarks from session DB
  for (const lm of landmarks) {
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

  if (selectedRs) {
    const sel = xs.find((c) => c.rs === selectedRs);
    if (sel) {
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

  const layout: any = {
    title: {
      text: `Flood Profile — ${profile.reach.river} \\ ${profile.reach.reach}`,
      font: { size: 14 },
    },
    xaxis: {
      title: "Cumulative Distance (ft)",
      gridcolor: "#eee",
    },
    yaxis: { title: "Elevation (NAVD88, ft)", gridcolor: "#eee" },
    legend: { orientation: "h", y: -0.2 },
    height: Math.min(600, window.innerHeight - 120),
    margin: { l: 60, r: 20, t: 40, b: 60 },
    shapes,
    annotations,
  };

  return (
    <Plot
      data={traces}
      layout={layout}
      config={{ displaylogo: false, responsive: true, doubleClick: "reset" }}
      onClick={(ev: any) => {
        const pts = ev?.points ?? [];
        const p = pts[0];
        if (ev?.event?.shiftKey && onAddLandmark && p && p.x != null && p.y != null) {
          onAddLandmark(Number(p.x), Number(p.y));
          return;
        }
        if (p != null && p.pointNumber != null && xs[p.pointNumber]) {
          onSelectRs(xs[p.pointNumber].rs);
        }
      }}
      style={{ width: "100%", height: "100%" }}
    />
  );
}
