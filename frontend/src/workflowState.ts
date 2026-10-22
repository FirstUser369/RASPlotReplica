import type {GetProfileResponse} from "./api/client";

export const CUMDIST_DIRTY_MESSAGE = "CUMDIST is DIRTY. Recalculate CUMDIST before Preview, Panel Setup or export; saved panels are preserved for review.";

export function engineeringStateFromDb(db: any, currentError: string | null) {
  const stationingDirty = Boolean(db?.stationing_dirty);
  return {
    stationingDirty,
    panelsStale: Boolean(db?.panels_stale),
    notice: stationingDirty ? CUMDIST_DIRTY_MESSAGE : null,
    error: !stationingDirty && currentError === CUMDIST_DIRTY_MESSAGE ? null : currentError,
  };
}

export function mergeWorkingRows(profile: GetProfileResponse | null, db: any): GetProfileResponse | null {
  if (!profile) return null;
  const byIdentity = new Map<string, any>();
  for (const row of db?.working ?? []) {
    if (row.kind === "xs" && row.source_identity) byIdentity.set(String(row.source_identity), row);
  }
  return {
    ...profile,
    cross_sections: profile.cross_sections.map(row => {
      const working = byIdentity.get(row.xs_id);
      if (!working) return row;
      const selected = profile.profiles_selected ?? [profile.profile];
      const values = Object.fromEntries(selected.map(id => [id, working.profiles?.[id] ?? null]));
      return {
        ...row,
        length_channel: working.length_chnl,
        cumdist: working.cumdist,
        minchel: working.minchel,
        label_letter: working.label_letter,
        label_text: working.label_text,
        wsel: values[profile.profile] ?? row.wsel,
        wsel_by_profile: {...row.wsel_by_profile, ...values},
      };
    }),
  };
}

export function activeReachContext(activeSourceName: string | null, session: any, reachIdx: number) {
  if (activeSourceName) return {combined: true, label: activeSourceName};
  const reach = session?.reaches?.[reachIdx];
  return {combined: false, label: reach ? `${reach.river} / ${reach.reach}` : "Unknown reach"};
}

export function profileTableVisible(requested: boolean, workingTable: GetProfileResponse | null) {
  return requested && workingTable !== null;
}

export function engineeringPresentation(stationingDirty: boolean, activeSourceName: string | null) {
  return {
    chart: stationingDirty ? "RECALCULATION_REQUIRED" : "PROFILE",
    panels: stationingDirty ? "RECALCULATION_REQUIRED" : "EDITABLE",
    allowOriginalReachSelection: !activeSourceName,
  } as const;
}
