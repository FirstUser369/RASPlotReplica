# P0 Phase 4: hydraulic meaning and validation

## Hydraulic and profile identity

WSEL association retains the Phase 1 exact river/reach/RS bijection with result-side geometry. Each profile now has a stable identity independent of its source/display name, source result type, file and dataset path. Steady identities identify source rows; unsteady identities identify saved rows or the derived envelope, scoped to the source plan/dataset. Renaming or mapping a profile does not change its identity or values.

Raw source values/states are retained in each working XS's `raw_profiles` and `hydraulic_values`, keyed by profile ID. The record also carries its complete XS identity. Working WSEL is separate. Unique source-name aliases remain in the legacy working-value table for compatibility; duplicate names use profile IDs, and ambiguous name selection is rejected. Display names are never sufficient to resolve duplicates.

The frontend selects IDs. Multi-profile requests serialize a JSON array, not a comma-separated list. Names containing commas, percent signs, plus signs and special characters remain intact. The source name, display name, legend/style and identity are separate fields.

## Frequency semantics

There is no name-based frequency inference, including for exact `1%`, `1%+`, `1% Future`, `1000-year` or `Maximum WSEL`. Unmapped frequency has provenance UNKNOWN. Current source adapters do not claim to extract trusted frequency metadata.

An explicit user mapping records USER_ENTERED provenance and updates legend, color, line type, weight and DXF layer together. Numeric annual-chance percent is separate from scenario/use labels; `1%+` and `1% Future` are not silently assigned numeric annual-chance meaning. Source names, IDs and hydraulic values remain unchanged. A derived saved-timestep envelope cannot be assigned annual-chance frequency through this interface.

## Unsteady maximum and time

Audit finding: the former maximum already used **all saved base-output timesteps**, not just the subset of profiles exposed in the UI. It is a separate maximum at each XS, not necessarily a simultaneous water surface, not a maximum over unsaved computation timesteps, and not an independently verified source-authoritative maximum.

The source-compatible name `Maximum WSEL` remains, with result type DERIVED_MAXIMUM_ENVELOPE and visible `derived saved-timestep envelope` wording. The model retains source dataset, row range and valid/total sample counts. Finite samples form the envelope; partial coverage remains PARTIAL_ENVELOPE. An all-missing column stays null. Undocumented sentinel involvement remains UNKNOWN_SENTINEL and blocks affected export.

The reader first checks `Time Date Stamp` on the parent unsteady time-series group, then the older cross-section location. It validates count against result rows, parses the observed HEC-RAS calendar format and requires strictly increasing timestamps. Strings are preserved; timezone remains UNKNOWN. Missing, malformed, duplicated, reversed or mismatched timestamps produce `Saved step N (source time unavailable)`, explicitly STEP_INDEX provenance. The numeric millisecond timestamp schema is not interpreted without a validated epoch/timezone contract.

Actual Muncie inspection: 289 saved rows (0–288), 34 exposed profiles including the envelope, and verified parent timestamps beginning `02JAN1900 00:00:00`, followed by five-minute intervals. Calendar year 1900 is preserved as source text, not interpreted as the real flood-event date.

## Missing, dry and nonfinite contract

| Source condition | Normalized numeric value | State |
| --- | --- | --- |
| Finite ordinary value, including zero | Preserved | VALID |
| Absent value | null | MISSING |
| NaN or either infinity | null; raw token retained as text | NONFINITE |
| Sentinel explicitly declared by dataset dry metadata | null; raw retained | DRY |
| Sentinel explicitly declared by missing/fill metadata | null; raw retained | MISSING |
| Undocumented -9999, -99999 or extreme negative sentinel candidate | null; raw retained | UNKNOWN_SENTINEL, affected export blocked |
| Unparseable value | null; raw retained | INVALID, affected export blocked |

Recognized sentinel attributes are `dry_value`/`Dry Value`, `missing_value`/`Missing Value` and `_FillValue` (case-insensitive). No universal DRY meaning is claimed for an undocumented sentinel number. A real dry/missing fixture is still required to validate other HEC-RAS versions/schemas. No value is replaced with zero. Missing WSEL datasets and geometry/result schema mismatches fail with source-context errors.

Preview retains nulls and explicitly disables gap connection. PDF and DXF split contiguous runs before clipping to panels/Revision Limits. They never remove a missing XS and connect its neighbors. Ground runs are also split. No hydraulic interpolation across a gap is authorized.

## Ground/invert and structures

Ground provenance identifies MINIMUM_BETWEEN_BANK_STATIONS. Missing arrays, invalid slices/banks, banks outside geometry, unordered station arrays, nonfinite geometry and empty bank slices do not silently fall back to a full-section minimum. They retain MISSING/INVALID state and block affected export. A user ground edit is recorded as USER_ENTERED with prior value/provenance and a preflight warning.

Phase 3 structure hydraulic rules remain enforced. No nearby XS WSEL is copied to a structure. No structure result adapter is certified. Structure points cannot enter authoritative WSEL lines merely through a non-null working-table value. The existing EXTRACTED/INTERPOLATED/COPIED_XS/DISPLAY_ONLY/MISSING structure provenance is preserved; source/working XS records separately distinguish SOURCE, DERIVED and USER_ENTERED.

## Fix Drawdowns and backwater

Fix Drawdowns remains an explicit user action, never an automatic repair of HEC-RAS output. It deterministically walks downstream to upstream, raising a lower working value to the carried value. A missing section resets the carry. Raw values stay unchanged; adjustments record original/adjusted values, profile identity/key, USER_FIX_DRAWDOWNS reason and USER_ENTERED provenance. Repeating the action without intervening edits adds no further changes. Preview/PDF/DXF legends disclose user-adjusted WSEL.

Backwater remains a USER_ENTERED_REFERENCE_ONLY elevation. Explicit JSON null clears it; omission leaves it unchanged. Nonfinite entries are rejected. Preview/PDF/DXF label the horizontal line as a user-entered reference with extent not calculated. The panel-width drawing is not a hydraulic spatial-extent claim.

## Units and datum

Source units come from the HDF root `Units System`. Both real fixtures explicitly report US Customary. Explicit SI/other unsupported units are rejected before feet-based processing. UNKNOWN units are retained for inspection but block authoritative export. No SI conversion is implemented. Internally constructed test databases declare their US Customary context separately; this is not an HDF unit fallback.

Source datum is read only from an explicit root `Vertical Datum` attribute in the current adapter. Horizontal projection names are not vertical datum evidence. Both current fixtures report UNKNOWN under this contract. Project/header/Preview defaults no longer claim NAVD88. User-entered datum has separate provenance; source datum remains unchanged. Output labels UNKNOWN when unresolved.

The export dialog offers `Deliverable requires a known datum`. Enabling it blocks export until datum is established. Otherwise an explicitly UNKNOWN-labelled output is allowed with a warning. No datum transformation is performed. FDT/FIRM remain outside this phase and are not covered by this PDF/DXF contract.

## Preflight

`GET /api/db/{sid}/hydraulic-preflight` returns hydraulic-specific errors/warnings. Preview receives the same information. PDF/DXF combine it with Phase 2 geometry/scale checks and Phase 3 structure preflight. A hydraulic-only `can_export` result is not an override of structure preflight.

Checks include profile identity and selection, source units, frequency provenance, prohibited envelope-frequency claims, finite working/backwater values, uncertain sentinel meaning, ground geometry and deliverable datum requirements. Errors identify the affected river/reach/RS/profile where applicable. Missing/dry/nonfinite source values remain null gaps with warnings; unverified sentinels and invalid source meaning block affected ranges. Structure hydraulic values without an approved adapter are rejected.

## Validation

- Full backend suite: **113 tests passed** (78 protected tests plus 35 new Phase 4 tests).
- TypeScript and Vite production build passed into a temporary folder. The existing bundle-size warning remains. Windows sandbox `spawn EPERM` required the normal build-process permission; no packaged assets were rebuilt.
- New tests cover all requested A–V topics, including true HTTP serialization for nonfinite WSEL, real serialized DXF gap geometry, captured PDF run geometry and Preview null values. Additional checks cover malformed timestamp ordering/count, unknown units/sentinels, absent datasets, bad geometry slices, known-datum requirements, explicit edit rejection and adjusted labels.
- No browser visual acceptance or new real-model acceptance for undocumented dry/missing schemas is claimed.
- Existing ezdxf deprecation warnings remain; no test failures.

Protected baselines remain: Muncie extent 15458.599994659424 ft; North extent 3229.0599632263184 ft; North faces 2629.289993286133 / 2519.289993286133 ft (110 ft apart); lateral zero-added-distance; Phase 1 identity/order/source lengths; PDF 13 x 9 inch physical axes; DXF inch units; saved panels; metadata isolation; Phase 3 connectivity and preflight. Muncie full authoritative export remains intentionally blocked by its unvalidated lateral geometry.

## Files changed in Phase 4

- New `backend/app/core/hydraulics.py`.
- `backend/app/core/hdf_reader.py`, `rasplot_db.py`, `export_geometry.py`, `dxf_export.py`.
- `backend/app/routers/hecras_api.py`, `report.py`.
- `frontend/src/App.tsx`, `frontend/src/api/client.ts`.
- `frontend/src/components/ControlsRow.tsx`, `ProfileChart.tsx`, `ProfileMappingDialog.tsx`, `ExportDialog.tsx`.
- New `backend/tests/test_hydraulics.py` and this report.

Earlier phase/user changes remain intact. No Thursday package rebuild, commit or push.

## Remaining UNKNOWN items and exact required datasets

Supply plan HDF, its matching plan-linked geometry files, HEC-RAS version, source-unit declaration and independently checked expected results for each case:

1. A 1D model with known wet/dry transitions, explicit dry/missing sentinel documentation or dataset metadata, partially missing XS results and an absent-result case. Needed to validate version-specific sentinel semantics rather than assume them.
2. An unsteady model containing both saved base-output timesteps and the authoritative source maximum dataset, with known maxima between saved rows. Needed for a source-authoritative maximum adapter and comparison with derived envelopes.
3. Unsteady output using millisecond/numeric timestamps, explicit epoch and timezone/DST convention, and expected times. Numeric-time interpretation and timezone remain UNKNOWN.
4. A model with explicitly encoded annual-chance metadata, including separate `1%`, `1%+` and future-condition scenarios, and an approved mapping reference. Automatic trusted-source frequency extraction remains UNKNOWN; only deliberate user mapping is implemented.
5. A real SI model with explicit unit metadata. Rejection is synthetic-tested; no SI engineering conversion/acceptance is claimed.
6. A model with explicit vertical datum metadata and a surveyed datum reference. Non-root datum schemas and datum transformation remain unsupported.
7. The Phase 3 bridge, multiple-opening bridge/culvert, alternative culvert shape/group, inline weir/dam, gated, long lateral/receiving-area and multiple-structures-per-interval fixtures, including identified face/opening hydraulic result datasets. Structure hydraulic extraction remains unvalidated.

There is no general engineering rule establishing that every drawdown should be removed or that a backwater reference has a calculated longitudinal extent. Those operations retain user-entered meaning. Connected multi-reach semantics, SI conversion, datum transformation, FDT/FIRM corrections, DXF import and raster integration remain outside this phase.
