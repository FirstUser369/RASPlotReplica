type Landmark = {
  id?: number;
  distance: number;
  elevation: number;
  text: string;
  orientation: string;
};

type Props = {
  landmarks: Landmark[];
  onChange: (items: Landmark[]) => void;
  onSave: () => void;
};

export default function LandmarksPanel({ landmarks, onChange, onSave }: Props) {
  function update(i: number, patch: Partial<Landmark>) {
    const next = landmarks.map((lm, idx) => (idx === i ? { ...lm, ...patch } : lm));
    onChange(next);
  }
  function remove(i: number) {
    onChange(landmarks.filter((_, idx) => idx !== i));
  }

  return (
    <div className="lm-panel">
      <div className="merge-panel__title">
        Landmarks
        <small> — double-click profile to add</small>
      </div>
      {landmarks.length === 0 && <div className="empty-mini">No landmarks yet.</div>}
      <ul className="lm-list">
        {landmarks.map((lm, i) => (
          <li key={lm.id ?? i}>
            <input
              className="cell-input"
              value={lm.text}
              aria-label={`Landmark ${i + 1} text`}
              placeholder="Label text"
              onChange={(e) => update(i, { text: e.target.value })}
            />
            <input
              className="cell-input"
              type="number"
              title="Distance (ft)"
              aria-label={`Landmark ${i + 1} distance in feet`}
              value={lm.distance}
              onChange={(e) => update(i, { distance: parseFloat(e.target.value) || 0 })}
            />
            <input
              className="cell-input"
              type="number"
              title="Elevation"
              aria-label={`Landmark ${i + 1} elevation`}
              value={lm.elevation}
              onChange={(e) => update(i, { elevation: parseFloat(e.target.value) || 0 })}
            />
            <select
              aria-label={`Landmark ${i + 1} orientation`}
              value={lm.orientation}
              onChange={(e) => update(i, { orientation: e.target.value })}
            >
              <option value="Vertical">V</option>
              <option value="Horizontal">H</option>
            </select>
            <button type="button" className="chip-x" onClick={() => remove(i)}>×</button>
          </li>
        ))}
      </ul>
      <button type="button" className="button button--primary" onClick={onSave}>Save landmarks</button>
    </div>
  );
}
