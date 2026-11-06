import { contextVersion, assertContext, contextHeaders, StaleResponse } from "./context";
// API client for RAS-Plot Replica backend.

export type ProfileMeta = {
  profile_id?: string;
  key?: string;
  result_type?: string;
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
  model_id?: string;
  model_context?: { model_id: string; generation: number; session_id: string; source_path: string; status: string };
  flow_type: string;
  name: string;
  reaches: any[];
  profiles: { name: string; profile_id: string; display_name?: string; wsel: (number | null)[] }[];
  db?: any;
  requires_reach_selection?: boolean;
};

export type FloodingSourceRequest = {
  display_name: string;
  reach_ids: string[];
  naming_provenance: "USER_DEFINED" | "ENGINEERING_SUPPLIED";
  start_cumdist?: number;
};

export type FloodingSourceResponse = {
  flooding_source: {id: string; display_name: string; topology_validation_status: string; validation_messages: string[]};
  continuous_distance: {status: string; distances: Record<string, number>; ledger: any[]; messages: string[]};
  db?: any;
};

export type GetProfileResponse = {
  session_id: string;
  reach: { river: string; reach: string };
  profile: string;
  vertical_datum?: string;
  datum_provenance?: string;
  source_units?: string;
  working_units?: string;
  unit_contract?: Record<string, string>;
  unit_conversion_status?: string;
  profiles_selected?: string[];
  profile_segments?: Record<string, Array<Array<[number, number]>>>;
  profile_metadata?: ProfileMeta[];
  hydraulic_preflight?: {errors: string[]; warnings: string[]};
  profile_names: string[];
  cross_sections: Array<{
    xs_id: string;
    xs_identity?: [string, string, string];
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
    raw_wsel_by_profile?: Record<string, number | null>;
    adjustment_by_profile?: Record<string, {
      original_value?: number | null; adjusted_value?: number | null;
      reason?: string; provenance?: string;
    } | null>;
    drawdown_by_profile?: Record<string, {
      working_value: number; qualifying_level: number; reason: string;
    } | null>;
  }>;
  structure_models?: Array<{
    identity: string; river: string; reach: string; rs: string; type: string;
    status: string; reason: string; bounds: [number, number] | null;
  }>;
  structures: Array<{
    structure_id?: string;
    row_identity?: string;
    support_status?: string;
    support_reason?: string;
    geometry_role?: string;
    occupied_interval?: [number, number] | null;
    containing_interval?: [number, number] | null;

    river: string;
    reach: string;
    rs: string;
    type: string;
    name: string;
    source_name?: string | null;
    reviewer_name?: string | null;
    name_provenance?: "SOURCE" | "USER_ENTERED" | "UNNAMED" | string;
    source_faces?: Array<{
      face: string; station: number; invert: number;
      low_chord: number; high_chord: number; provenance?: Record<string, unknown>;
    }>;
    weir_width: number;
    low_chord: number | null;
    high_chord: number | null;
    minchel: number | null;
    cumdist?: number | null;
    connected_us_rs?: string | null;
    profile_data?: Array<[number, number]>;
    hw_connectivity?: Array<[string, number]>;
    tw_connectivity?: Array<[string, number]>;
    lateral_hw_distance?: number | null;
    lateral_tw_distance?: number | null;
    connected_ds_rs?: string | null;
    culvert_us_gap?: number | null;
    culvert_barrel_length?: number | null;
    culvert_ds_residual?: number | null;
    structure_partition_valid?: boolean | null;
    structure_partition_warning?: string | null;
  }>;
};

export type ExportOptions = {
  expected_engineering_version?: string;
  format: "pdf" | "dxf";
  profile_names?: string[];
  panel_numbers?: number[];
  page_size?: string;
  custom_page_w?: number;
  custom_page_h?: number;
  h_scale?: number;
  v_scale?: number;
  distance_unit?: string;
  vertical_datum?: string;
  require_known_datum?: boolean;
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
export type ExportJobResponse = {
  job_id: string;
  session_id: string;
  export_type: string;
  output_filename: string;
  state: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  stage: "QUEUED" | "VALIDATION" | "DRAWING_DEFINITION" | "RENDERING" | "WRITING" | "FINALIZATION" | "COMPLETED" | "FAILED" | "CANCELLED";
  message: string;
  result: ExportResponse | null;
  error: string | null;
};

export type PanelInput = {
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

export type PanelsResponse = {
  panels: PanelInput[];
};

export type PanelContentContributor = {
  kind: string;
  identity: string;
  label: string;
  minimum_elevation: number;
  maximum_elevation: number;
  outside_existing_range: boolean;
  below_by: number;
  above_by: number;
  provenance: string;
};

export type PanelContentAnalysis = {
  panel: number;
  panel_suffix: string;
  display_id: string;
  existing_minimum_elevation: number;
  existing_maximum_elevation: number;
  required_minimum_elevation: number;
  required_maximum_elevation: number;
  missing_lower_clearance: number;
  missing_upper_clearance: number;
  suggested_minimum_elevation: number;
  suggested_maximum_elevation: number;
  existing_panel_valid: boolean;
  responsible_content: PanelContentContributor[];
  saved_vertical_scale: number;
  saved_horizontal_scale: number;
  page_size: string;
  physical_vertical_capacity: number;
  required_content_span: number;
  suggested_span: number;
  suggested_required_plot_height_inches: number;
  suggested_vertical_clearance_inches: number;
  suggested_physically_valid: boolean;
  requires_scale_or_page_change: boolean;
  minimum_vertical_scale_for_suggestion: number;
  sheet_capacity: {
    page_size: string;
    paper_width_inches: number;
    paper_height_inches: number;
    usable_plot_width_inches: number;
    usable_plot_height_inches: number;
    horizontal_station_span: number;
    vertical_elevation_span: number;
    horizontal_scale: number;
    vertical_scale: number;
    required_plot_width_inches: number;
    required_plot_height_inches: number;
    remaining_horizontal_clearance_inches: number;
    remaining_vertical_clearance_inches: number;
    horizontal_overflow_inches: number;
    vertical_overflow_inches: number;
    physically_valid: boolean;
  };
};

export type PanelContentAnalysisResponse = {
  panels: PanelContentAnalysis[];
  all_panels_valid: boolean;
  panels_stale: boolean;
  proposal_only: true;
  message: string;
};

export type PanelsReplace = {
  panels: PanelInput[];
  auto: boolean;
  draft?: boolean;
  h_scale?: number | null;
  v_scale?: number | null;
  page_size?: string | null;
};

export type AutoEndResponse = {
  start_station: number;
  end_station: number;
  h_scale: number;
  page_size: string;
  usable_width_in: number;
  panel_coverage: number;
};

export type RevisionLimitsResponse = {
  review_required?: boolean;
  revision_start_station: number;
  revision_end_station: number;
  model_start_station: number;
  model_end_station: number;
  explicit: boolean;
};

export type StructurePreflightResponse = {
  can_export: boolean;
  inventory: {state?: string; reason?: string};
  structures: Array<{
    identity: string; river: string; reach: string; rs: string; type: string;
    status: string; reason: string; affects_range: boolean; message: string;
    missing_fields?: string[]; diagnostic_preview_available?: boolean;
  }>;
};

export type AutoLetterResponse = {
  ok: true;
  cross_sections_evaluated: number | null;
  letters_assigned: number | null;
  letters_changed: number | null;
  no_op: boolean | null;
  details_complete: boolean;
};

export type FixDrawdownsResponse = {
  ok: true;
  profiles_evaluated: number | null;
  cross_sections_evaluated: number | null;
  wsel_adjustments_made: number | null;
  affected_profiles: string[] | null;
  no_op: boolean | null;
  details_complete: boolean;
};

function operationRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || (value as Record<string, unknown>).ok !== true) {
    throw new ApiError(
      "The server response did not confirm the operation. No automatic retry was attempted; verify the current working values before trying again.",
      "UNCONFIRMED_OPERATION",
      502,
    );
  }
  return value as Record<string, unknown>;
}

function resultCount(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function normalizeAutoLetterResponse(value: unknown): AutoLetterResponse {
  const result = operationRecord(value);
  const crossSections = resultCount(result.cross_sections_evaluated);
  const assigned = resultCount(result.letters_assigned);
  const changed = resultCount(result.letters_changed);
  const noOp = typeof result.no_op === "boolean" ? result.no_op : null;
  return {
    ok: true,
    cross_sections_evaluated: crossSections,
    letters_assigned: assigned,
    letters_changed: changed,
    no_op: noOp,
    details_complete: crossSections != null && assigned != null && changed != null && noOp != null,
  };
}

export function normalizeFixDrawdownsResponse(value: unknown): FixDrawdownsResponse {
  const result = operationRecord(value);
  const profiles = resultCount(result.profiles_evaluated);
  const crossSections = resultCount(result.cross_sections_evaluated);
  const adjustments = resultCount(result.wsel_adjustments_made);
  const affected = Array.isArray(result.affected_profiles) && result.affected_profiles.every(item => typeof item === "string")
    ? [...result.affected_profiles] as string[]
    : null;
  const noOp = typeof result.no_op === "boolean" ? result.no_op : null;
  return {
    ok: true,
    profiles_evaluated: profiles,
    cross_sections_evaluated: crossSections,
    wsel_adjustments_made: adjustments,
    affected_profiles: affected,
    no_op: noOp,
    details_complete: profiles != null && crossSections != null && adjustments != null && affected != null && noOp != null,
  };
}

export type AppSettings = {
  host?: string;
  port?: number;
  last_plan_path?: string;
  default_fis?: Record<string, any>;
};

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) { super(message); }
}

let mutationTail: Promise<unknown> | null = null;
const readVersions = new Map<string, number>();
function req<T>(path: string, init?: RequestInit): Promise<T> {
  const version = contextVersion();
  const headers = contextHeaders();
  const mutation = init?.method && init.method !== 'GET';
  const readKey = path.split('?')[0];
  const readVersion = (readVersions.get(readKey) ?? 0) + 1;
  const latestRead = !mutation && path.startsWith('/api/profile/');
  if (latestRead) readVersions.set(readKey, readVersion);
  const check = () => {
    assertContext(version);
    if (latestRead && readVersions.get(readKey) !== readVersion) throw new StaleResponse('Superseded read');
  };
  const execute = () => { check(); return performRequest<T>(path, init, headers, check); };
  if (!mutation) return execute();
  // Preserve edit order and discard queued edits from obsolete contexts before send.
  const result = mutationTail ? mutationTail.catch(() => {}).then(execute) : execute();
  mutationTail = result;
  void result.finally(() => { if (mutationTail === result) mutationTail = null; }).catch(() => {});
  return result;
}

async function performRequest<T>(path: string, init: RequestInit | undefined, headers: Record<string,string>, check: () => void): Promise<T> {
  const version = contextVersion();
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...headers },
    ...init,
  }).catch(error => { check(); throw error; });
  check();
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    let code = 'INTERNAL_ERROR';
    try {
      const body = await r.json();
      code = body?.code ?? code;
      if (body?.detail)
        msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    check();
    throw new ApiError(msg, code, r.status);
  }
  const body = await r.json();
  check();
  if (typeof window !== "undefined" && init?.method && init.method !== "GET" && !path.startsWith("/api/project/")
      && path !== "/api/source" && !path.includes("/commit") && init.method !== "DELETE") {
    window.dispatchEvent(new CustomEvent("rasplot:committed-mutation"));
  }
  return body as T;
}

export const api = {
  closeSession: (sid: string) => req(`/api/session/${sid}`, {method: 'DELETE'}),
  abandonSession: (sid: string) => fetch(`/api/session/${sid}`, {method: 'DELETE'}).then(() => undefined),
  browseSource: () => req<{path: string | null}>("/api/source/browse", { method: "POST", body: "{}" }),
  browseProjectOpen: () => req<{path: string | null}>("/api/project/browse-open", {method: "POST", body: "{}"}),
  browseProjectSave: () => req<{path: string | null}>("/api/project/browse-save", {method: "POST", body: "{}"}),
  openProject: (path: string, replacementPath: string | null, modelGeneration = contextVersion()) =>
    req<OpenResponse & {project_path: string; warnings?: string[]}>("/api/project/open", {
      method: "POST", body: JSON.stringify({path, replacement_path: replacementPath, model_generation: modelGeneration}),
    }),
  saveProject: (sid: string, path: string | null, overwrite: boolean) =>
    req<{ok: boolean; path: string; project_id: string; saved_at_utc: string}>(`/api/project/${sid}/save`, {
      method: "POST", body: JSON.stringify({path, overwrite}),
    }),
  getSettings: () => req<AppSettings>("/api/settings"),
  openSource: (path: string, modelGeneration = contextVersion()) =>
    req<OpenResponse>("/api/source", { method: "POST", body: JSON.stringify({ path, model_generation: modelGeneration }) }),
  commitSession: (sid: string) => req<{model_context: any}>(`/api/session/${sid}/commit`, {method: 'POST', body: '{}'}),
  getModelInventory: (sid: string) => req<any>(`/api/model/${sid}/inventory`),
  validateFloodingSource: (sid: string, body: FloodingSourceRequest) =>
    req<FloodingSourceResponse>(`/api/model/${sid}/flooding-source`, {method: "POST", body: JSON.stringify(body)}),
  buildFloodingSource: (sid: string, body: FloodingSourceRequest) =>
    req<FloodingSourceResponse>(`/api/model/${sid}/flooding-source/build`, {method: "POST", body: JSON.stringify(body)}),
  getProfile: (sid: string, reachIdx: number, profiles: string | string[]) => {
    const list = Array.isArray(profiles) ? profiles : [profiles];
    const q = new URLSearchParams({
      reach_idx: String(reachIdx),
      profiles: JSON.stringify(list.filter(Boolean)),
    });
    return req<GetProfileResponse>(`/api/profile/${sid}?${q.toString()}`);
  },
  getMap: (sid: string) => req<GeoJSON.FeatureCollection>(`/api/map/${sid}`),
  getDb: (sid: string) => req<any>(`/api/db/${sid}`),
  getPanels: (sid: string) => req<PanelsResponse>(`/api/db/${sid}/panels`),
  analyzePanelContent: (sid: string, panels: PanelInput[], pageSize: string, roundingIncrement = 5) =>
    req<PanelContentAnalysisResponse>(`/api/db/${sid}/panels/content-analysis`, {
      method: "POST",
      body: JSON.stringify({panels, page_size: pageSize, rounding_increment: roundingIncrement}),
    }),
  getRevisionLimits: (sid: string) =>
    req<RevisionLimitsResponse>(`/api/db/${sid}/revision-limits`),
  getStructurePreflight: (sid: string) =>
    req<StructurePreflightResponse>(`/api/db/${sid}/structure-preflight`),
  putRevisionLimits: (sid: string, start: number, end: number) =>
    req<RevisionLimitsResponse>(`/api/db/${sid}/revision-limits`, {
      method: "PUT",
      body: JSON.stringify({
        revision_start_station: start,
        revision_end_station: end,
      }),
    }),
  clearRevisionLimits: (sid: string) =>
    req<RevisionLimitsResponse>(`/api/db/${sid}/revision-limits`, { method: "DELETE" }),
  getAutoEnd: (sid: string, startStation: number) =>
    req<AutoEndResponse>(`/api/db/${sid}/auto-end?start_station=${encodeURIComponent(String(startStation))}`),
  patchWorking: (sid: string, patches: any[]) =>
    req<any>(`/api/db/${sid}/working`, { method: "PATCH", body: JSON.stringify(patches) }),
  fixDrawdowns: (sid: string) =>
    req<unknown>(`/api/db/${sid}/fix-drawdowns`, { method: "POST", body: "{}" }).then(normalizeFixDrawdownsResponse),
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
    req<unknown>(`/api/db/${sid}/auto-letter?every=${every}`, { method: "POST", body: "{}" }).then(normalizeAutoLetterResponse),
  nodeNames: (sid: string, enabled: boolean) =>
    req<any>(`/api/db/${sid}/node-names`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  putHeader: (sid: string, body: any) =>
    req<any>(`/api/db/${sid}/header`, { method: "PUT", body: JSON.stringify(body) }),
  putProfiles: (sid: string, updates: any[]) =>
    req<any>(`/api/db/${sid}/profiles`, { method: "PUT", body: JSON.stringify(updates) }),
  putPanels: (sid: string, body: PanelsReplace) =>
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
  startExport: (sid: string, opts: ExportOptions) =>
    req<ExportJobResponse>(`/api/report/export/start`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...opts }),
    }),
  getExportJob: (sid: string, jobId: string) =>
    req<ExportJobResponse>(`/api/report/export-jobs/${encodeURIComponent(sid)}/${encodeURIComponent(jobId)}`),
  cancelExportJob: (sid: string, jobId: string) =>
    req<ExportJobResponse>(`/api/report/export-jobs/${encodeURIComponent(sid)}/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
    }),
  exportCadReviewPackage: (sid: string, opts: ExportOptions) =>
    req<ExportResponse>(`/api/report/cad-review-package`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, ...opts, format: "cad-review-package" }),
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
