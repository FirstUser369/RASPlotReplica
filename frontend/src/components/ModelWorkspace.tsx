import {useEffect, useMemo, useState} from "react";
import {api, type FloodingSourceResponse} from "../api/client";
import {addReachInAssemblyOrder, connectionBetween, localPathStatus, moveReach, overviewCounts, topologyReachComponents, upstreamChoices, type ModelInventory} from "../modelWorkspace";

type Props = {
  sessionId: string;
  generation: number;
  inventory: ModelInventory;
  onBuild: (result: FloodingSourceResponse) => Promise<void>;
};

function statusClass(status: string) { return `model-status model-status--${status.toLowerCase()}`; }
function reachLabel(inventory: ModelInventory, id: string) {
  const reach = inventory.reaches.find(item => item.id === id);
  return reach ? `${reach.river} / ${reach.reach}` : id;
}

export default function ModelWorkspace({sessionId, generation, inventory, onBuild}: Props) {
  const [name, setName] = useState("");
  const [provenance, setProvenance] = useState<"USER_DEFINED" | "ENGINEERING_SUPPLIED">("USER_DEFINED");
  const [selected, setSelected] = useState<string[]>([]);
  const [manualOrder, setManualOrder] = useState(false);
  const [preview, setPreview] = useState<FloodingSourceResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");
  const counts = useMemo(() => overviewCounts(inventory), [inventory]);
  const components = useMemo(() => topologyReachComponents(inventory), [inventory]);
  const local = useMemo(() => localPathStatus(inventory, selected), [inventory, selected]);
  const choices = selected.length ? upstreamChoices(inventory, selected[selected.length - 1], selected) : [];

  useEffect(() => {
    setName(""); setProvenance("USER_DEFINED"); setSelected([]); setManualOrder(false); setPreview(null); setError("");
  }, [inventory.model_context.model_id, sessionId, generation]);

  useEffect(() => {
    let current = true;
    setPreview(null);
    if (!selected.length || !name.trim()) return () => { current = false; };
    setValidating(true);
    api.validateFloodingSource(sessionId, {display_name: name.trim(), naming_provenance: provenance, reach_ids: selected})
      .then(result => { if (current) setPreview(result); })
      .catch(reason => { if (current) setError(String(reason.message ?? reason)); })
      .finally(() => { if (current) setValidating(false); });
    return () => { current = false; };
  }, [sessionId, generation, name, provenance, selected]);

  const backendReady = preview?.flooding_source.topology_validation_status === "SUPPORTED" && preview?.continuous_distance.status === "SUPPORTED";
  const ready = Boolean(name.trim() && local.ready && backendReady);
  const selectedXs = selected.reduce((sum, id) => sum + (inventory.reaches.find(reach => reach.id === id)?.xs_count ?? 0), 0);

  async function build() {
    if (!ready || building) return;
    setBuilding(true); setError("");
    try {
      const result = await api.buildFloodingSource(sessionId, {display_name: name.trim(), naming_provenance: provenance, reach_ids: selected});
      await onBuild(result);
    } catch (reason: any) { setError(String(reason.message ?? reason)); }
    finally { setBuilding(false); }
  }

  const capabilities = inventory.model.capabilities ?? {};
  const capabilityRows = [
    ["Geometry", capabilities.geometry_available], ["Hydraulic results", capabilities.results_available],
    ["Cross sections", capabilities.cross_sections_available], ["Junction distances", capabilities.junction_distances_available],
  ].filter(([, status]) => status);

  return <main className="model-workspace">
    <section className="model-overview" aria-labelledby="model-overview-title">
      <div className="model-section-heading"><div><h2 id="model-overview-title">Model Overview</h2><p>Inspect the model and define one connected flooding source before entering the engineering workspace.</p></div></div>
      <dl className="model-identity">
        <div><dt>HEC-RAS</dt><dd>{inventory.model.ras_version || "UNKNOWN"}</dd></div>
        <div><dt>Units</dt><dd>{inventory.model.source_units || "UNKNOWN"}{inventory.model.source_units === "SI" ? " source → canonical working ft" : ""}</dd></div>
        <div><dt>Plan</dt><dd title={inventory.model.name}>{inventory.model.name || "UNKNOWN"}</dd></div>
        <div><dt>Geometry</dt><dd title={capabilities.geometry_identity?.name}>{capabilities.geometry_identity?.name || capabilities.geometry_identity?.path || "UNKNOWN"}</dd></div>
        <div><dt>Datum</dt><dd>{inventory.model.source_datum || capabilities.datum_status || "UNKNOWN"}</dd></div>
      </dl>
      <div className="model-counts">
        {[ ["Rivers", counts.rivers], ["Reaches", counts.reaches], ["Cross Sections", counts.crossSections], ["Junctions", counts.junctions], ["Structures", counts.structures], ["Hydraulic Profiles", counts.profiles] ].map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}
      </div>
      <div className="capability-strip">
        {capabilityRows.map(([label, status]) => <span key={label}><b className={statusClass(String(status))}>{status}</b> {label}</span>)}
      </div>
      {(capabilities.structures_detected ?? []).map((item: any) => <div className="model-notice" key={`${item.type}:${item.status}`}><b className={statusClass(item.status)}>{item.status}</b><span><strong>{item.type}</strong> detected. {item.status === "UNVALIDATED" ? "Inventory is available; authoritative engineering interpretation has not been validated." : "Source inventory is available."}</span></div>)}
      {(capabilities.workflow_capabilities ?? []).map((item: any, index: number) => <div className="model-notice" key={`${item.feature}:${item.entity?.identity ?? item.entity?.source_id ?? index}`}><b className={statusClass(item.status)}>{String(item.status).replaceAll("_", " ")}</b><span><strong>{item.feature}</strong> — {item.reason} <small>Affects: {(item.affected_stages ?? []).join(", ")}</small></span></div>)}
      {(capabilities.unit_system === "SI" || inventory.model.source_units === "SI") && capabilities.workflow_capabilities == null ? <div className="model-notice"><b className={statusClass("UNVALIDATED")}>INSPECTION ONLY</b><span>SI units detected. Open the model to verify and apply the canonical engineering-unit contract.</span></div> : null}
    </section>

    <div className="model-workspace-grid">
      <section className="model-panel reach-inventory"><h3>River / Reach Inventory</h3>
        <p className="model-help">Available reaches are grouped by connected component and displayed downstream → upstream. Reaches marked Branch share a topology level; their display order is stable but is not a hydraulic branch choice.</p>
        {(inventory.topology_warnings ?? []).map(warning => <p className="topology-order-warning" key={warning}>{warning}</p>)}
        {components.map((component, componentIndex) => <details key={component.id} open><summary>Connected component {componentIndex + 1} <small>{component.reaches.length} reach{component.reaches.length === 1 ? "" : "es"}</small></summary>
          {component.warning && <p className="topology-order-warning">{component.warning}</p>}
          <div className="reach-list">{component.reaches.map(reach => <article key={reach.id} className={selected.includes(reach.id) ? "reach-card reach-card--selected" : "reach-card"}>
            <div><strong>{reach.river} / {reach.reach}</strong><span>{reach.xs_count} XS · {reach.structure_count} structures {reach.branch_ambiguous ? "· Branch" : ""}</span></div>
            <div><span>Station extent</span><b>{reach.station_extent?.join(" – ") ?? "UNKNOWN"}</b></div>
            <div><span>Downstream</span><b>{reach.downstream_connection?.connection_name || reach.downstream_connection?.connection_type || "UNKNOWN"}</b></div>
            <div><span>Upstream</span><b>{reach.upstream_connection?.connection_name || reach.upstream_connection?.connection_type || "UNKNOWN"}</b></div>
            <div><span>Topology</span><b className={statusClass(reach.topology_status)}>{reach.topology_status}</b></div>
            <div title={String(reach.source_order_provenance?.rule ?? "Source ordering metadata unavailable")}><span>XS ordering</span><b className={statusClass(reach.source_order_status ?? "UNKNOWN")}>{reach.source_order_status ?? "UNKNOWN"}</b></div>
            <button type="button" disabled={selected.includes(reach.id)} title="Insert in downstream-to-upstream assembly order" onClick={() => setSelected(items => addReachInAssemblyOrder(inventory, items, reach.id, manualOrder))}>Add to source</button>
          </article>)}</div>
        </details>)}
      </section>

      <section className="model-panel topology-panel"><h3>Topology</h3><p className="model-help"><strong>Arrow direction: upstream → downstream flow.</strong> This is opposite the flooding-source assembly list, which is displayed downstream → upstream. Selected branch connections are highlighted.</p>
        <div className="topology-network">{inventory.connections.length ? inventory.connections.map(connection => {
          const active = selected.some((id, index) => connectionBetween(inventory, id, selected[index + 1] ?? "").some(item => item.id === connection.id));
          const junction = inventory.junctions.find(item => item.id === connection.junction_id);
          return <div className={active ? "topology-edge topology-edge--selected" : "topology-edge"} key={connection.id}>
            <span>{reachLabel(inventory, connection.upstream_reach_id)}</span><b>→ {junction?.name || "Junction"} →</b><span>{reachLabel(inventory, connection.downstream_reach_id)}</span><em className={statusClass(connection.status)}>{connection.status}</em>
          </div>;
        }) : <p>No junction connections were reported. A single reach can still be used.</p>}</div>
      </section>

      <section className="model-panel source-builder"><h3>Flooding Source Builder</h3><div className="source-fields">
        <label><span>Flooding source name</span><input value={name} onChange={event => setName(event.target.value)} placeholder="Enter an engineering display name" /></label>
        <label><span>Naming provenance</span><select value={provenance} onChange={event => setProvenance(event.target.value as any)}><option value="USER_DEFINED">User defined</option><option value="ENGINEERING_SUPPLIED">Engineering supplied</option></select></label>
      </div><div className="direction-label">ASSEMBLY ORDER: DOWNSTREAM → UPSTREAM · {manualOrder ? "MANUAL ORDER (preserved)" : "AUTOMATIC TOPOLOGY ORDER"}</div>
        <ol className="source-path">{selected.map((id, index) => {
          const reach = inventory.reaches.find(item => item.id === id)!;
          const link = local.connections[index];
          return <li key={id}><div className="source-segment"><div><strong>{reach.river} / {reach.reach}</strong><span>{reach.xs_count} cross sections</span></div><div className="source-actions"><button aria-label={`Move ${reach.reach} downstream`} disabled={!index} onClick={() => { setManualOrder(true); setSelected(moveReach(selected, index, -1)); }}>↑</button><button aria-label={`Move ${reach.reach} upstream`} disabled={index === selected.length - 1} onClick={() => { setManualOrder(true); setSelected(moveReach(selected, index, 1)); }}>↓</button><button onClick={() => setSelected(items => items.filter(item => item !== id))}>Remove</button></div></div>
            {index < selected.length - 1 && <details className={`source-connection source-connection--${link?.status.toLowerCase()}`}><summary><b>{link?.status ?? "BLOCKED"}</b>{link?.connection?.transition_distance != null ? ` · ${link.connection.transition_distance.toFixed(2)} ft` : " · connection unavailable"}</summary>{link?.connection && <div>{link.connection.upstream_component ?? "?"} ft + {link.connection.downstream_component ?? "?"} ft = {link.connection.transition_distance ?? "UNKNOWN"} ft<br/>Source: HEC-RAS geometry connectivity</div>}</details>}
          </li>;
        })}</ol>
        {!selected.length && <p className="builder-empty">Add any reach. It will be inserted automatically in downstream-to-upstream topology order.</p>}
        {choices.length > 0 && <div className="branch-assist"><strong>{choices.length === 1 ? "One valid upstream continuation" : "Branch selection required — choose one continuation; branches are not selected automatically"}</strong>{choices.map(({reach, connection}) => <button key={reach.id} onClick={() => setSelected(items => addReachInAssemblyOrder(inventory, items, reach.id, manualOrder))}>{reach.river} / {reach.reach}{connection.transition_distance != null ? ` · ${connection.transition_distance.toFixed(2)} ft` : " · distance unknown"}</button>)}</div>}
      </section>

      <section className="model-panel validation-preview"><h3>Validation Preview</h3><dl><div><dt>Flooding source</dt><dd>{name.trim() || "Not named"}</dd></div><div><dt>Reach segments</dt><dd>{selected.length}</dd></div><div><dt>Junction transitions</dt><dd>{Math.max(0, selected.length - 1)}</dd></div><div><dt>Selected-source XS</dt><dd>{selectedXs}</dd></div><div><dt>Hydraulic profiles</dt><dd>{counts.profiles}</dd></div></dl>
        <div className={ready ? "build-status build-status--ready" : "build-status build-status--blocked"}><strong>{validating ? "VALIDATING" : ready ? "READY TO BUILD" : "BLOCKED"}</strong>
          {!name.trim() && <span>Enter a flooding-source name.</span>}{local.messages.map(message => <span key={message}>{message}</span>)}{preview?.flooding_source.validation_messages.map(message => <span key={message}>{message}</span>)}{preview?.continuous_distance.messages.map(message => <span key={message}>{message}</span>)}{error && <span>{error}</span>}
        </div><button className="build-profile" type="button" disabled={!ready || validating || building} onClick={build}>{building ? "Building Profile…" : "Build Profile"}</button>
      </section>
    </div>
  </main>;
}
