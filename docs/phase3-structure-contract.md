# Phase 3 structure contract and validation

## Scope and evidence

Implemented source-backed structure normalization, shared placement, explicit support states, and export preflight. Real acceptance evidence is limited to North's single box/single barrel culvert and Muncie's lateral anchor/zero-added-distance behavior. Synthetic tests establish regression behavior, not acceptance of other HEC-RAS structure classes.

## Identity and source model

`structure_model.py` preserves river, reach, RS, source type, source record/path and record ordinal in the canonical identity. Connections use complete river/reach/RS identity, never global RS. Distinct records and opening/group identities remain separate; duplicate source ownership fails validation. Retained evidence includes raw HDF attributes, profile/crest arrays, connectivity, width/length, group records and plan-linked geometry text. Source chord summaries remain explicitly unvalidated; they do not authorize export.

Lateral `local_profile_extent` is the source profile's local station range, **not main-channel CUMDIST**. Receiving-side fields and weir connectivity are retained. No main-channel extent is invented.

## Culvert correction and interval rule

The previous initial-load path could choose the nearest numeric XS even when the source identified a different upstream XS. Initial load, recalculation, Preview and export now use the same explicit connectivity rule.

The US and DS connections must identify adjacent ordinary XS in the structure's reach. UP face = US XS CUMDIST minus source upstream gap. DN face = UP face minus source barrel length. Residual = US working channel length minus gap minus barrel length. Structure rows add no channel distance. Source channel lengths remain immutable.

Gap and length must be finite and nonnegative. Residual, interval closure, face containment and working-axis agreement use an absolute 0.01 ft tolerance; a residual between -0.01 and 0 is treated as floating-point closure noise and reported as zero. Faces are not moved to hide a larger error. Topology, identity and source geometry checks are required in addition to arithmetic closure. A working-length edit requires recalculation before export.

Only one associated box group with one barrel and matching plan-linked geometry text is validated. Inverts and rise are source values; soffit = invert + rise. Deck elevations use interpolation at source barrel stations in the observed two-point deck schema, without extrapolation. Multiple text/group candidates are not reduced to the first candidate.

## Support matrix

| Class/configuration | State | Authoritative export |
| --- | --- | --- |
| North-compatible single box, single barrel, matching source deck | VALIDATED | Allowed after range/scale checks |
| Invalid culvert topology, partition or conflicting source elevations | INVALID | Blocked when affecting range or extent cannot be proven |
| Alternative culvert shape, multiple barrels or missing deck evidence | UNSUPPORTED | Blocked when affecting range |
| Bridge | IMPLEMENTED_NEEDS_VALIDATION | No invented faces; blocked when affecting range |
| Inline structure / weir | IMPLEMENTED_NEEDS_VALIDATION | No invented faces; blocked when affecting range |
| Lateral | IMPLEMENTED_NEEDS_VALIDATION | Anchor only; unknown main-channel extent blocks export |
| Multiple openings/groups or distinct structures sharing an interval | UNSUPPORTED | Records retained; combined geometry blocked |
| Gated structure / unknown type | UNSUPPORTED | No inferred gate geometry or states |

These states describe normalization and validation coverage, not general HEC-RAS compatibility.

## Hydraulic provenance

Values distinguish EXTRACTED, INTERPOLATED, COPIED_XS, DISPLAY_ONLY and MISSING. No structure hydraulic-result adapter is certified in this phase, so face/opening hydraulic values are MISSING. Nearby ordinary XS WSEL is not copied into structure rows or inserted into the hydraulic profile. Geometry interpolation provenance is separate from hydraulic-result provenance.

## Output, Revision Limits and preflight

Preview consumes normalized face stations/elevations from the API; PDF and DXF consume `export_faces`. Approved faces are vertical at their actual stations, without guessed symmetric widths. Preview labels unsupported cases with identity and reason and does not draw their engineering faces.

Revision inclusion is inclusive at each boundary. Only actual validated faces within the requested range are drawn: both, one, or neither. A cut entirely inside a barrel draws no invented boundary face or connecting roof. Multiple structures are considered independently unless shared ownership makes their configuration unsupported.

`GET /api/db/{sid}/structure-preflight` returns every structure's identity, state, reason, bounds and range relevance. PDF/DXF enforce the same checks. Unsupported/unvalidated/invalid structures block an affected range with river/reach/RS/type and a reason. A structure proven outside the range by its connected containing interval does not block. Unknown extent cannot be assumed outside the range. Missing normalized structure provenance also blocks.

**Intentional Muncie change:** its full authoritative PDF/DXF export is blocked by unvalidated lateral extent. The earlier Muncie export smoke expectation was updated to assert this rejection; its stationing assertions were preserved.

## Validation and protected baselines

- 77 existing-plus-Phase-3 backend tests passed together; an additional real-model API/DB/preflight JSON serialization test passed afterward (78 passing tests total).
- 24 new structure tests cover explicit connectivity versus wrong nearest RS, invalid/nonfinite partitions, topology, distinct and duplicate identities, multiple groups, same-interval structures, unsupported classes, hydraulic provenance, edits/recalculation, seven revision cuts with PDF/DXF comparison, actionable API errors and real-model evidence.
- Frontend TypeScript and Vite production build passed into a temporary directory; Vite reported its bundle-size warning. No browser visual acceptance is claimed.
- Muncie ordinary XS extent: 15458.599994659424 ft; lateral rows add zero distance and retain explicit US anchors.
- North ordinary XS extent: 3229.0599632263184 ft. Culvert UP/DN: 2629.289993286133 / 2519.289993286133 ft. Separation: 110 ft. Partition: 5 + 110 + 17.470001220703125 = 132.47000122070312 ft.
- Phase 1 identity/order/source-editable-length regressions passed. Phase 2 physical PDF axes 13 x 9 inches, DXF inch units/version/origin contracts, saved-panel definitions/scales and project metadata isolation passed.
- Existing ezdxf `closed` deprecation warnings remain; no test failures.

## Remaining real datasets and UNKNOWN rules

Each fixture must include its plan HDF, matching plan-linked geometry text (and geometry HDF when applicable), computed results, HEC-RAS version/units and independently checked expected longitudinal geometry/results.

1. Bridge: explicit bounding XS, upstream offset, bridge width and deck/chord geometry. UNKNOWN: accepted bridge face/deck mapping.
2. Multiple-opening bridge/culvert: all opening identities, dimensions and results. UNKNOWN: combined ownership, spacing and result mapping.
3. Alternative culvert shapes/groups, including multiple barrels: shape parameters, inverts and deck. UNKNOWN: shape-specific vertical representation and grouping semantics.
4. Inline weir/dam: connected XS, crest/profile and occupied extent. UNKNOWN: longitudinal interval and crest mapping.
5. Gated structure: gate types, geometry, operating states and computed results. UNKNOWN: gate geometry/state/result mapping.
6. Long lateral connected to a reach/storage area: complete headwater/tailwater connectivity and checked start/end CUMDIST. UNKNOWN: local crest station to main-channel extent and receiving-side semantics.
7. Multiple distinct structures in one ordinary XS interval: independently checked ownership/partition. UNKNOWN: shared-interval composition and overlap rules.

Structure-face/opening hydraulic-result extraction remains UNKNOWN across these classes. No synthetic test substitutes for these datasets. Connected multi-reach semantics remain outside Phase 3.

## Phase 3 changed files

- Backend core: `hdf_reader.py`, `geometry_text_reader.py`, new `structure_model.py`, `rasplot_db.py`, `export_geometry.py`, `dxf_export.py`.
- Backend routers: `hecras_api.py`, `report.py`.
- Frontend: `src/api/client.ts`, `src/components/ProfileChart.tsx`, `src/components/ProfileTable.tsx`.
- Tests: new `backend/tests/test_structures.py`; `backend/tests/test_cumdist.py` Muncie export expectation.
- Documentation: this file.

Earlier-phase/user working-tree changes were preserved. The Thursday package and generated production assets were not rebuilt in Phase 3. No commit or push was made.
