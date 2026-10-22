# RAS-Plot Replica RC4 Release Notes

**Release:** RC4
**Purpose:** Engineering Pilot
**Source:** Phase 7C validated uncommitted working tree

## Improvements since RC3

- Generic HDF model inventory and flooding-source workspace.
- Stable cross-section identities across duplicate river-station labels.
- Explicit downstream-to-upstream multi-reach construction with authoritative junction-distance ledger accounting.
- Working channel-length edits and full-source CUMDIST recalculation without adding structure widths.
- Dirty-state blocking and explicit panel review after dependent changes.
- Project/session isolation with stale asynchronous-response rejection.
- Phase 7 panel overlays, selection, station/elevation movement controls, and stitching indicators.
- Local elevation windows for generated panels.
- Revision Limits preserves exact internal endpoints behind human-readable display values.

## Validation

- Backend: 196/196 passed.
- Frontend contracts: 70/70 passed.
- TypeScript: passed.
- Production Vite build: passed.
- Current assets: `index-CcfmY9NQ.js`, `index-DIIRDEy4.css`.

## Manual acceptance status

Phase 7B panel workflows were manually accepted with Industrial Park (Southern Solution Wash) and 1033 North. RC4 clean-start and packaged-browser acceptance are recorded separately in the final packaging report; CAD-application DXF acceptance remains external.
