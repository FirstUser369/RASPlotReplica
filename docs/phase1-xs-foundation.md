# Phase 1 cross-section foundation

Status: implemented and tested for independent reaches; continuous multi-reach
stationing remains BLOCKED BY DATA. Phase 1 is not fully complete.

## Identity and ordering contract

- Canonical identity is the case-sensitive `(river, reach, RS)` tuple after
  trimming source padding. RS remains text; numeric conversion is for ordering only.
- Duplicate RS on different rivers/reaches remain distinct. Duplicate identities
  or numerically identical station tags within one reach are rejected as ambiguous.
- Each reach stores ordinary XS upstream-to-downstream (strict descending numeric
  RS). Reach enumeration is deterministic by river/reach name, not river topology.
- Nonnumeric/interpolated-suffix identifiers are rejected with an actionable error
  until an evidence-based ordering adapter is available. They never sort to zero.
- Geometry slices use the original source row index before complete XS objects
  are sorted. Local working indices are not hydraulic result identities.
- Result columns must have a bijective identity match with all geometry XS.
  Structured River/Reach/Station-or-RS attributes are supported. The observed
  5.0.7 steady `Geometry Info/Cross Section Only` labels are matched against unique
  complete geometry labels; ambiguous combined labels are rejected. Missing
  identities, duplicates, extra/missing XS or count mismatches are errors.
- ProfileResult retains explicit xs_ids; working and preview WSEL use identity maps.
- Companion geometry HDF must be identified by Plan Information/Geometry Filename;
  the first filename match is no longer accepted as evidence.

## Source and working lengths

- source_length_chnl holds the original HEC-RAS value. canonical_length_chnl remains
  the same immutable-through-API source value for compatibility. Source plan values
  are never edited by working patches.
- length_chnl is the editable working interval. Recalculate explicitly consumes it.
- For one upstream-first reach: C[last] = start; C[i] = C[i+1] + working_length[i].
- The final downstream XS has no within-reach interval. Its displayed working
  length remains zero; a nonzero edit is rejected. Its source length is preserved.
- Negative, missing and nonfinite lengths are rejected, including at the source.
  Explicit zero is accepted and produces coincident CUMDIST without merging IDs.
- Invalid patch batches are rejected before mutation. Nonfinite starting CUMDIST
  and arithmetic overflow are rejected.
- Laterals remain noncontributing markers. Existing valid culverts retain gap and
  barrel lengths; residual follows a changed enclosing XS interval. Recalculation
  rejects a working interval too short to contain that validated partition.

## Topology stop condition

Neither real fixture contains multiple reaches/junctions. User-provided reach
order and gap numbers do not prove connectivity. All continuous multi-reach
merge/recalculation requests therefore fail before creating a synthetic plan or
mutating the session. This prevents silent disconnected/branched profiles and the
previous duplicate junction accounting, but does NOT implement connected merging.

Required to unblock: a real connected two-reach model with plan HDF, exact linked
geometry HDF and geometry text, project/plan files, HEC-RAS version/units, and
junction editor/table evidence identifying upstream/downstream reach endpoints
and channel-distance ownership. Also request a branched/disconnected model to
validate rejection. The authoritative interpretation of junction distance must
be established before implementing that rule. No topology is inferred from RS,
reach names, array order or user-entered gap distances.

## Regression coverage and results

Baseline: 9 existing tests passed before changes.
Final: 34 tests passed, zero failures, zero skips, using portable Python 3.11.3:
`python -B -m unittest discover -s backend/tests -v`

25 new tests cover shuffled geometry/result rows, per-reach WSEL, repeated RS
across reaches, source geometry slices, structured/legacy result identifiers,
unsteady identity mapping, companion linkage, mismatches/missing/duplicate IDs,
working edits and preserved source lengths, atomic invalid patches, recalculation,
invalid lengths/stations/start, explicit zero lengths, ambiguous labels, and
unverified-merge rejection without source/working divergence.

Requested A/B/C/D/G/H/I/J/L have regression coverage. E has synthetic independent
multi-reach result-mapping coverage, but connected topology acceptance remains
blocked. F is covered by repeated RS across two reaches. K's positive connected
junction accounting test is blocked; current tests require rejection instead of
permitting any junction calculation. Do not report connected merging as passed.

| Fixture | Before extent (ft) | After extent (ft) |
| --- | ---: | ---: |
| Muncie | 15458.599994659424 | 15458.599994659424 |
| 1033 North | 3229.0599632263184 | 3229.0599632263184 |

North UP face remains 2629.289993286133 ft; DN face remains 2519.289993286133 ft;
separation remains 110 ft. Existing North PDF/DXF face-coordinate tests and Muncie
export/axis tests pass. No PDF/DXF generator or UI source was changed in this phase.
Existing ezdxf `closed` deprecation warnings remain.

No frontend build or package rebuild was performed: this phase changes backend
source only. No commit or push. The Thursday package remains unchanged.

## Remaining limits

Connected/branched junction semantics, nonnumeric station order and additional
HEC-RAS identity schemas require source evidence. General structure engineering,
PDF scale, DXF units, SI conversion, floodway/FIRM and UI work remain outside this
phase. Existing generic geometry optional-data handling and geometry-text
structure supplementation have not been broadly hardened or certified here.
