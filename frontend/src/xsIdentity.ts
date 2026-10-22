export function workingRowsByIdentity(rows: any[]): Record<string, number> {
  const result: Record<string, number> = {};
  for (const row of rows ?? []) {
    if (row.kind !== "xs") continue;
    const identity = String(row.source_identity ?? "");
    if (!identity) throw new Error("Working cross section is missing its stable source identity.");
    if (result[identity] != null) throw new Error(`Duplicate working cross-section identity: ${identity}`);
    result[identity] = Number(row.id);
  }
  return result;
}

export function crossSectionByIdentity<T extends {xs_id: string}>(rows: T[], identity: string | null): T | undefined {
  return identity ? rows.find(row => row.xs_id === identity) : undefined;
}
