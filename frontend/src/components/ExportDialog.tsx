import { useEffect, useState } from "react";
import { api, type ExportOptions } from "../api/client";

type Props = {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  selectedProfiles: string[];
  defaults: Record<string, any>;
  onBusy: (b: boolean) => void;
  onError: (e: string | null) => void;
};

export default function ExportDialog({
  open, onClose, sessionId, selectedProfiles, defaults, onBusy, onError,
}: Props) {
  const [format, setFormat] = useState<"pdf" | "dxf">("pdf");
  const [pageSize, setPageSize] = useState("11x17");
  const [sizes, setSizes] = useState<Record<string, { width_in: number; height_in: number }>>({});
  const [units, setUnits] = useState<string[]>(["Feet"]);
  const [dxfVersions, setDxfVersions] = useState<string[]>(["R2013"]);
  const [hScale, setHScale] = useState(Number(defaults.h_scale ?? 500));
  const [vScale, setVScale] = useState(Number(defaults.v_scale ?? 5));
  const [unit, setUnit] = useState(String(defaults.distance_unit ?? "Feet"));
  const [stream, setStream] = useState(String(defaults.stream_name ?? "WHITE RIVER"));
  const [community, setCommunity] = useState(String(defaults.community ?? ""));
  const [county, setCounty] = useState(String(defaults.county ?? ""));
  const [state, setState] = useState(String(defaults.state ?? "IN"));
  const [datum, setDatum] = useState(String(defaults.vertical_datum ?? "NAVD88"));
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

  useEffect(() => {
    if (!open) return;
    api.getPageSizes().then((r) => {
      setSizes(r.sizes || {});
      setUnits(r.distance_units || ["Feet"]);
      setDxfVersions(r.dxf_versions || ["R2013"]);
    }).catch(() => {});
  }, [open]);

  if (!open) return null;

  async function doExport() {
    onBusy(true);
    onError(null);
    try {
      const opts: ExportOptions = {
        format,
        profile_names: selectedProfiles,
        page_size: pageSize,
        h_scale: hScale,
        v_scale: vScale,
        distance_unit: unit,
        vertical_datum: datum,
        stream_name: stream,
        community,
        county,
        state,
        start_station_description: startDesc,
        panel_no: panelNo,
        dxf_version: dxfVer,
        include_grid: grid,
        include_title_block: titleBlock,
        use_current_view: useView || fitOne,
        fit_to_one_panel: fitOne,
      };
      if (customW && customH) {
        opts.custom_page_w = Number(customW);
        opts.custom_page_h = Number(customH);
      }
      const res = await api.exportReport(sessionId, opts);
      const info =
        `panels=${(res as any).panel_count ?? "?"}  H=1"=${(res as any).h_scale ?? hScale}  ` +
        `V=1"=${(res as any).v_scale ?? vScale}  ${(res as any).page_size ?? pageSize}`;
      setLastInfo(info);
      if ((res as any).h_scale) setHScale(Number((res as any).h_scale));
      if ((res as any).v_scale) setVScale(Number((res as any).v_scale));
      window.open(api.downloadUrl(sessionId, res.path), "_blank");
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      onBusy(false);
    }
  }

  async function doFdt() {
    onBusy(true);
    onError(null);
    try {
      await api.putHeader(sessionId, {
        stream_name: stream, community, county, state, vertical_datum: datum,
      });
      const res = await api.buildFdt(sessionId);
      window.open(api.downloadUrl(sessionId, res.path), "_blank");
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      onBusy(false);
    }
  }

  async function doFirm() {
    onBusy(true);
    onError(null);
    try {
      const res = await api.buildFirm(sessionId);
      window.open(api.downloadUrl(sessionId, res.path), "_blank");
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      onBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Export flood profile</h2>
          <button className="ghost" onClick={onClose}>×</button>
        </header>

        <section className="form-grid">
          <label>Format</label>
          <div className="row">
            <label><input type="radio" checked={format === "pdf"} onChange={() => setFormat("pdf")} /> PDF</label>
            <label><input type="radio" checked={format === "dxf"} onChange={() => setFormat("dxf")} /> DXF (CAD)</label>
          </div>

          <label>Page size</label>
          <select value={pageSize} onChange={(e) => setPageSize(e.target.value)}>
            {Object.keys(sizes).length
              ? Object.entries(sizes).map(([k, v]) => (
                  <option key={k} value={k}>{k} ({v.width_in}×{v.height_in} in)</option>
                ))
              : ["11x17", "ANSI_A", "ANSI_B", "ANSI_C", "ANSI_D", "A3", "A1", "LETTER", "TABLOID"].map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
          </select>

          <label>Custom page (in)</label>
          <div className="row">
            <input type="number" placeholder="W" value={customW}
              onChange={(e) => setCustomW(e.target.value === "" ? "" : Number(e.target.value))} />
            <span>×</span>
            <input type="number" placeholder="H" value={customH}
              onChange={(e) => setCustomH(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>

          <label>H scale (ft/in)</label>
          <input type="number" value={hScale} onChange={(e) => setHScale(Number(e.target.value))} />
          <label>V scale (ft/in)</label>
          <input type="number" value={vScale} onChange={(e) => setVScale(Number(e.target.value))} />

          <label>Distance unit</label>
          <select value={unit} onChange={(e) => setUnit(e.target.value)}>
            {units.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>

          <label>Vertical datum</label>
          <input value={datum} onChange={(e) => setDatum(e.target.value)} />

          <label>Stream name</label>
          <input value={stream} onChange={(e) => setStream(e.target.value)} />
          <label>Community</label>
          <input value={community} onChange={(e) => setCommunity(e.target.value)} />
          <label>County</label>
          <input value={county} onChange={(e) => setCounty(e.target.value)} />
          <label>State</label>
          <input value={state} onChange={(e) => setState(e.target.value)} />
          <label>Start station note</label>
          <input value={startDesc} onChange={(e) => setStartDesc(e.target.value)}
            placeholder="confluence with …" />
          <label>Start panel no.</label>
          <input type="number" value={panelNo} onChange={(e) => setPanelNo(Number(e.target.value))} />

          {format === "dxf" && (
            <>
              <label>DXF version</label>
              <select value={dxfVer} onChange={(e) => setDxfVer(e.target.value)}>
                {dxfVersions.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
              <label>Options</label>
              <div className="row">
                <label><input type="checkbox" checked={grid} onChange={(e) => setGrid(e.target.checked)} /> Grid</label>
                <label><input type="checkbox" checked={titleBlock} onChange={(e) => setTitleBlock(e.target.checked)} /> Title block</label>
              </div>
            </>
          )}

          <label>Paneling</label>
          <div className="check-col">
            <label className="check">
              <input type="checkbox" checked={fitOne} onChange={(e) => setFitOne(e.target.checked)} />
              Fit entire reach on <b>one panel</b> (adjusts scale if needed)
            </label>
            <label className="check">
              <input type="checkbox" checked={useView} onChange={(e) => setUseView(e.target.checked)}
                disabled={fitOne} />
              Rebuild panels from stated H/V scale × page plot width
            </label>
            {lastInfo && <div className="hint">{lastInfo}</div>}
          </div>
        </section>

        <footer className="modal-actions">
          <button className="ghost" onClick={doFdt}>FDT PDF</button>
          <button className="ghost" onClick={doFirm}>FIRM shapefile ZIP</button>
          <span style={{ flex: 1 }} />
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button onClick={doExport}>Export {format.toUpperCase()}</button>
        </footer>
      </div>
    </div>
  );
}
