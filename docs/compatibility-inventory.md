# HEC-RAS compatibility inventory

This inventory records fail-closed production behavior. `Preview` means source
geometry/provenance may be inspected; it does not imply authoritative drawing
eligibility.

## Structure configurations

| Configuration | Source evidence | Current state | Import / preview / export | Remaining evidence |
|---|---|---|---|---|
| Single bridge chord envelope | Structure Attributes, Table Info/Profile Data, explicit adjacent XS, US/DS lid and weir profiles | Implemented and verified | Yes / yes / yes | Multiple disjoint lid runs are a different capability |
| Box, circular, or elliptical culvert groups; multiple barrels/groups with common longitudinal placement | Structure and group identities, plan-linked deck/culvert text, barrel centers, rise/span/length/gap/inverts | Implemented and verified | Yes / yes / yes | Transverse shape remains metadata; longitudinal envelope only |
| Culvert groups with different gap/length | Same evidence but distinct longitudinal intervals | Unsupported: no authentic validated fixture/rule | Yes / yes / blocked | Authentic model and independent expected face placement for every group |
| Additional culvert shapes | Shape-specific HDF/text definition | Unsupported | Yes / inventory / blocked | Authentic fixture defining shape semantics, rise/span, barrel centers, and expected longitudinal envelope |
| Multiple disjoint bridge openings | Multiple nonzero lid-profile runs | Unsupported: one envelope would merge openings | Yes / source geometry / blocked | Authentic fixture plus separately verified opening/chord ownership and expected drawing |
| Mixed bridge/culvert | Bridge chord and culvert group records on one structure | Unsupported: cannot collapse unlike openings | Yes / source geometry / blocked | Authentic fixture and approved separate opening representation |
| Gated structure | Gate groups, opening geometry, invert/soffit, operating state, placement | Inventory only | Yes / inventory / blocked | Authentic gate fixture and authoritative state/drawing rule |
| Inline/weir structure | Crest/profile tables and identity | Source-geometry preview only | Yes / source geometry / blocked | Authoritative longitudinal face-placement rule and fixture |
| Lateral structure to 1D/2D receiver | Crest profile, HW/TW connectivity, receiver identity | Source-geometry preview and zero-distance anchor only | Yes / source geometry / blocked | Main-channel extent/end-face mapping and technically valid longitudinal representation |
| Cross-reach structure | Explicit faces owned by different reaches | Unsupported | Inventory / blocked / blocked | Authoritative cross-reach face identities, route ownership, and distance-ledger rule |
| Missing/ambiguous structure identity | Incomplete river/reach/RS or group ownership | Unsupported | Inventory / blocked / blocked | Exact source identity; station proximity is never accepted |

### Authentic Muncie finding

Muncie contains two lateral structures (`White / Muncie / 13214` and `7300`).
Both connect a 1D headwater reach to `2D Interior Area`. The HDF preserves crest
profiles, headwater river-station connectivity, tailwater cells/segments, and
source HW/TW WSEL arrays. It does **not** provide a unique downstream ordinary
XS/end face or a tailwater CUMDIST on the longitudinal river profile. The crest
profile station is local to the lateral structure. Mapping it onto river
CUMDIST would invent engineering geometry, so authoritative PDF/DXF/CAD Review
export remains blocked.

## Hydraulic result schemas

| Signature | Identity strategy | State |
|---|---|---|
| Unsteady Base Output | Explicit result Cross Section Attributes | Verified |
| Steady Base Output | Explicit result Cross Section Attributes | Verified |
| Steady 5.0.7-style | Exact combined result Geometry Info labels | Verified |
| Post Process profiles with result-local Cross Section Attributes | Coded candidate only | Blocked pending authentic fixture |
| Post Process profiles with `Output/Geometry Info/Cross Section Attributes`, `Profile Dates`, and explicit WSEL units | Explicit result-side composite identities | Verified from authentic Muncie layout; selected only when higher-precedence base output is absent |
| Any positional-only or ambiguous result layout | None | Unsupported; never associated by column coincidence |

The verified post-process adapter preserves source row zero (`Max WS`) as
`SOURCE_AUTHORITATIVE_MAXIMUM`; saved date rows remain
`SAVED_TIMESTEP_WSEL`. It does not infer frequency or timezone. If an unsteady
or steady Base Output signature is present, that higher-precedence source is
selected instead.

## General blockers

- Unknown or inconsistent units: import inspection may continue; engineering
  editing/panels/export fail closed.
- Missing result identities, duplicate same-reach XS identities, ambiguous
  adapter matches, or missing topology transition distances: authoritative
  workflow blocked.
- Unknown datum is disclosed and remains subject to deliverable requirements;
  no datum transformation is performed.
- Unsupported structures outside a proven revision interval do not block that
  interval; an unsupported structure with unknown extent remains blocking.
