import { StaleResponse, assertContext, contextVersion } from "../api/context";
import { useEffect, useRef, useState } from "react";
import { api, type ExportJobResponse, type ExportOptions, type PanelInput, type RevisionLimitsResponse } from "../api/client";

type Props = {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  selectedProfiles: string[];
  onBusy: (b: boolean) => void;
  onError: (e: string | null) => void;
};

export function cadReviewAvailability({
  busy, panelsReady, savedPanelCount, selectedPanelCount, panelsStale,
}: {
  busy: boolean;
  panelsReady: boolean;
  savedPanelCount: number;
  selectedPanelCount: number;
  panelsStale: boolean;
}) {
  if (busy || !panelsReady) return { enabled: false, message: "Loading saved-panel status..." };
  if (savedPanelCount === 0) return {
    enabled: false,
    message: "Open Panel Setup, create or load panels, then Apply to save and review them.",
  };
  if (panelsStale) return {
    enabled: false,
    message: "Saved panels require review. Open Panel Setup, verify the geometry, and Apply before export.",
  };
  if (selectedPanelCount === 0) return {
    enabled: false,
    message: "Select one saved panel in the Panels section.",
  };
  if (selectedPanelCount > 1) return {
    enabled: false,
    message: "CAD Review exports one panel at a time. Clear Selection, then select exactly one panel.",
  };
  return {
    enabled: true,
    message: "Ready: one reviewed saved panel will export as raw station/elevation DXF with aligned PNG/PGW.",
  };
}

export default function ExportDialog({
  open, onClose, sessionId, selectedProfiles, onBusy, onError,
}: Props) {
  const [format, setFormat] = useState<"pdf" | "dxf">("pdf");
  const [pageSize, setPageSize] = useState("11x17");
  const [sizes, setSizes] = useState<Record<string, { width_in: number; height_in: number }>>({});
  const [units, setUnits] = useState<string[]>(["Feet"]);
  const [dxfVersions, setDxfVersions] = useState<string[]>(["R2013"]);
  const [hScale, setHScale] = useState(500);
  const [vScale, setVScale] = useState(5);
  const [unit, setUnit] = useState("Feet");
  const [stream, setStream] = useState("");
  const [community, setCommunity] = useState("");
  const [county, setCounty] = useState("");
  const [state, setState] = useState("");
  const [datum, setDatum] = useState("");
  const [otherDatum, setOtherDatum] = useState("");
  const [requireDatum, setRequireDatum] = useState(false);
  const [startDesc, setStartDesc] = useState("");
  const [panelNo, setPanelNo] = useState(1);
  const [dxfVer, setDxfVer] = useState("R2013");
  const [grid, setGrid] = useState(true);
  const [titleBlock, setTitleBlock] = useState(true);
  const [customW, setCustomW] = useState<number | "">("");
  const [customH, setCustomH] = useState<number | "">("");
  const [useView, setUseView] = useState(true);
  const [fitOne, setFitOne] = useState(true);
  const [lastInfo, setLastInfo] = useState<string>("");
  const [savedPanels, setSavedPanels] = useState<PanelInput[]>([]);
  const [selectedPanelNumbers, setSelectedPanelNumbers] = useState<number[]>([]);
  const [panelsLoading, setPanelsLoading] = useState(false);
  const [panelsReady, setPanelsReady] = useState(false);
  const [revisionLimits, setRevisionLimits] = useState<RevisionLimitsResponse | null>(null);
  const [panelsStale, setPanelsStale] = useState(false);
  const [profileLabels, setProfileLabels] = useState<Record<string,string>>({});
  const [loading, setLoading] = useState(true);
  const [metadataReady, setMetadataReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState<ExportJobResponse | null>(null);
  const [download, setDownload] = useState<{url: string; label: string; filename: string} | null>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape" && !exporting) onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, exporting, onClose]);
  const running = useRef(false);
  const initialized = useRef(false);
  const engineeringVersion = useRef<string>();

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setMetadataReady(false);
    setError(null);
    api.getDb(sessionId).then((db) => {
      if (!active) return;
      setPanelsStale(Boolean(db.panels_stale));
      engineeringVersion.current = db.engineering_version;
      setProfileLabels(Object.fromEntries((db.profiles ?? []).map((p: any) => [p.profile_id, p.name])));
      const h = db.header;
      if (!h) throw new Error("Project metadata could not be loaded.");
      if (!initialized.current) {
        setStream(h.stream_name ?? ""); setCommunity(h.community ?? "");
        setCounty(h.county ?? ""); setState(h.state ?? "");
        setRequireDatum(h.require_known_datum ?? false);
        { const loadedDatum = h.vertical_datum ?? "UNKNOWN";
          if (["UNKNOWN", "NAVD88", "NGVD29"].includes(loadedDatum)) setDatum(loadedDatum);
          else { setDatum("OTHER"); setOtherDatum(loadedDatum); }
        }
        setStartDesc(h.stream_distance_label ?? "");
        setPanelNo(h.start_panel_no ?? 1);
        initialized.current = true;
      }
      setHScale(h.h_scale); setVScale(h.v_scale);
      setUnit(h.unit); setPageSize(h.page_size);
      setLoading(false);
      setMetadataReady(true);
    }).catch((e) => { if (active) { setError(String(e.message ?? e)); setLoading(false); } });
    return () => { active = false; };
  }, [open, sessionId]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    api.getPageSizes().then((r) => {
      if (!active) return;
      setSizes(r.sizes || {});
      setUnits(r.distance_units || ["Feet"]);
      setDxfVersions(r.dxf_versions || ["R2013"]);
    }).catch(() => {});
    api.getRevisionLimits(sessionId).then((response) => {
      if (active) setRevisionLimits(response);
    }).catch(() => {
      if (active) setRevisionLimits(null);
    });
    setPanelsLoading(true);
    setPanelsReady(false);
    api.getPanels(sessionId).then((response) => {
      if (!active) return;
      const panels = response.panels || [];
      setSavedPanels(panels);
      setSelectedPanelNumbers(panels.map((panel) => panel.panel));
      setFitOne(panels.length === 0);
      setUseView(panels.length === 0);
      setPanelsReady(true);
    }).catch((error: any) => {
      if (!active) return;
      setSavedPanels([]);
      setSelectedPanelNumbers([]);
      setError(String(error.message ?? error));
    }).finally(() => {
      if (active) setPanelsLoading(false);
    });
    return () => { active = false; };
  }, [open, sessionId]);

  if (!open) return null;

  function exportOptions(): ExportOptions {
    const opts: ExportOptions = {
      expected_engineering_version: engineeringVersion.current,
      format,
      profile_names: selectedProfiles,
      ...(savedPanels.length > 0 ? { panel_numbers: selectedPanelNumbers } : {}),
      page_size: pageSize, h_scale: hScale, v_scale: vScale,
      distance_unit: unit, vertical_datum: datum === "OTHER" ? otherDatum : datum, require_known_datum: requireDatum,
      stream_name: stream, community, county, state,
      start_station_description: startDesc, panel_no: panelNo,
      dxf_version: dxfVer, include_grid: grid, include_title_block: titleBlock,
      use_current_view: useView || fitOne, fit_to_one_panel: fitOne,
    };
    if (customW && customH) {
      opts.custom_page_w = Number(customW); opts.custom_page_h = Number(customH);
    }
    return opts;
  }

  async function doExport() {
    if (savedPanels.length > 0 && selectedPanelNumbers.length === 0) {
      setError("Select at least one saved panel to export.");
      return;
    }
    const operationVersion = contextVersion();
    await runBackgroundExport(format.toUpperCase(), async () => {
      const opts = exportOptions();
      const job = await api.startExport(sessionId, opts);
      setExportStatus(job);
      let current = job;
      while (!(["COMPLETED", "FAILED", "CANCELLED"] as string[]).includes(current.state)) {
        await new Promise(resolve => window.setTimeout(resolve, 400));
        try {
          assertContext(operationVersion);
        } catch (error) {
          await api.cancelExportJob(sessionId, job.job_id).catch(() => undefined);
          throw error;
        }
        current = await api.getExportJob(sessionId, job.job_id);
        setExportStatus(current);
      }
      if (current.state === "FAILED") throw new Error(current.error || current.message);
      if (current.state === "CANCELLED") throw new Error("Export cancelled. No artifact was created.");
      if (!current.result) throw new Error("Export completed without a downloadable artifact.");
      const res = current.result;
      const info =
        `panels=${(res as any).panel_count ?? "?"}  H=1"=${(res as any).h_scale ?? hScale}  ` +
        `V=1"=${(res as any).v_scale ?? vScale}  ${(res as any).page_size ?? pageSize}`;
      setLastInfo(info);
      if ((res as any).h_scale) setHScale(Number((res as any).h_scale));
      if ((res as any).v_scale) setVScale(Number((res as any).v_scale));
      return res;
    });
  }

  async function cancelActiveExport() {
    if (!exportStatus?.job_id || ["COMPLETED", "FAILED", "CANCELLED"].includes(exportStatus.state)) return;
    try {
      const status = await api.cancelExportJob(sessionId, exportStatus.job_id);
      setExportStatus(status);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function doCadReviewExport() {
    if (savedPanels.length === 0 || selectedPanelNumbers.length !== 1) {
      setError("Select exactly one saved panel for the CAD Review Package.");
      return;
    }
    await runExport("CAD Review Package", () => api.exportCadReviewPackage(sessionId, {
      ...exportOptions(), use_current_view: false, fit_to_one_panel: false,
    }));
  }

  function togglePanel(panelNumber: number) {
    setSelectedPanelNumbers((current) => current.includes(panelNumber)
      ? current.filter((number) => number !== panelNumber)
      : [...current, panelNumber]);
  }

  function panelValue(value: number) {
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  async function runExport(label: string, action: () => Promise<{path: string}>) {
    const version = contextVersion();
    if (running.current || loading || !metadataReady || panelsLoading || !panelsReady) return;
    running.current = true;
    setExporting(true); onBusy(true); onError(null); setError(null);
    setDownload(null); setLastInfo("");
    try {
      const res = await action();
      const filename = String(res.path).split(/[\\/]/).pop() || `${label} output`;
      setDownload({url: api.downloadUrl(sessionId, res.path), label, filename});
    } catch (e: any) {
      if (e instanceof StaleResponse) return;
      setError(String(e.message ?? e));
    } finally {
      running.current = false;
      if (version === contextVersion()) { setExporting(false); onBusy(false); }
    }
  }

  async function runBackgroundExport(label: string, action: () => Promise<{path: string}>) {
    setExportStatus(null);
    await runExport(label, action);
  }

  const disabled = exporting || loading || !metadataReady || panelsLoading || !panelsReady;
  const cadReview = cadReviewAvailability({
    busy: disabled,
    panelsReady,
    savedPanelCount: savedPanels.length,
    selectedPanelCount: selectedPanelNumbers.length,
    panelsStale,
  });
  return (
    <div className="modal-backdrop export-backdrop">
      <div className="export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title">
        <header className="export-header">
          <div><span className="eyebrow">Panels and output</span><h2 id="export-title">Export Engineering Deliverables</h2></div>
          <button className="icon-button" aria-label="Close export" disabled={exporting} onClick={onClose}>×</button>
        </header>
        <fieldset className="export-body" disabled={disabled}>
          <section>
            <h3>Export Scope</h3>
            <p>{selectedProfiles.map(id => profileLabels[id] ?? id).join(", ") || "No profiles selected"}</p>
            {revisionLimits && <p>Station range: {panelValue(revisionLimits.revision_start_station)} - {panelValue(revisionLimits.revision_end_station)} ft</p>}
          </section>
          <section>
            <h3>Format and Page</h3>
            <div className="export-fields">
              <label>Format<select value={format} onChange={e => setFormat(e.target.value as "pdf" | "dxf")}>
                <option value="pdf">Engineering PDF</option><option value="dxf">Engineering DXF (paper-inch CAD drawing)</option>
              </select></label>
              <label>Page size<select value={pageSize} onChange={e => setPageSize(e.target.value)}>
                {Object.keys(sizes).length ? Object.entries(sizes).map(([k,v]) => <option key={k} value={k}>{k} ({v.width_in} x {v.height_in} in)</option>) : <option value={pageSize}>{pageSize}</option>}
              </select></label>
              <label>Custom Width (in, optional)<input type="number" value={customW} onChange={e => setCustomW(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <label>Custom Height (in, optional)<input type="number" value={customH} onChange={e => setCustomH(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <label>Horizontal scale (ft/in)<input type="number" value={hScale} disabled={savedPanels.length > 0} onChange={e => setHScale(Number(e.target.value))} /></label>
              <label>Vertical scale (ft/in)<input type="number" value={vScale} disabled={savedPanels.length > 0} onChange={e => setVScale(Number(e.target.value))} /></label>
              <label>Distance display unit<select value={unit} onChange={e => setUnit(e.target.value)}>{units.map(u => <option key={u}>{u}</option>)}</select></label>
              <label>Start panel number<input type="number" value={panelNo} onChange={e => setPanelNo(Number(e.target.value))} /></label>
            </div>
            <p className="export-hint">Enter both custom dimensions to override the page size. Saved panels retain their saved scales.</p>
          </section>
          <section>
            <h3>Project Information</h3>
            <p className="export-hint">Applies to this project only. Verify the stream name and vertical datum before exporting.</p>
            <div className="export-fields">
              <label>Stream name<input value={stream} onChange={e => setStream(e.target.value)} /></label>
              <label>Community<input value={community} onChange={e => setCommunity(e.target.value)} /></label>
              <label>County<input value={county} onChange={e => setCounty(e.target.value)} /></label>
              <label>State<input value={state} onChange={e => setState(e.target.value)} /></label>
              <label>Vertical datum (source or verified user entry)<select value={["UNKNOWN", "NAVD88", "NGVD29"].includes(datum) ? datum : "OTHER"} onChange={e => setDatum(e.target.value === "OTHER" ? "OTHER" : e.target.value)}>
                <option value="UNKNOWN">Unknown</option><option value="NAVD88">NAVD 88</option><option value="NGVD29">NGVD 29</option><option value="OTHER">Other</option>
              </select></label>
              {datum === "OTHER" && <label>Other datum label<input value={otherDatum} onChange={e => setOtherDatum(e.target.value)} placeholder="Enter verified datum label" /></label>}
              <label><span>Datum requirement</span><span><input type="checkbox" checked={requireDatum} onChange={e => setRequireDatum(e.target.checked)} /> Deliverable requires a known datum</span></label>
              <label>Start station note<input value={startDesc} onChange={e => setStartDesc(e.target.value)} placeholder="e.g. confluence with ..." /></label>
            </div><p className="export-hint">Selecting a datum labels the deliverable; RAS-Plot does not perform a vertical datum conversion.</p>
          </section>
          <section>
            <h3>Panels</h3>
            {panelsStale && <p role="alert">Saved panels need review after engineering changes. Open Panel Setup and Apply before exporting saved panels.</p>}
            {panelsLoading ? <p>Loading saved panels...</p> : savedPanels.length > 0 ? <>
              <p className="export-hint">These panels come from Panel Setup and retain their saved scales. Use Panel Setup to change their layout.</p>
              <div className="export-panel-actions"><strong>{selectedPanelNumbers.length} of {savedPanels.length} panels selected</strong>
                <button onClick={() => setSelectedPanelNumbers(savedPanels.map(p => p.panel))}>Select All</button>
                <button onClick={() => setSelectedPanelNumbers([])}>Clear Selection</button>
              </div>
              <div className="export-panels">{savedPanels.map(p => <label className="export-panel" key={p.panel}>
                <input type="checkbox" checked={selectedPanelNumbers.includes(p.panel)} onChange={() => togglePanel(p.panel)} aria-label={`Select panel ${p.display_id ?? p.panel}`} />
                <strong>Panel {p.display_id ?? p.panel}</strong>
                <span>Stations: {panelValue(p.start_station)} - {panelValue(p.end_station)} ft</span>
                <span>Elevations: {panelValue(p.start_elevation)} - {panelValue(p.end_elevation)} ft</span>
                <small>H: {p.h_scale} ft/in / V: {p.v_scale} ft/in</small>
              </label>)}</div>
            </> : <div className="export-checks">
              <label><input type="checkbox" checked={fitOne} onChange={e => setFitOne(e.target.checked)} /> Fit active export range on one panel (adjusts scale if needed)</label>
              <label><input type="checkbox" checked={useView} disabled={fitOne} onChange={e => setUseView(e.target.checked)} /> Rebuild panels using the stated scales and page size</label>
            </div>}
          </section>
          {format === "dxf" && <section>
            <h3>DXF Options</h3>
            <div className="export-fields"><label>DXF version<select value={dxfVer} onChange={e => setDxfVer(e.target.value)}>{dxfVersions.map(v => <option key={v}>{v}</option>)}</select></label></div>
            <p className="export-hint">R2013 is recommended for current AutoCAD workflows. Engineering DXF uses paper-inch model-space coordinates and preserves the validated sheet scale. CAD Review Package is a separate single-panel R12 export using raw station/elevation coordinates with aligned PNG/PGW files.</p>
            <div className="export-checks">
              <label><input type="checkbox" checked={grid} onChange={e => setGrid(e.target.checked)} /> Include grid</label>
              <label><input type="checkbox" checked={titleBlock} onChange={e => setTitleBlock(e.target.checked)} /> Include title block</label>
            </div>
          </section>}
        </fieldset>
        <footer className="export-footer">
          <div className="export-status" aria-live="polite">
            {loading && !error && <span>Loading project metadata...</span>}
            {exporting && <span><strong>{exportStatus?.stage?.replaceAll("_", " ") ?? "STARTING"}</strong>: {exportStatus?.message ?? "Starting export..."}</span>}
            {error && <p role="alert" className="export-error">{error}</p>}
            {download && <p>{download.label} generated successfully as <strong>{download.filename}</strong>. <a href={download.url} target="_blank" rel="noreferrer">Download {download.filename}</a><small>Your browser chooses the download destination and reports its download status.</small>{lastInfo && <small>{lastInfo}</small>}</p>}
          </div>
          <div className="export-actions">
            <div className="export-secondary">
              <button disabled={!cadReview.enabled} aria-describedby="cad-review-requirement"
                title={cadReview.message} onClick={doCadReviewExport}>Export CAD Review Package</button>
              <span id="cad-review-requirement" role="status">{cadReview.message}</span>
              <span>FDT / FIRM disabled: engineering and regulatory validation pending.</span>
            </div>
            <div className="export-primary">{exporting
              ? <button onClick={cancelActiveExport} disabled={exportStatus?.state === "CANCELLED"}>Cancel Export</button>
              : <button onClick={onClose}>Cancel</button>}
              <button className="export-submit" disabled={disabled || (savedPanels.length > 0 && selectedPanelNumbers.length === 0)} onClick={doExport}>{exporting ? "Exporting..." : `Export ${format.toUpperCase()}`}</button></div>
          </div>
        </footer>
      </div>
    </div>
  );
}
