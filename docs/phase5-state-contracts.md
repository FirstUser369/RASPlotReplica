# Phase 5: enforcement and state integrity

The authoritative release scope and updated retention policy are in
[support-matrix.md](support-matrix.md).

This phase does not expand HEC-RAS engineering support. The Thursday package is
not rebuilt. Existing source work from Phases 1–4 remains the baseline.

## Stationing

The working database seals a fingerprint of the ordered working records,
identities, interval lengths, starting distance, connectivity and source
structure inputs used by positioning. A changed dependency makes CUMDIST DIRTY;
matching the old final extent is not sufficient. No-op edits remain fresh.

Preview, panel generation, dependent range operations and export reject DIRTY
stationing. The user explicitly recalculates. Successful recalculation seals the
new inputs, preserves source lengths and preserves manual panels. Starting
distance is applied atomically as part of that explicit operation.

## Profile identities

Working hydraulic dictionaries and API selectors use profile IDs exclusively.
Source/display names are labels, including names identical to another ID.
The historical `profile_names` request field now contains IDs; old clients that
send display names must migrate. No implicit ID-or-name lookup remains.

## Inventory and hydraulic meaning

Inventory states distinguish VERIFIED_NONE, VERIFIED_PARSED, INCOMPLETE,
UNSUPPORTED and MALFORMED. Structure-related geometry, source results and linked
text cannot be silently discarded to establish an empty inventory. Missing or
inconsistent inventory blocks authoritative export; it never creates invented
structures. Malformed required identity records produce compatibility errors.

Panel Setup consumes the backend's shared hydraulic segments for the selected
profile. Missing WSEL is not replaced by another profile or ground. Ground has
its own dashed representation. Unknown datum remains UNKNOWN, including through
the legacy PDF endpoint. Auto End Elevation ignores null/nonfinite values.

## Panels

Opening Panel Setup creates a local draft. Auto Generate requests a validated
draft without modifying the committed panels or header. Cancel discards the
draft. Apply validates and commits panel definitions and records their reviewed
dependency fingerprint. Independent origins and scales are retained.

Panel identity, numeric bounds, scales, physical page fit and full requested
range coverage are validated. Overlap is allowed. Deliberate incomplete exports
are not supported: narrow Revision Limits or provide complete coverage. Error
messages list uncovered intervals. The generation ceiling is 500 panels; use a
larger horizontal scale or a smaller range when exceeded. Generation uses a
bounded count and verifies a finite positive station step.

Stationing, Revision Limits, hydraulic/ground edits, selected profiles and
backwater values invalidate saved-panel review. Definitions are preserved.
Reverting to identical dependency values restores the same evidence. Saved
panels require review/Apply before export. Fresh explicitly generated export
panels are independently validated without replacing saved definitions.
Export still enforces hydraulic vertical fit and all Phase 3/4 preflight rules.

Working databases, manual panels and edits are retained separately per reach;
switching reaches does not reconstruct and discard an edited database.

## Requests and exports

Frontend context generations discard obsolete success and error responses.
Dialog drafts also have open/close generations. API requests carry a browser
context token and expected reach. Under the state lock, superseded requests or
requests for an obsolete active reach are rejected before mutation.

Local API transactions are serialized with a reentrant lock. Exports deep-copy
the database, apply options to the snapshot, preflight it and render from it.
This deliberately favors deterministic local operation over parallel rendering;
Matplotlib is not used concurrently. Exports do not persist their options into
the live session. Artifacts have unpredictable independent filenames.

FDT/FIRM implementations remain in source but their normal endpoints return a
disabled/validation-pending error. They are not offered as authoritative actions.

## File safety and errors

Static paths and artifact paths require resolved containment. Downloads and
merge inputs require a registered artifact owned by that session. Merge output
accepts a plain PDF filename only and generates a unique session-owned path.
Cache redirection through filesystem links is rejected. Session IDs are random.

Artifacts expire after 24 hours. Lazy cleanup skips active downloads and removes
only registered/marked artifacts, including expired marked files after restart.
Sessions expire after 24 hours of inactivity, with a 32-session limit; export
capacity is 256 artifacts/session and 2,048 registered/process. Unmarked crash
residue is not blindly deleted. See the support matrix for the complete policy.

Loopback peer, local Host, matching Origin and Fetch Metadata checks apply to all
routes. API integration tests use Starlette's in-process test transport; that
special peer does not exist on a network connection.

Compatibility/engineering/format validation errors are actionable 4xx responses.
Filesystem and renderer failures remain software errors with actionable 500
messages, rather than being mislabeled unsupported engineering. Invalid
nonfinite request inputs cannot break JSON serialization of validation errors.

## Validation and boundaries

`backend/tests/test_phase5_contracts.py` covers the confirmed bypasses. The earlier
113 cases retain their engineering assertions; name-based test selectors were
migrated to the intentionally stricter ID contract. `frontend/tests/phase5.cjs`
tests finite elevations, segmentation and deferred response/context behavior.
`diagnostics/phase5_browser.py` uses installed Edge against the production bundle
and a current local backend, with temporary copies of North data; it does not
change either real fixture or the Thursday package.

Connected junction semantics, bridges, multiple openings, alternative culverts,
inline/gated structures and lateral extents still require real evidence. Muncie
full authoritative export remains blocked. SI conversion, datum transformation,
FDT/FIRM redesign, DXF import and raster integration remain out of scope.
Autodesk application acceptance remains separate from code/browser validation.
