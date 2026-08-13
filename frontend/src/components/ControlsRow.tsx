type Props = {
  session: any;
  profileNames: string[];
  selectedProfiles: string[];
  onProfilesChange: (names: string[]) => void;
  reachIdx: number;
  onReachChange: (idx: number) => void;
  onOpenExport: () => void;
  onOpenProfileMapping: () => void;
  onFixDrawdowns: () => void;
  onAutoLetter: () => void;
  onRecalcCumdist: () => void;
  onNodeNames: (on: boolean) => void;
  useNodeNames: boolean;
};

export default function ControlsRow({
  session,
  profileNames,
  selectedProfiles,
  onProfilesChange,
  reachIdx,
  onReachChange,
  onOpenExport,
  onOpenProfileMapping,
  onFixDrawdowns,
  onAutoLetter,
  onRecalcCumdist,
  onNodeNames,
  useNodeNames,
}: Props) {
  const reaches = session?.reaches ?? [];
  const names: string[] = profileNames.length
    ? profileNames
    : (session?.profiles?.map((p: any) => String(p.name)) ?? []);
  const primary = selectedProfiles[0] ?? names[0] ?? "";

  function setPrimary(name: string) {
    const rest = selectedProfiles.filter((n) => n !== name);
    onProfilesChange([name, ...rest]);
  }
  function addOverlay(name: string) {
    if (!name || selectedProfiles.includes(name)) return;
    onProfilesChange([...selectedProfiles, name]);
  }
  function removeOverlay(name: string) {
    if (selectedProfiles.length <= 1) return;
    onProfilesChange(selectedProfiles.filter((n) => n !== name));
  }
  const addable = names.filter((n) => !selectedProfiles.includes(n));

  return (
    <div className="controls-row">
      <label>Reach:</label>
      <select value={reachIdx} onChange={(e) => onReachChange(parseInt(e.target.value, 10))}>
        {reaches.map((r: any, i: number) => (
          <option key={i} value={i}>
            {r.river} \ {r.reach} ({r.cross_sections?.length ?? 0} xs)
          </option>
        ))}
      </select>

      <label>Primary:</label>
      <select value={primary} onChange={(e) => setPrimary(e.target.value)}>
        {names.map((n) => (
          <option key={n} value={n}>{n}</option>
        ))}
      </select>

      <label>Overlay:</label>
      <div className="chip-row">
        {selectedProfiles.map((n) => (
          <span key={n} className={`chip ${n === primary ? "chip--primary" : ""}`}>
            {n}
            {selectedProfiles.length > 1 && (
              <button type="button" className="chip-x" onClick={() => removeOverlay(n)}>×</button>
            )}
          </span>
        ))}
      </div>
      <select value="" onChange={(e) => { if (e.target.value) addOverlay(e.target.value); e.target.value = ""; }}
        disabled={!addable.length}>
        <option value="">+ add profile…</option>
        {addable.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>

      <div className="tools">
        <button type="button" className="tool" onClick={onOpenProfileMapping} title="Review and assign FEMA frequency/use for each extracted profile">Profile Mapping</button>
        <button type="button" className="tool" onClick={onAutoLetter} title="Auto-letter cross sections">Letters</button>
        <button type="button" className="tool" onClick={onFixDrawdowns} title="Fix drawdowns (all profiles)">Drawdowns</button>
        <button type="button" className="tool" onClick={onRecalcCumdist} title="Recalculate CUMDIST">CUMDIST</button>
        <label className="tool-check" title="Import HEC-RAS node names as letters/text">
          <input type="checkbox" checked={useNodeNames} onChange={(e) => onNodeNames(e.target.checked)} />
          Node names
        </label>
      </div>

      <button onClick={onOpenExport}>Export PDF / DXF…</button>
    </div>
  );
}
