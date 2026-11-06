# Production compatibility hardening

## Current architecture

```text
HEC-RAS plan HDF + optional plan-linked geometry text
  -> HdfReader (schema inspection, geometry/results identity, provenance)
  -> normalized Plan / TopologyGraph / StructureModel
  -> selected reach or reviewer-selected FloodingSource
  -> RasplotDatabase working state (raw + adjusted hydraulics, distance ledger)
  -> revision + reviewed PanelDef state
  -> immutable EngineeringDrawingDefinition
       -> physical PDF
       -> paper-inch Engineering DXF
       -> raw station/elevation CAD Review DXF + PNG/PGW

.rasplot.json
  -> strict schema + SHA-256 source verification + semantic identity verification
  -> isolated candidate session
  -> deterministic recalculation and review-evidence comparison
  -> commit candidate only after validation
```

The compatibility report is operation-scoped. Dataset presence is not treated
as evidence that editing, panel generation, or authoritative export is safe.

## Severity-ranked risk register

| Severity | Risk and reproducible scenario | Affected modules | Engineering consequence | Resolution / next correction |
|---|---|---|---|---|
| Critical | SI HDF values enter calculations designed and labelled as feet. The reader currently blocks this before working state construction. | `hdf_reader`, `rasplot_db`, panel/export paths, persistence, frontend labels | Incorrect stationing, scales, elevations and deliverables | Fail-closed remains. `engineering_units.py` now defines source/working/display-paper boundaries and tested SI/US conversion. Integrate conversion only after every source quantity and saved-project migration is covered. |
| Critical | Result rows cannot be associated uniquely with geometry rows. | `hdf_reader` result adapters | WSEL assigned to the wrong XS | Existing composite-identity adapters reject mismatch. Add only evidence-backed adapters through a registry; positional guessing remains prohibited. |
| Critical | Missing/ambiguous junction distance or alternate branches are treated as one route. | `model_topology`, CUMDIST workflow | False continuous stationing | Existing route selection and ledger fail closed. More real split/rejoin fixtures are required. |
| Critical | Unsupported structure geometry is drawn authoritatively. | `geometry_text_reader`, `structure_model`, exports | Fabricated bridge/culvert geometry | Existing type-specific normalization and export preflight remain fail closed; inline/gated and unverified mixed groups remain unsupported. |
| High | Nonnumeric RS labels cannot pass `station_number`, even if authoritative source row order and identity exist. | `hdf_reader` | Valid models rejected; unsafe workarounds could reorder XS | Add an explicit source-order adapter with direction evidence before relaxing numeric normalization. Do not infer order from label text. |
| High | Only two hydraulic XS identity schema families are verified. | `hdf_reader` | Other HEC-RAS versions rejected | Replace conditional discovery with a declared adapter registry containing required datasets and association validation. |
| High | Source order is nonmonotonic and normalized by numeric RS. | `hdf_reader`, CUMDIST | Possible inversion where RS convention differs | Keep compatibility warning; require source direction evidence for authoritative processing. |
| High | Unsteady `Maximum WSEL` is a derived saved-timestep envelope. | `hdf_reader`, hydraulics, legends | Derived result mistaken for authoritative maximum | Current provenance and legend distinguish it; extend real unsteady schema coverage. |
| High | Saved projects cross a future unit-contract/schema migration. | `project_file`, `project_restore` | Old values silently reinterpreted | Never reinterpret v1 values. Add an explicit migration version and unit manifest before enabling SI save/reopen. |
| High | Large models cause quadratic or memory-heavy behavior. | reader, working-table build, renderers, frontend tables | Lockups or incomplete exports | Repeated XS lookup was removed. 1k benchmark exists; 5k/10k/25k benchmarks and profiling remain required. |
| Medium | Unknown datum is presented as authoritative. | header, preflight, drawing presentation | Misstated elevations | Current output discloses UNKNOWN and configurable deliverables can require confirmation. No datum transform is performed. |
| Medium | Annotation collision varies by dense sheet content and font metrics. | shared drawing definition/renderers | Unreadable labels | Deterministic placement/leaders and warnings exist. Browser/PDF/CAD visual acceptance remains external. |
| Medium | Browser UI hides operation-specific limitations. | compatibility API, `ModelWorkspace` | Reviewer proceeds with unsupported workflow | Current UI publishes feature, status, affected stages and reason. Add collapsible entity-level diagnostics as coverage grows. |
| Medium | Interrupted/locked OneDrive save or source relocation fails. | project persistence | Data loss or wrong source | Atomic writes, overwrite guards, fingerprint and semantic verification exist; packaged Windows locking tests remain required. |
| Low | Display rounding is mistaken for stored precision. | frontend inputs/tables | Reviewer confusion | Stored values remain full precision; continue field-specific display-only formatting tests. |

## Evidence-based compatibility matrix

| Capability | Real-model evidence | Synthetic/contract evidence | Current status |
|---|---|---|---|
| US Customary steady profiles | North, Muncie, Cherry Creek, Industrial Park fixtures where locally available | Identity, gaps, persistence and export tests | Supported within per-entity preflight |
| SI source inspection | HDF unit attribute inspection | Unit conversion contract tests | Inspection supported; authoritative workflow blocked pending complete integration |
| Single reach | North/Muncie/Cherry Creek | Regression suite | Supported subject to structures/datum/review |
| Connected multi-reach | Industrial Park | Topology/ledger tests | Supported for reviewer-selected routes with authoritative transitions |
| Split/rejoin | Limited real evidence | Synthetic graph tests | Reviewer-selected route only; ambiguity is not auto-resolved |
| Steady WSEL | Multiple real fixtures | Missing/dry/gap tests | Supported for verified identity adapters |
| Unsteady WSEL | Limited fixture evidence | Timestamp/envelope contracts | Supported with explicit provenance/limitations |
| Bridges/culverts | Source-backed fixture-specific shapes/configurations | Structure preflight tests | Supported per normalized record, never by broad type claim |
| Inline/gated/mixed structures | Insufficient authoritative fixtures | Fail-closed tests | Unsupported |
| PDF / Engineering DXF | Shared drawing definition and scale tests | Cross-format geometry contracts | Automated contract verified; engineering/CAD visual approval external |
| Project save/reopen | North and synthetic multi-reach | Fingerprint/semantic/review tests | Supported; changed source is never silently trusted |

## Phase 1 conclusion and Phase 2 entry gate

The architecture is not universally compatible today. The highest-severity
boundary is units: values must not enter feet-based stationing and drawing code
unless their source unit is known and conversion is comprehensive. The new unit
contract is foundation only. SI authoritative processing stays blocked until
all geometry, hydraulics, structures, topology distances, panel state, drawing
transforms, frontend labels, and persisted values use the contract and pass
cross-format physical-scale equivalence tests.

## Phase 2 unit contract and quantity audit

Canonical internal engineering length is **foot**. SI source values are
converted exactly once by `HdfReader` after identity/result parsing and before
topology or working-state construction. Source quantities are retained in
provenance. Map XY coordinates and river-station labels are not converted.

| Quantity | Source meaning/unit | Canonical working unit | Display/edit unit | Export unit |
|---|---|---|---|---|
| River station (`RS`) | Source identifier; often numeric-looking text | Unchanged stable identity | Source text | Annotation text only |
| Channel length | HDF source length, ft or m | ft | working ft | Drives station/page transformation |
| CUMDIST and junction transitions | Converted source lengths and reviewer start | ft | working ft | PDF engineering axis; DXF transformed to paper inches |
| XS station/elevation arrays and banks | HDF local distance/elevation, ft or m | ft | working ft where exposed | Ground line in canonical engineering coordinates |
| Ground/minimum channel elevation | Derived from converted XS geometry | ft | working ft | PDF axis; DXF paper-inch Y |
| WSEL/energy elevation used by profile | HDF elevation, ft or m | ft; raw value/unit retained | working ft | PDF axis; DXF paper-inch Y |
| Map centerlines and cross-section polylines | Source CRS/map coordinates | Unchanged | Map coordinates | Not used as profile station/elevation |
| Structure distances/dimensions/elevations | HDF/text source ft or m | ft; raw dictionaries retained | working ft | Shared drawing geometry then PDF/DXF transforms |
| Revision limits | User-authored canonical stationing | ft | explicitly labelled working ft | Clips canonical engineering geometry |
| Panel bounds/origins | User-authored canonical station/elevation | ft | explicitly labelled working ft | Shared drawing definition |
| H/V engineering scales | Canonical engineering length per paper inch | ft/in | ft/in | PDF physical page and Engineering DXF paper inches |
| PDF page/layout | Physical sheet dimensions | inches then points | inches | 72 PDF points/inch |
| Engineering DXF | Physical drawing coordinates | paper inches | paper-inch contract | `$INSUNITS=1` |
| CAD Review DXF/PNG/PGW | Canonical engineering coordinates | ft | working ft | Raw ft station/elevation; no CRS declaration |
| Datum | Datum label/provenance only | No transformation | Source/user-confirmed text | Explicit disclosure |
| Saved project values | Canonical working state | ft | working ft | Unit manifest required for SI; legacy US v1 remains valid |

The frontend deliberately labels editable engineering values as working feet.
For SI sources, Model Overview discloses `SI source → canonical working ft`.
This avoids mixed-unit editing while preserving source-unit provenance.
