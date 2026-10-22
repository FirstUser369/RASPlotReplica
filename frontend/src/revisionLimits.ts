import type { RevisionLimitsResponse } from "./api/client";

export type RevisionDraft = {
  startInput: string;
  endInput: string;
  exactStart: number;
  exactEnd: number;
};

export type RevisionSubmission =
  | { ok: true; start: number; end: number; unchanged: boolean }
  | { ok: false; error: string };

export function displayRevisionBoundary(value: number) {
  return Number.isFinite(value) ? String(Number(value.toFixed(2))) : "";
}

export function revisionDraft(limits: RevisionLimitsResponse): RevisionDraft {
  return {
    startInput: displayRevisionBoundary(limits.revision_start_station),
    endInput: displayRevisionBoundary(limits.revision_end_station),
    exactStart: limits.revision_start_station,
    exactEnd: limits.revision_end_station,
  };
}

export function prepareRevisionSubmission(
  limits: RevisionLimitsResponse,
  startInput: string,
  endInput: string,
  startEdited: boolean,
  endEdited: boolean,
): RevisionSubmission {
  const parsedStart = Number(startInput);
  const parsedEnd = Number(endInput);
  const start = startEdited ? parsedStart : limits.revision_start_station;
  const end = endEdited ? parsedEnd : limits.revision_end_station;

  if (!startInput.trim() || !endInput.trim() || !Number.isFinite(start) || !Number.isFinite(end)) {
    return { ok: false, error: "Revision limits must be numeric." };
  }
  if (start >= end) {
    return { ok: false, error: "Revision start station must be less than end station." };
  }
  if (start < limits.model_start_station || end > limits.model_end_station) {
    return { ok: false, error: "Revision limits must fall within the model extent." };
  }
  return {
    ok: true,
    start,
    end,
    unchanged: start === limits.revision_start_station && end === limits.revision_end_station,
  };
}
