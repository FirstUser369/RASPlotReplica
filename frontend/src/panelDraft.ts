export const PANEL_NUMERIC_FIELDS = [
  "start_station",
  "end_station",
  "start_elevation",
  "end_elevation",
] as const;

export type PanelNumericField = typeof PANEL_NUMERIC_FIELDS[number];
export type PanelNumericInputs = Record<string, string>;

export type EditablePanel = {
  panel: number;
  start_station: number;
  end_station: number;
  start_elevation: number;
  end_elevation: number;
};

export function formatPanelDraftNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "";
  return String(Number(Number(value).toFixed(2)));
}

export function panelNumericInputKey(index: number, field: PanelNumericField) {
  return `${index}:${field}`;
}

export function createPanelNumericInputs(panels: EditablePanel[]): PanelNumericInputs {
  const inputs: PanelNumericInputs = {};
  panels.forEach((panel, index) => {
    PANEL_NUMERIC_FIELDS.forEach((field) => {
      inputs[panelNumericInputKey(index, field)] = formatPanelDraftNumber(panel[field]);
    });
  });
  return inputs;
}

export function parseFinitePanelInput(raw: string, label: string) {
  if (raw.trim() === "") return { ok: false as const, error: `${label} is required.` };
  const value = Number(raw);
  if (!Number.isFinite(value)) return { ok: false as const, error: `${label} must be a finite number.` };
  return { ok: true as const, value };
}

export function validatePanelGeometry(panel: EditablePanel): string | null {
  const values: Array<[string, number]> = [
    ["Starting Station", panel.start_station],
    ["Ending Station", panel.end_station],
    ["Starting Elevation", panel.start_elevation],
    ["Ending Elevation", panel.end_elevation],
  ];
  const invalid = values.find(([, value]) => !Number.isFinite(value));
  if (invalid) return `Panel ${panel.panel}: ${invalid[0]} must be a finite number.`;
  if (panel.end_station <= panel.start_station) {
    return `Panel ${panel.panel}: Ending Station must be greater than Starting Station.`;
  }
  if (panel.end_elevation <= panel.start_elevation) {
    return `Panel ${panel.panel}: Ending Elevation must be greater than Starting Elevation.`;
  }
  return null;
}

export function materializePanelNumericInputs<T extends EditablePanel>(
  panels: T[],
  inputs: PanelNumericInputs,
): { ok: true; panels: T[] } | { ok: false; error: string } {
  const next = panels.map((panel) => ({ ...panel }));
  for (let index = 0; index < next.length; index += 1) {
    for (const field of PANEL_NUMERIC_FIELDS) {
      const key = panelNumericInputKey(index, field);
      const parsed = parseFinitePanelInput(inputs[key] ?? "", `${field.replaceAll("_", " ")} for Panel ${next[index].panel}`);
      if (!parsed.ok) return { ok: false, error: parsed.error };
      next[index][field] = parsed.value;
    }
    const geometryError = validatePanelGeometry(next[index]);
    if (geometryError) return { ok: false, error: geometryError };
  }
  return { ok: true, panels: next };
}

export function synchronizePanelNumericInputs(
  current: PanelNumericInputs,
  panels: EditablePanel[],
  indexes?: number[],
) {
  if (!indexes) return createPanelNumericInputs(panels);
  const next = { ...current };
  for (const index of indexes) {
    const panel = panels[index];
    if (!panel) continue;
    PANEL_NUMERIC_FIELDS.forEach((field) => {
      next[panelNumericInputKey(index, field)] = formatPanelDraftNumber(panel[field]);
    });
  }
  return next;
}
