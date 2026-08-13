// API client for RAS-Plot Replica backend.

export type ProfileMeta = {
  name: string;
  frequency: string;
  backwater_elev: number | null;
  backwater_text: string;
  selected?: boolean;
  color?: string;
  linetype?: string;
  lineweight?: number;
  legend_text?: string;
  layer?: string;
};

export type OpenResponse = {
  session_id: string;
  flow_type: string;
  name: string;
  reaches: any[];
  profiles: { name: string; wsel: number[] }[];
  db?: any;
};

export type GetProfileResponse = {
  session_id: string;
  reach: { river: string; reach: string };
  profile: string;
  profiles_selected?: string[];
  profile_names: string[];
  cross_sections: Array<{
    rs: string;
    river: string;
    reach: string;
    length_channel: number;
    cumdist: number;
    minchel: number | null;
    wsel: number | null;
    label_letter: string | null;
    label_text: string | null;
    wsel_by_profile?: Record<string, number | null>;
  }>;
  structures: Array<{
    river: string;
    reach: string;
    rs: string;
    type: string;
    name: string;
    weir_width: number;
    low_chord: number | null;
    high_chord: number | null;
    minchel: number | null;
    cumdist?: number | null;
  }>;
};

export type ExportOptions = {
  format: "pdf" | "dxf";
  profile_names?: string[];
  page_size?: string;
  custom_page_w?: number;
  custom_page_h?: number;
  h_scale?: number;
  v_scale?: number;
  distance_unit?: string;
  vertical_datum?: string;
  stream_name?: string;
  community?: string;
  county?: string;
  state?: string;
  start_station_description?: string;
  panel_no?: number;
  dxf_version?: string;
  include_grid?: boolean;
  include_title_block?: boolean;
  use_current_view?: boolean;
  fit_to_one_panel?: boolean;
  single_panel?: boolean;
};

export type ExportResponse = { path: string; size_bytes: number; format?: string };
export type AppSettings = {
  host?: string;
  port?: number;
  last_plan_path?: string;
  default_fis?: Record<string, any>;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const body = await r.json();
      if (body?.detail)
        msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  getSettings: () => req<AppSettings>("/api/settings"),
  openSource: (path: string) =>
    req<OpenResponse>("/api/source", { method: "POST", body: JSON.stringify({ path }) }),
  getProfile: (sid: string, reachIdx: number, profiles: string | string[]) => {
    const list = Array.isArray(profiles) ? profiles : [profiles];
    const q = new URLSearchParams({
      reach_idx: String(reachIdx),
      profiles: list.filter(Boolean).join(","),
    });
    return req<GetProfileResponse>(`/api/profile/${sid}?${q.toString()}`);
  },
  getMap: (sid: string) => req<GeoJSON.FeatureCollection>(`/api/map/${sid}`),
  getDb: (sid: string) => req<any>(`/api/db/${sid}`),
  patchWorking: (sid: string, patches: any[]) =>
    req<any>(`/api/db/${sid}/working`, { method: "PATCH", body: JSON.stringify(patches) }),
  fixDrawdowns: (sid: string) =>
    req<any>(`/api/db/${sid}/fix-drawdowns`, { method: "POST", body: "{}" }),
  reletter: (sid: string, start_row_id: number, start_letter: string) =>
    req<any>(`/api/db/${sid}/reletter`, {
      method: "POST",
      body: JSON.stringify({ start_row_id, start_letter }),
    }),
  recalcCumdist: (sid: string, start = 0) =>
    req<any>(`/api/db/${sid}/recalc-cumdist`, {
      method: "POST",
      body: JSON.stringify({ start }),
    }),
  autoLetter: (sid: string, every = 5) =>
    req<any>(`/api/db/${sid}/auto-letter?every=${every}`, { method: "POST", body: "{}" }),
  nodeNames: (sid: string, enabled: boolean) =>
    req<any>(`/api/db/${sid}/node-names`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  putHeader: (sid: string, body: any) =>
    req<any>(`/api/db/${sid}/header`, { method: "PUT", body: JSON.stringify(body) }),
  putProfiles: (sid: string, updates: any[]) =>
    req<any>(`/api/db/${sid}/profiles`, { method: "PUT", body: JSON.stringify(updates) }),
  putPanels: (sid: string, body: any) =>
    req<any>(`/api/db/${sid}/panels`, { method: "PUT", body: JSON.stringify(body) }),
  putLandmarks: (sid: string, items: any[]) =>
    req<any>(`/api/db/${sid}/landmarks`, { method: "PUT", body: JSON.stringify(items) }),
  selectReach: (sid: string, body: any) =>
    req<any>(`/api/db/${sid}/select-reach`, { method: "POST", body: JSON.stringify(body) }),
  getPageSizes: () => req<any>("/api/report/page-sizes"),
  exportReport: (sid: string, opts: ExportOptions) =>
    req<ExportResponse>(`/api/report/export`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...opts }),
    }),
  buildFdt: (sid: string, body: any = {}) =>
    req<ExportResponse>(`/api/report/fdt.pdf`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...body }),
    }),
  buildFirm: (sid: string) =>
    req<ExportResponse>(`/api/report/firm.zip`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid }),
    }),
  buildReport: (sid: string, body: any) =>
    req<ExportResponse>(`/api/report/profile.pdf`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...body }),
    }),
  downloadUrl: (sid: string, path: string) =>
    `/api/source/${sid}/download?path=${encodeURIComponent(path)}`,
};
