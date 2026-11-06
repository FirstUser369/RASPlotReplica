# Structure capability contract

Structure recognition never authorizes export by itself. The registry declares
the source evidence a family requires; `structure_model.refresh` then verifies
identity, placement, dimensions, elevations, working CUMDIST, and revision
inclusion before returning `VALIDATED` faces.

## Source inventory

- Identity and longitudinal placement: `Geometry/Structures/Attributes`, with
  river, reach, RS, explicit US/DS river/reach/RS, upstream distance and width.
- Bridge elevations: `Table Info` slices into `Profile Data` for US/DS bridge
  weir and lid profiles.
- Culvert groups: `Culvert Groups/Attributes`, associated by explicit Structure
  ID or complete river/reach/RS identity.
- Barrel centers: `Culvert Groups/Barrels/Attributes` when available.
- Deck and culvert records: the exact plan-linked geometry text file. HDF and
  text dimensions and group identities must agree one-to-one. Each face's
  fixed-width record is decoded as station, deck/roadway high chord, and low
  chord; a blank optional low-chord record is retained as absent rather than
  consumed as the next face's station array.
- Lateral connectivity: `User Defined Weir Connectivity` and receiving-side
  source attributes.
- Units: the Phase 2 engineering-unit contract; raw source quantities remain in
  provenance and normalized dimensions are canonical feet.

## Export-capable families

### BRIDGE_CHORD_ENVELOPE_V1

Requires adjacent explicit bounding XS, finite source upstream distance and
positive width, and all four US/DS weir/lid profiles. It draws two longitudinal
UP/DN chord faces. Disjoint lid runs are treated as multiple bridge openings
and remain blocked rather than collapsed into one envelope. Mixed
bridge/culvert or gated configurations remain blocked.

### CULVERT_GROUP_ENVELOPES_V1

Supports source-matched Box, Circular pipe, and Ellipse groups, integral barrel
counts, multiple barrels, and multiple groups sharing validated longitudinal
placement. Each group retains its identity and gets separate UP/DN faces and a
longitudinal opening outline. Rise/span, inverts, soffits, deck elevations,
length, and upstream distance must be finite and source-backed. Transverse
shape is retained as metadata; it is not fabricated in profile view.

Real-model evidence currently covers a North box culvert, Cherry Creek bridge
chord envelopes and a three-barrel box group, and Industrial Park circular and
elliptical single/multiple-barrel groups (including two groups on one
structure). Synthetic tests cover fail-closed mixed bridge/culvert, gated,
missing-field, ambiguous-registry, and disjoint bridge-opening cases. This
evidence does not authorize other transverse shapes or operating structures.

## Inventory-only families

- `LATERAL_ANCHOR_ONLY_V1`: upstream anchor and zero added channel distance are
  retained; authoritative extent and receiving-side semantics remain blocked.
- `INLINE_WEIR_INVENTORY_ONLY_V1`: source records are retained, but no validated
  longitudinal face rule exists.
- `GATED_INVENTORY_ONLY_V1`: gate geometry and operating state remain blocked.
- Unknown, cross-reach, missing-association, duplicate-identity, and incomplete
  configurations fail closed.

Preflight reports the river, reach, RS, normalized type, capability ID, missing
source fields, affected workflows, proven bounds, status, and reason. Preview
may retain inventory records, but authoritative PDF, engineering DXF, and CAD
Review exports require every in-range structure to be `VALIDATED`.
