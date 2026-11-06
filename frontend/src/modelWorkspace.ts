export type ReachInventory = {
  id: string;
  river_id: string;
  river: string;
  reach: string;
  xs_count: number;
  structure_count: number;
  station_extent: [string, string] | null;
  upstream_connection: Endpoint | null;
  downstream_connection: Endpoint | null;
  topology_status: string;
  component_id?: string | null;
  source_order_status?: string;
  source_order_provenance?: Record<string, unknown>;
};

export type Endpoint = {
  connection_type: string;
  connection_name: string;
  endpoint_distance: number | null;
};

export type TopologyConnection = {
  id: string;
  junction_id: string;
  upstream_reach_id: string;
  downstream_reach_id: string;
  upstream_component: number | null;
  downstream_component: number | null;
  transition_distance: number | null;
  status: string;
  provenance: Record<string, unknown>;
};

export type ModelInventory = {
  model_context: {model_id: string; generation: number; session_id: string};
  model: any;
  reaches: ReachInventory[];
  junctions: Array<{id: string; name: string; upstream_reach_ids: string[]; downstream_reach_ids: string[]}>;
  connections: TopologyConnection[];
  components?: string[][];
  cycle_reach_ids?: string[];
  branch_junction_ids?: string[];
  topology_warnings?: string[];
};

export type OrderedReach = ReachInventory & {
  topology_level: number | null;
  branch_ambiguous: boolean;
  topology_warning: string | null;
};

export type ReachComponent = {
  id: string;
  reaches: OrderedReach[];
  warning: string | null;
};

const reachKey = (reach: ReachInventory) => `${reach.river}\u0000${reach.reach}\u0000${reach.id}`.toLocaleLowerCase();

/**
 * Return model reaches in flooding-source assembly direction (downstream to
 * upstream). Connections themselves are stored in physical flow direction
 * (upstream to downstream). Equal topology levels are branches, not a claimed
 * linear hydraulic order; their label/id sort is display-only and deterministic.
 */
export function topologyReachComponents(inventory: ModelInventory): ReachComponent[] {
  const byId = new Map(inventory.reaches.map(reach => [reach.id, reach]));
  const ids = new Set(byId.keys());
  const downstream = new Map<string, Set<string>>();
  const upstream = new Map<string, Set<string>>();
  const undirected = new Map<string, Set<string>>([...ids].map(id => [id, new Set()]));
  for (const connection of inventory.connections) {
    const up = connection.upstream_reach_id;
    const down = connection.downstream_reach_id;
    if (!ids.has(up) || !ids.has(down) || up === down) continue;
    (downstream.get(up) ?? downstream.set(up, new Set()).get(up)!).add(down);
    (upstream.get(down) ?? upstream.set(down, new Set()).get(down)!).add(up);
    undirected.get(up)!.add(down);
    undirected.get(down)!.add(up);
  }

  const unseen = new Set(ids);
  const components: ReachComponent[] = [];
  while (unseen.size) {
    const seed = [...unseen].sort((a, b) => reachKey(byId.get(a)!).localeCompare(reachKey(byId.get(b)!)))[0];
    const members: string[] = [];
    const queue = [seed];
    unseen.delete(seed);
    while (queue.length) {
      const id = queue.shift()!;
      members.push(id);
      for (const neighbor of [...(undirected.get(id) ?? [])].sort()) {
        if (unseen.delete(neighbor)) queue.push(neighbor);
      }
    }

    const memberSet = new Set(members);
    const terminals = members.filter(id => ![...(downstream.get(id) ?? [])].some(item => memberSet.has(item)));
    const level = new Map<string, number>();
    const traverse = [...terminals].sort();
    traverse.forEach(id => level.set(id, 0));
    while (traverse.length) {
      const id = traverse.shift()!;
      const nextLevel = level.get(id)! + 1;
      for (const next of [...(upstream.get(id) ?? [])].filter(item => memberSet.has(item)).sort()) {
        if (!level.has(next) || nextLevel < level.get(next)!) {
          level.set(next, nextLevel);
          traverse.push(next);
        }
      }
    }
    const cycle = members.some(id => !level.has(id));
    const counts = new Map<number, number>();
    level.forEach(value => counts.set(value, (counts.get(value) ?? 0) + 1));
    const ordered = members.map(id => byId.get(id)!).sort((a, b) => {
      const al = level.get(a.id) ?? Number.MAX_SAFE_INTEGER;
      const bl = level.get(b.id) ?? Number.MAX_SAFE_INTEGER;
      return al - bl || reachKey(a).localeCompare(reachKey(b));
    }).map(reach => ({
      ...reach,
      topology_level: level.get(reach.id) ?? null,
      branch_ambiguous: level.has(reach.id) && (counts.get(level.get(reach.id)!) ?? 0) > 1,
      topology_warning: cycle ? "Cycle or unsupported connection prevents a complete linear topology order." : null,
    }));
    components.push({id: ordered.map(item => item.id).sort().join("|"), reaches: ordered, warning: cycle ? "Cycle or unsupported connection detected; verify this component manually." : null});
  }
  return components.sort((a, b) => reachKey(a.reaches[0]).localeCompare(reachKey(b.reaches[0])));
}

export function topologyReachOrder(inventory: ModelInventory) {
  return topologyReachComponents(inventory).flatMap(component => component.reaches.map(reach => reach.id));
}

export function addReachInAssemblyOrder(inventory: ModelInventory, selected: string[], reachId: string, manualOrder = false) {
  if (selected.includes(reachId) || !inventory.reaches.some(reach => reach.id === reachId)) return selected;
  if (!manualOrder) {
    const rank = new Map(topologyReachOrder(inventory).map((id, index) => [id, index]));
    return [...selected, reachId].sort((a, b) => (rank.get(a) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b) ?? Number.MAX_SAFE_INTEGER));
  }
  // Preserve every existing pair's relative order after a deliberate manual move.
  // Insert where it creates the most authoritative adjacent connections.
  let best = [...selected, reachId];
  let bestScore = -1;
  for (let index = 0; index <= selected.length; index++) {
    const candidate = [...selected.slice(0, index), reachId, ...selected.slice(index)];
    let score = 0;
    if (index > 0 && connectionBetween(inventory, candidate[index - 1], reachId).length === 1) score += 2;
    if (index < selected.length && connectionBetween(inventory, reachId, candidate[index + 1]).length === 1) score += 2;
    if (score > bestScore) { best = candidate; bestScore = score; }
  }
  return best;
}

export function groupReaches(reaches: ReachInventory[]) {
  const groups = new Map<string, ReachInventory[]>();
  for (const reach of reaches) groups.set(reach.river, [...(groups.get(reach.river) ?? []), reach]);
  return [...groups.entries()].map(([river, items]) => ({river, reaches: items}));
}

export function connectionBetween(inventory: ModelInventory, downstreamId: string, upstreamId: string) {
  return inventory.connections.filter(connection =>
    connection.downstream_reach_id === downstreamId && connection.upstream_reach_id === upstreamId);
}

export function upstreamChoices(inventory: ModelInventory, downstreamId: string, selected: string[]) {
  return inventory.connections
    .filter(connection => connection.downstream_reach_id === downstreamId && !selected.includes(connection.upstream_reach_id))
    .map(connection => ({connection, reach: inventory.reaches.find(reach => reach.id === connection.upstream_reach_id)!}))
    .filter(item => item.reach);
}

export function moveReach(selected: string[], index: number, delta: -1 | 1) {
  const target = index + delta;
  if (target < 0 || target >= selected.length) return selected;
  const next = [...selected];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function localPathStatus(inventory: ModelInventory, selected: string[]) {
  const messages: string[] = [];
  const connections: Array<{status: string; connection?: TopologyConnection}> = [];
  if (new Set(selected).size !== selected.length) messages.push("A flooding source cannot contain the same reach more than once.");
  for (const id of selected) {
    if (!inventory.reaches.some(reach => reach.id === id)) messages.push(`Selected reach ${id} is not present in the active model inventory.`);
  }
  for (let index = 0; index < selected.length - 1; index++) {
    const matches = connectionBetween(inventory, selected[index], selected[index + 1]);
    if (matches.length !== 1) {
      const status = matches.length > 1 ? "AMBIGUOUS" : "BLOCKED";
      connections.push({status});
      messages.push(matches.length > 1 ? "Multiple source connections match this reach pair." : "Selected reaches are not connected in downstream-to-upstream order.");
    } else {
      const connection = matches[0];
      const status = connection.status === "SUPPORTED" && connection.transition_distance != null ? "CONNECTED" : connection.status === "MISSING" ? "UNKNOWN" : connection.status;
      connections.push({status, connection});
      if (status !== "CONNECTED") messages.push("A selected junction transition lacks an authoritative distance.");
    }
  }
  return {ready: selected.length > 0 && messages.length === 0, messages, connections};
}

export function overviewCounts(inventory: ModelInventory) {
  return {
    rivers: new Set(inventory.reaches.map(reach => reach.river_id || reach.river)).size,
    reaches: inventory.reaches.length,
    crossSections: inventory.reaches.reduce((sum, reach) => sum + reach.xs_count, 0),
    junctions: inventory.junctions.length,
    structures: inventory.reaches.reduce((sum, reach) => sum + reach.structure_count, 0),
    profiles: inventory.model?.profiles?.length ?? 0,
  };
}
