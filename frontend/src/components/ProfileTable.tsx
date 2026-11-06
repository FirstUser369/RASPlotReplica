import { useEffect, useMemo, useRef, useState } from "react";
import type { GetProfileResponse } from "../api/client";

type Props = {
  profile: GetProfileResponse;
  selectedXsId: string | null;
  onSelectXs: (xsId: string) => void;
  onEditLetter?: (xsId: string, letter: string) => void;
  onEditLabelText?: (xsId: string, text: string) => void;
  onEditWorkingRow?: (
    xsId: string,
    field: "length_chnl" | "minchel" | "low_chord" | "high_chord",
    value: number | null,
  ) => Promise<void> | void;
  onEditProfileValue?: (xsId: string, profileName: string, value: number | null) => Promise<void> | void;
  workingByIdentity?: Record<string, number>;
  workspaceMode?: boolean;
  mode?: "compact" | "workspace";
};

export default function ProfileTable({
  profile,
  selectedXsId,
  onSelectXs,
  onEditLetter,
  onEditLabelText,
  onEditWorkingRow,
  onEditProfileValue,
  workingByIdentity,
  workspaceMode = false,
  mode,
}: Props) {
  const resolvedMode = mode ?? (workspaceMode ? "workspace" : "compact");
  const rows = useMemo(() => {
    const out: any[] = [];
    const pending = new Set(profile.structures);
    const structuresByUpstream = new Map<string, any[]>();
    for (const structure of profile.structures) {
      const key = `${structure.river}\u0000${structure.reach}\u0000${structure.connected_us_rs}`;
      const group = structuresByUpstream.get(key) ?? [];
      group.push(structure);
      structuresByUpstream.set(key, group);
    }
    for (const c of profile.cross_sections) {
      out.push({kind:"xs",...c});
      const key = `${c.river}\u0000${c.reach}\u0000${c.rs}`;
      for (const s of structuresByUpstream.get(key) ?? []) {
        out.push({kind:"struct",...s});
        pending.delete(s);
      }
    }
    for (const s of pending) out.push({kind:"struct",...s});
    return out;
  }, [profile]);

  const profileLabel = (id: string) => profile.profile_metadata?.find(p => p.profile_id === id)?.name ?? id;
  const extraProfiles = useMemo(
    () => (profile.profiles_selected ?? [profile.profile]).filter((n) => n !== profile.profile),
    [profile],
  );

  const tableRef = useRef<HTMLTableElement | null>(null);
  const pageSize = 500;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const visibleRows = useMemo(
    () => rows.slice(page * pageSize, (page + 1) * pageSize),
    [rows, page],
  );
  const [copiedText, setCopiedText] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);

  useEffect(() => {
    setPage(current => Math.min(current, pageCount - 1));
  }, [pageCount]);

  useEffect(() => {
    if (!selectedXsId || !workspaceMode) return;
    const index = rows.findIndex(row => (row.xs_id ?? row.row_identity) === selectedXsId);
    if (index >= 0) setPage(Math.floor(index / pageSize));
  }, [selectedXsId, workspaceMode, rows]);

  useEffect(() => {
    if (!selectedXsId || !tableRef.current || !workspaceMode) return;
    const selector = `[data-row-identity="${CSS.escape(String(selectedXsId))}"]`;
    const row = tableRef.current.querySelector(selector) as HTMLElement | null;
    row?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selectedXsId, workspaceMode, page]);

  function restoreOriginalValue(target: HTMLInputElement) {
    const original = target.dataset.originalValue ?? "";
    target.value = original;
  }

  function parseNumericValue(raw: string): number | null {
    const value = raw.trim();
    if (value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      throw new Error("Please enter a valid number.");
    }
    return parsed;
  }

  function focusNextEditableCell(current: HTMLInputElement, step: number) {
    const cells = Array.from(document.querySelectorAll<HTMLInputElement>(".editable-grid-cell"));
    const currentIndex = cells.indexOf(current);
    const next = cells[(currentIndex + step + cells.length) % cells.length];
    next?.focus();
    next?.select();
  }

  function commitFieldInput(target: HTMLInputElement, mode: "letter" | "label" | "numeric" | "profile") {
    const xsId = target.dataset.xsId ?? "";
    const field = target.dataset.field ?? "";
    const profileName = target.dataset.profileName ?? "";
    const original = target.dataset.originalValue ?? target.value;
    try {
      if (mode === "letter") {
        const value = target.value.trim();
        if (value !== (target.dataset.originalValue ?? "") && onEditLetter) {
          onEditLetter(xsId, value);
        }
        return;
      }
      if (mode === "label") {
        const value = target.value.trim();
        if (value !== (target.dataset.originalValue ?? "") && onEditLabelText) {
          onEditLabelText(xsId, value);
        }
        return;
      }
      if (mode === "numeric") {
        const numeric = parseNumericValue(target.value);
        if (field && onEditWorkingRow && xsId) {
          const current = (target.dataset.originalValue ?? "") === "" ? null : parseNumericValue(target.dataset.originalValue ?? "");
          const next = numeric;
          if (next !== current && onEditWorkingRow) {
            onEditWorkingRow(xsId, field as any, next);
          }
        }
        return;
      }
      if (mode === "profile") {
        const numeric = parseNumericValue(target.value);
        const current = (target.dataset.originalValue ?? "") === "" ? null : parseNumericValue(target.dataset.originalValue ?? "");
        const next = numeric;
        if (profileName && xsId && next !== current && onEditProfileValue) {
          onEditProfileValue(xsId, profileName, next);
        }
      }
    } catch (e: any) {
      setErrorText(e.message ?? "Invalid value");
      restoreOriginalValue(target);
      target.focus();
    }
  }

  function inputCellProps(
    row: any,
    field: string,
    value: string | number | null | undefined,
    mode: "letter" | "label" | "numeric" | "profile",
    profileName?: string,
  ) {
    const initial = value == null ? "" : String(value);
    return {
      className: "editable-grid-cell",
      "data-xs-id": row.xs_id ?? "",
      "data-field": field,
      "data-profile-name": profileName ?? "",
      "data-original-value": initial,
      defaultValue: initial,
      onFocus: (e: React.FocusEvent<HTMLInputElement>) => {
        e.currentTarget.dataset.originalValue = e.currentTarget.value;
      },
      onBlur: (e: React.FocusEvent<HTMLInputElement>) => {
        const target = e.currentTarget;
        const original = target.dataset.originalValue ?? target.value;
        const isChanged = target.value.trim() !== String(original).trim();
        if (!isChanged) return;
        commitFieldInput(target, mode);
      },
      onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Escape") {
          e.preventDefault();
          restoreOriginalValue(e.currentTarget);
          e.currentTarget.blur();
          return;
        }
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur();
          focusNextEditableCell(e.currentTarget, 1);
          return;
        }
        if (e.key === "Tab") {
          e.preventDefault();
          focusNextEditableCell(e.currentTarget, e.shiftKey ? -1 : 1);
          return;
        }
      },
      onCopy: (e: React.ClipboardEvent<HTMLInputElement>) => {
        const text = e.currentTarget.value;
        e.clipboardData.setData("text/plain", text);
        setCopiedText(text);
      },
      onPaste: (e: React.ClipboardEvent<HTMLInputElement>) => {
        const text = e.clipboardData.getData("text/plain");
        if (!text) return;
        const plain = text.replace(/\r/g, "").replace(/\n/g, "").replace(/\t/g, " ").trim();
        if (mode === "numeric" || mode === "profile") {
          try {
            const numeric = parseNumericValue(plain);
            e.currentTarget.value = String(numeric ?? "");
            // Let blur commit the value.
          } catch {
            e.preventDefault();
            setErrorText("Pasted value is not valid for this numeric cell.");
          }
          return;
        }
        e.preventDefault();
        e.currentTarget.value = plain;
      },
    };
  }

  const isCompact = resolvedMode === "compact";
  const stickyLefts: Record<number, number> = { 1: 0, 2: 90, 3: 180 };
  const stickyClass = (n: number) => (isCompact || n > 3 ? "" : `sticky-col sticky-col-${n}`);
  const columnWidths = [
    // RIVER, REACH, RS, STRUC, LENGTHCHNL, CUMDIST, MINCHEL, LOWCHORD, HIGHCHORD, LETTER, LABEL, primary profile
    90, 100, 90, 80, 120, 100, 120, 110, 110, 70, 120, 120,
    ...extraProfiles.map(() => 120),
  ];
  const profileCellClass = (row: any, profileId: string) => row.adjustment_by_profile?.[profileId]
    ? "profile-value-adjusted"
    : row.drawdown_by_profile?.[profileId] ? "profile-value-drawdown" : "";
  const profileCellExplanation = (row: any, profileId: string) => {
    const adjustment = row.adjustment_by_profile?.[profileId];
    if (adjustment) return adjustment.reason === "USER_FIX_DRAWDOWNS"
      ? `Automatically corrected by Fix Drawdowns. Original ${fmt(adjustment.original_value)}; working ${fmt(adjustment.adjusted_value)}. Source value remains preserved.`
      : `Manually adjusted working WSEL. Original ${fmt(adjustment.original_value)}; working ${fmt(adjustment.adjusted_value)}. Source value remains preserved.`;
    const drawdown = row.drawdown_by_profile?.[profileId];
    return drawdown ? `Detected drawdown: working WSEL ${fmt(drawdown.working_value)} is below the carried downstream level ${fmt(drawdown.qualifying_level)}. No correction has been applied.` : "";
  };

  return (
    <div className={`profile-table profile-table--${resolvedMode}`} aria-label={isCompact ? "Profile data summary" : "Editable engineering profile data"}>
      {workspaceMode && <div className="data-grid-legend"><span><i className="data-grid-legend__editable" />Editable working value</span><span><i className="data-grid-legend__source" />Read-only calculated/source value</span><span>Exact input precision is preserved</span></div>}
      {rows.length > pageSize && <nav className="large-table-pages" aria-label="Working table pages">
        <button type="button" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>Previous 500</button>
        <span>Rows {page * pageSize + 1}–{Math.min(rows.length, (page + 1) * pageSize)} of {rows.length}</span>
        <button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage(value => Math.min(pageCount - 1, value + 1))}>Next 500</button>
      </nav>}
      <div className={`table-scroll ${workspaceMode ? "table-scroll--workspace" : ""}`}>
        {errorText && <div className="table-error">{errorText}</div>}
        {copiedText != null && <div className="table-copy">Copied: {copiedText}</div>}
        <table ref={tableRef} className={`fis ${workspaceMode ? "fis--workspace" : ""}`}>
        <colgroup>
          {columnWidths.map((width, index) => (
            <col key={`col-${index}`} style={{ width: `${width}px`, minWidth: `${width}px` }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th className={stickyClass(1)} style={isCompact ? undefined : { left: `${stickyLefts[1]}px` }}>RIVER</th>
            <th className={stickyClass(2)} style={isCompact ? undefined : { left: `${stickyLefts[2]}px` }}>REACH</th>
            <th className={stickyClass(3)} style={isCompact ? undefined : { left: `${stickyLefts[3]}px` }}>RS</th>
            <th>STRUC</th>
            <th title="Working channel length in feet">LENGTHCHNL <small>ft</small></th>
            <th title="Calculated cumulative distance in feet">CUMDIST <small>ft</small></th>
            <th title="Minimum channel elevation in feet">MINCHEL <small>ft</small></th>
            <th>LOWCHORD <small>ft</small></th>
            <th>HIGHCHORD <small>ft</small></th>
            <th>LETTER</th>
            <th>LABEL</th>
            <th>{profileLabel(profile.profile)}{!isCompact && <small>Source / Working (working ft)</small>}</th>
            {extraProfiles.map((n) => (
              <th key={n}>{profileLabel(n)}{!isCompact && <small>Source / Working (working ft)</small>}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((r, visibleIndex) => {
            const i = page * pageSize + visibleIndex;
            if (r.kind === "xs") {
              const isFlash = selectedXsId === r.xs_id;
              return (
                <tr
                  key={r.xs_id ?? `xs${i}`}
                  className={isFlash ? "flash" : ""}
                  data-row-identity={r.xs_id}
                  onClick={() => onSelectXs(r.xs_id)}
                >
                  <td className={stickyClass(1)} style={isCompact ? undefined : { left: `${stickyLefts[1]}px` }}>{r.river}</td>
                  <td className={stickyClass(2)} style={isCompact ? undefined : { left: `${stickyLefts[2]}px` }}>{r.reach}</td>
                  <td className={stickyClass(3)} style={isCompact ? undefined : { left: `${stickyLefts[3]}px` }}>{r.rs}</td>
                  <td></td>
                  {isCompact ? (
                    <>
                      <td>{fmt(r.length_channel)}</td>
                      <td>{fmt(r.cumdist)}</td>
                      <td>{fmt(r.minchel)}</td>
                      <td>{fmt(r.low_chord)}</td>
                      <td>{fmt(r.high_chord)}</td>
                      <td className="letter">{r.label_letter ?? ""}</td>
                      <td>{r.label_text ?? ""}</td>
                      <td>{fmt(r.wsel)}</td>
                      {extraProfiles.map((n) => (
                        <td key={n}>{fmt(r.wsel_by_profile?.[n])}</td>
                      ))}
                    </>
                  ) : (
                    <>
                      <td className={profileCellClass(r, profile.profile)} title={profileCellExplanation(r, profile.profile)}>
                        <span className="source-profile-value">Source: {fmt(r.raw_wsel_by_profile?.[profile.profile]) || "Missing"}</span>
                        {(r.adjustment_by_profile?.[profile.profile] || r.drawdown_by_profile?.[profile.profile]) && <span className="profile-value-state">{r.adjustment_by_profile?.[profile.profile]?.reason === "USER_FIX_DRAWDOWNS" ? "Auto-corrected" : r.adjustment_by_profile?.[profile.profile] ? "Manual edit" : "Drawdown detected"}</span>}
                        <input
                          {...inputCellProps(r, "length_chnl", r.length_channel, "numeric")}
                          type="text"
                          inputMode="decimal"
                        />
                      </td>
                      <td>{fmt(r.cumdist)}</td>
                      <td>
                        <input
                          {...inputCellProps(r, "minchel", r.minchel, "numeric")}
                          type="text"
                          inputMode="decimal"
                        />
                      </td>
                      <td></td>
                      <td></td>
                      <td className="letter">
                        <input
                          {...inputCellProps(r, "label_letter", r.label_letter ?? "", "letter")}
                          className="editable-grid-cell cell-input"
                          maxLength={6}
                        />
                      </td>
                      <td>
                        <input
                          {...inputCellProps(r, "label_text", r.label_text ?? "", "label")}
                          className="editable-grid-cell cell-input cell-input--wide"
                        />
                      </td>
                      <td>
                        <input
                          {...inputCellProps(r, profile.profile, r.wsel, "profile", profile.profile)}
                          type="text"
                          inputMode="decimal"
                        />
                      </td>
                      {extraProfiles.map((n) => (
                        <td key={n} className={profileCellClass(r, n)} title={profileCellExplanation(r, n)}>
                          <span className="source-profile-value">Source: {fmt(r.raw_wsel_by_profile?.[n]) || "Missing"}</span>
                          {(r.adjustment_by_profile?.[n] || r.drawdown_by_profile?.[n]) && <span className="profile-value-state">{r.adjustment_by_profile?.[n]?.reason === "USER_FIX_DRAWDOWNS" ? "Auto-corrected" : r.adjustment_by_profile?.[n] ? "Manual edit" : "Drawdown detected"}</span>}
                          <input
                            {...inputCellProps(r, n, r.wsel_by_profile?.[n], "profile", n)}
                            type="text"
                            inputMode="decimal"
                          />
                        </td>
                      ))}
                    </>
                  )}
                </tr>
              );
            }
            return (
              <tr key={r.row_identity ?? `${r.structure_id ?? i}-${r.type}`} title={r.support_reason} className="struct" data-row-identity={r.row_identity} onClick={() => r.row_identity && onSelectXs(r.row_identity)}>
                <td className={stickyClass(1)} style={isCompact ? undefined : { left: `${stickyLefts[1]}px` }}>{r.river}</td>
                <td className={stickyClass(2)} style={isCompact ? undefined : { left: `${stickyLefts[2]}px` }}>{r.reach}</td>
                <td className={stickyClass(3)} style={isCompact ? undefined : { left: `${stickyLefts[3]}px` }}>{r.rs}</td>
                <td>{r.type}{r.support_status !== "VALIDATED" ? ` (${r.support_status ?? "UNSUPPORTED"})` : ""}</td>
                {isCompact ? (
                  <>
                    <td>{fmt(r.weir_width)}</td>
                    <td>{fmt(r.cumdist)}</td>
                    <td>{fmt(r.minchel)}</td>
                    <td>{fmt(r.low_chord)}</td>
                    <td>{fmt(r.high_chord)}</td>
                    <td></td>
                    <td title={r.name_provenance === "SOURCE" ? "Authoritative source structure name" : r.name_provenance === "USER_ENTERED" ? "Reviewer-entered structure name" : "No source structure name is available"}>{r.name ?? ""}</td>
                    <td></td>
                    {extraProfiles.map((n) => (
                      <td key={n}></td>
                    ))}
                  </>
                ) : (
                  <>
                    <td className="source-geometry-cell" title="Source-backed structure geometry is read-only">{fmt(r.weir_width)}</td>
                    <td>{fmt(r.cumdist)}</td>
                    <td className="source-geometry-cell" title="Source-backed structure geometry is read-only">{fmt(r.minchel)}</td>
                    <td className="source-geometry-cell" title="Source-backed structure geometry is read-only">{fmt(r.low_chord)}</td>
                    <td className="source-geometry-cell" title="Source-backed structure geometry is read-only">{fmt(r.high_chord)}</td>
                    <td></td>
                    <td>
                      <span className="source-structure-name">Source: {r.source_name || "Not provided"}</span>
                      {r.source_faces?.length ? <span className="source-structure-faces">{r.source_faces.map((face: any) => <span key={`${face.face}-${face.station}`}><strong>{face.face}</strong>: station {fmt(face.station)}, invert {fmt(face.invert)}, low chord {fmt(face.low_chord)}, high chord {fmt(face.high_chord)}</span>)}</span> : null}
                      <input
                        {...inputCellProps(r, "label_text", r.reviewer_name ?? "", "label")}
                        className="editable-grid-cell cell-input cell-input--wide"
                        placeholder="Optional reviewer name"
                        aria-label={`Reviewer structure name for ${r.river} ${r.reach} RS ${r.rs}`}
                      />
                      <small>{r.reviewer_name ? "Reviewer-entered override" : r.source_name ? "Using source name" : "No source name; optional reviewer entry"}</small>
                    </td>
                    <td></td>
                    {extraProfiles.map((n) => (
                      <td key={n}></td>
                    ))}
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
        </table>
      </div>
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "";
  return Number.isInteger(v) ? String(v) : Number(v.toFixed(4)).toString();
}
