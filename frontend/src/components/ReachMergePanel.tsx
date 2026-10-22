type Reach = { river: string; reach: string; cross_sections?: any[]; structures?: any[] };

type Props = {
  reaches: Reach[];
  order: number[];
  junctions: number[];
  onOrderChange: (order: number[]) => void;
  onJunctionsChange: (j: number[]) => void;
  onApply: () => void;
};

export default function ReachMergePanel({
  reaches, order, junctions, onOrderChange, onJunctionsChange, onApply,
}: Props) {
  if (reaches.length < 2) return null;

  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    const next = order.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onOrderChange(next);
    // junctions are between consecutive items — keep length order-1
    const jn = junctions.slice(0, Math.max(0, next.length - 1));
    while (jn.length < next.length - 1) jn.push(0);
    onJunctionsChange(jn);
  }

  function setJunction(k: number, v: number) {
    const jn = junctions.slice();
    while (jn.length < order.length - 1) jn.push(0);
    jn[k] = v;
    onJunctionsChange(jn);
  }

  return (
    <div className="merge-panel">
      <div className="pane-section-heading"><div><strong>Reach assembly</strong><small>Downstream to upstream engineering order</small></div></div>
      <ol className="merge-list">
        {order.map((ri, i) => {
          const r = reaches[ri];
          if (!r) return null;
          return (
            <li key={`${ri}-${i}`}>
              <span className="merge-name">
                {r.river} \ {r.reach}
                <small> ({r.cross_sections?.length ?? 0} xs)</small>
              </span>
              <span className="merge-btns">
                <button type="button" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" disabled={i === order.length - 1} onClick={() => move(i, 1)}>↓</button>
              </span>
              {i < order.length - 1 && (
                <label className="jn">
                  junction&nbsp;
                  <input
                    type="number"
                    value={junctions[i] ?? 0}
                    onChange={(e) => setJunction(i, parseFloat(e.target.value) || 0)}
                  />
                  &nbsp;ft
                </label>
              )}
            </li>
          );
        })}
      </ol>
      <button type="button" className="button button--primary" onClick={onApply}>Apply reach order</button>
    </div>
  );
}
