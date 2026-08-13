import type { GetProfileResponse } from "../api/client";

type Props = {
  profile: GetProfileResponse;
  selectedRs: string | null;
  onSelectRs: (rs: string) => void;
  onEditLetter?: (rs: string, letter: string) => void;
  onEditLabelText?: (rs: string, text: string) => void;
};

export default function ProfileTable({
  profile, selectedRs, onSelectRs, onEditLetter, onEditLabelText,
}: Props) {
  const rows: any[] = [];
  for (const c of profile.cross_sections) {
    rows.push({ kind: "xs", ...c });
    for (const s of profile.structures) {
      const sRs = parseFloat(s.rs);
      const cRs = parseFloat(c.rs);
      const next = profile.cross_sections[profile.cross_sections.indexOf(c) + 1];
      const nextRs = next ? parseFloat(next.rs) : -Infinity;
      if (Number.isFinite(sRs) && Number.isFinite(cRs)) {
        const belongs = cRs >= sRs && (!Number.isFinite(nextRs) || nextRs < sRs);
        const closest = profile.cross_sections.reduce((best, x) => {
          const d = Math.abs(parseFloat(x.rs) - sRs);
          return d < best.d ? { rs: x.rs, d } : best;
        }, { rs: c.rs, d: Infinity });
        if (belongs && closest.rs === c.rs && closest.d < 500) {
          rows.push({ kind: "struct", ...s, cumdist: s.cumdist ?? c.cumdist });
        }
      }
    }
  }

  const extraProfiles = (profile.profiles_selected ?? [profile.profile]).filter(
    (n) => n !== profile.profile,
  );

  return (
    <div className="table-scroll">
      <table className="fis">
        <thead>
          <tr>
            <th>RIVER</th>
            <th>REACH</th>
            <th>RS</th>
            <th>STRUC</th>
            <th>LENGTHCHNL</th>
            <th>CUMDIST</th>
            <th>MINCHEL</th>
            <th>LOWCHORD</th>
            <th>HIGHCHORD</th>
            <th>LETTER</th>
            <th>LABEL</th>
            <th>{profile.profile}</th>
            {extraProfiles.map((n) => (
              <th key={n}>{n}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            if (r.kind === "xs") {
              const isFlash = selectedRs === r.rs;
              return (
                <tr
                  key={`xs${i}`}
                  className={isFlash ? "flash" : ""}
                  onClick={() => onSelectRs(r.rs)}
                >
                  <td>{r.river}</td>
                  <td>{r.reach}</td>
                  <td>{r.rs}</td>
                  <td></td>
                  <td>{fmt(r.length_channel)}</td>
                  <td>{fmt(r.cumdist)}</td>
                  <td>{fmt(r.minchel)}</td>
                  <td></td>
                  <td></td>
                  <td className="letter" onClick={(e) => e.stopPropagation()}>
                    <input
                      className="cell-input"
                      defaultValue={r.label_letter ?? ""}
                      maxLength={6}
                      onBlur={(e) => {
                        const v = e.target.value.trim();
                        if (v !== (r.label_letter ?? "") && onEditLetter) onEditLetter(r.rs, v);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                    />
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      className="cell-input cell-input--wide"
                      defaultValue={r.label_text ?? ""}
                      onBlur={(e) => {
                        const v = e.target.value.trim();
                        if (v !== (r.label_text ?? "") && onEditLabelText) onEditLabelText(r.rs, v);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                    />
                  </td>
                  <td>{fmt(r.wsel)}</td>
                  {extraProfiles.map((n) => (
                    <td key={n}>{fmt(r.wsel_by_profile?.[n])}</td>
                  ))}
                </tr>
              );
            }
            return (
              <tr key={`st${i}`} className="struct">
                <td>{r.river}</td>
                <td>{r.reach}</td>
                <td>{r.rs}</td>
                <td>{r.type}</td>
                <td>{fmt(r.weir_width)}</td>
                <td>{fmt(r.cumdist)}</td>
                <td>{fmt(r.minchel)}</td>
                <td>{fmt(r.low_chord)}</td>
                <td>{fmt(r.high_chord)}</td>
                <td></td>
                <td></td>
                <td></td>
                {extraProfiles.map((n) => (
                  <td key={n}></td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "";
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}
