# HEC-RAS HDF schema adapters

Adapter selection is based on verified dataset paths, object kinds, structured
identity fields, and units metadata. `File Version` is recorded as provenance
but is not a selector.

## Canonical geometry contract

- `Geometry/Cross Sections/Attributes`: explicit `River`, `Reach`, and `RS`.
- `Geometry/Cross Sections/Station Elevation Info` and `Station Elevation Values`:
  source-indexed geometry slices.
- `Geometry/River Centerlines/*`: optional map geometry and explicit reach
  endpoint connectivity.
- `Geometry/Junctions/Attributes`: optional named connectivity and transition
  distances.
- `Geometry/Structures/Attributes`, profile tables, and culvert group/barrel
  records: optional source-backed structures. Missing or ambiguous associations
  remain export-blocking.

## Registered result families

1. `UNSTEADY_BASE_OUTPUT_EXPLICIT_XS_V1` — saved base-output Water Surface rows,
   explicit river/reach/station result identities, and source timestamps when
   count/order/format verify. Maximum WSEL remains a derived saved-timestep
   envelope.
2. `STEADY_BASE_OUTPUT_EXPLICIT_XS_V1` — steady Water Surface profiles with
   explicit result-side cross-section identities.
3. `STEADY_BASE_OUTPUT_GEOMETRY_INFO_LABELS_V1` — legacy steady Water Surface
   profiles using exact, unique result-side `Geometry Info/Cross Section Only`
   labels matched to complete geometry identities.
4. `POST_PROCESS_STEADY_EXPLICIT_XS_V1` — recognized coded post-process layout
   with explicit identities. It remains blocked until an authorized authentic
   model validates its identity, units, and expected hydraulic values.

All result adapters require a bijection with the canonical geometry identities.
Position-only association, display-name-only association, duplicate identities,
unknown units, ambiguous adapter matches, and unobserved layouts fail closed.

## Quantity provenance

Ground and WSEL come from the adapter-selected geometry/result datasets.
Structure elevations and dimensions come only from explicit structure records
and linked geometry text. Units come from the root `Units System` attribute and
must agree with a companion geometry declaration. Datum comes from the root
`Vertical Datum` attribute when present; no datum transformation is performed.
