# 1033 North independent validation of candidate CUMDIST implementation

Date: 2026-09-29. Production code was not modified during this investigation.

## A. 1033 NORTH MODEL INVENTORY

Named source: `RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf`.

* File type/version: HEC-RAS Geometry, 5.0.7 March 2019; US Customary.
* Flow/results: steady; one profile, `PF 1`.
* River/reach: `White Tanks - Ag` / `Wash 18`.
* Cross sections: 28, stored upstream-first from RS 1.047 to 0.447.
* Structure records: 1.
* HDF schema inspected recursively. Unlike Muncie, `/Geometry/Structures` has `Attributes`, centerline datasets, and `Culvert Groups`; it has no `Profile Data`, `Table Info`, or `User Defined Weir Connectivity` dataset.
* Geometry text: `1033_North LOMR As-Built.g04`; all 28 HDF channel lengths agree with their text records at text precision.

## B. STRUCTURE INVENTORY

| Count | Type |
|---:|---|
| 1 | Culvert |
| 0 | Bridge |
| 0 | Inline structure |
| 0 | Lateral structure |
| 0 | Multiple-opening structure |
| 0 | Other |

The sole structure is White Tanks - Ag / Wash 18, RS 0.9425. Raw structure attributes explicitly connect US XS 0.943 to DS XS 0.928.

| Raw field | Value |
|---|---:|
| Structure type / RS | Culvert / 0.9425 |
| US / DS type | XS / XS |
| US / DS RS | 0.943 / 0.928 |
| Structure `Weir Width` | 46.0 ft |
| Culvert groups | 1 |
| Shape / rise / span | Box / 4 / 10 ft |
| Barrel length | 110.0 ft |
| US distance | 5.0 ft |
| DS residual | 17.470001 ft |
| US / DS invert | 1105.760010 / 1105.420044 ft |
| Barrel count | 1 |
| Entrance / exit loss | 0.5 / 1.0 |

The enclosing upstream XS 0.943 owns `Len Channel=132.470001`. The raw distances close exactly:

`5.0 + 110.0 + 17.470001220703125 = 132.470001220703125`.

This proves the three pieces partition one existing main-channel interval. Neither 46-ft width nor 110-ft barrel length is an additional interval.

## C. INDEPENDENT XS OWNERSHIP RESULT

RS difference is not valid physical-distance evidence here: RS is expressed in small decimal labels, while reach lengths are feet. Instead, the independent physical check projects each raw HDF XS cutline intersection onto the raw HDF river-centerline polyline and differences the projected chainages.

Two separate structure-free hand checks follow. `GIS interval` is the independently measured centerline interval. Errors are absolute feet.

Sequence 1:

| Upstream RS | Downstream RS | Upstream Ch | Downstream Ch | GIS interval | Upstream error | Downstream error | Conclusion |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0.906 | 0.884 | 131.5700 | 101.4500 | 131.5686 | 0.0014 | 30.1186 | Upstream |
| 0.884 | 0.863 | 101.4500 | 113.3200 | 101.4488 | 0.0012 | 11.8712 | Upstream |
| 0.863 | 0.840 | 113.3200 | 97.6400 | 113.3247 | 0.0047 | 15.6847 | Upstream |
| 0.840 | 0.813 | 97.6400 | 90.0200 | 97.6394 | 0.0006 | 7.6194 | Upstream |
| 0.813 | 0.801 | 90.0200 | 128.6000 | 90.0211 | 0.0011 | 38.5789 | Upstream |

Sequence 2:

| Upstream RS | Downstream RS | Upstream Ch | Downstream Ch | GIS interval | Upstream error | Downstream error | Conclusion |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0.742 | 0.723 | 97.8600 | 82.7400 | 97.8595 | 0.0005 | 15.1195 | Upstream |
| 0.723 | 0.704 | 82.7400 | 101.9300 | 82.7367 | 0.0033 | 19.1933 | Upstream |
| 0.704 | 0.685 | 101.9300 | 99.0000 | 101.9308 | 0.0008 | 2.9308 | Upstream |
| 0.685 | 0.668 | 99.0000 | 108.1100 | 99.0021 | 0.0021 | 9.1079 | Upstream |
| 0.668 | 0.639 | 108.1100 | 111.6900 | 108.1048 | 0.0052 | 3.5852 | Upstream |

Across the model:

* Adjacent intervals: 27.
* Structure-free intervals: 26.
* Supporting upstream ownership: 26/26.
* Downstream-ownership disagreements: 0.
* Maximum upstream-versus-GIS discrepancy among structure-free intervals: 1.8517 ft at 0.928→0.906. Most discrepancies are below 0.01 ft. The larger difference reflects GIS centerline geometry versus entered reach length; it is still 73.50 ft closer to the upstream value than the downstream value.
* Culvert interval 0.943→0.928: GIS centerline 139.3533 ft, entered channel length 132.4700 ft. This interval is explicitly partitioned by the culvert data and is not treated as structure-free.

Conclusion: **1033 North independently confirms upstream XS ownership.** The HDF and geometry text do not disagree.

## D. OLD VS NEW CUMDIST RESULTS

Downstream-most XS is the zero origin.

| Calculation | Extent (ft) |
|---|---:|
| Previous fresh-load algorithm: sum XS[1..27] | 7120.339992523193 |
| Candidate canonical fresh load | 3229.059963226318 |
| Candidate current `recalc_cumdist()` | 3101.589962005615 |
| Independently reconstructed ordinary-XS chain: sum XS[0..26] | 3229.059963226318 |
| Raw GIS centerline between endpoint XS intersections | 3238.181834450017 |

The previous 7120.34-ft result incorrectly included the downstream endpoint's 4443.5-ft reach length and omitted the upstream endpoint's 552.22-ft length. The candidate fresh load is correct for the raw ordinary-XS chain.

Fresh and recalculated results do **not** agree. Recalculation loses exactly 127.470001 ft, which equals the culvert barrel plus downstream residual: `110 + 17.470001`.

## E. STRUCTURE-BY-STRUCTURE FINDINGS

| River/reach | RS/type | US / DS XS | Raw enclosing interval | Width | US gap / barrel / DS gap | Fresh anchors | Raw-supported faces | Recalc effect |
|---|---|---|---:|---:|---|---|---|---|
| White Tanks - Ag / Wash 18 | 0.9425 / Culvert | 0.943 / 0.928 | 132.470001 | 46 | 5 / 110 / 17.470001 | UP=DN=2634.289993 | UP=2629.289993; DN=2519.289993 | Main extent −127.470001; faces remain at 2634.289993 |

Canonical neighboring coordinates:

* US XS 0.943: 2634.289993 ft.
* DS XS 0.928: 2501.819992 ft.
* Upstream face: `US XS - 5 = 2629.289993 ft`.
* Downstream face: `DS XS + 17.470001 = 2519.289993 ft`.
* Face separation: 110 ft.

Current Working construction replaces the US XS row length 132.47 with 5, then assigns structure-row lengths 110 and 17.47. This is a deliberate interval partition. Fresh XS coordinates were computed before that mutation, so the ordinary XS coordinates remain canonical; however, both fresh structure faces are initially collapsed at the US XS coordinate instead of their raw-supported positions.

The candidate recalculation processes only XS rows. It therefore sees the mutated 5-ft US XS length but excludes the two structure pieces. All XS upstream of the culvert shift downstream 127.47 ft. Culvert faces are not recalculated and remain at the stale fresh coordinate.

## F. PROFILE/PDF/DXF VALIDATION

Actual current backend code was exercised without changing production data.

Passed:

* Model loads: 28 XS, one `PF 1` profile, one culvert.
* XS ordering remains upstream-first.
* Ground and WSEL values remain unchanged through recalculation.
* Profile API, Edit Profile Table payload, Panel Setup endpoint, and Revision Limits endpoint return data.
* Fresh revision/model extent is 0..3229.059963; after recalculation it becomes 0..3101.589962.
* Saved panel remains 0..6500 and contains either extent.
* Fresh and recalculated PDF exports each produce one readable page.
* Fresh and recalculated DXF exports are readable.

Failed engineering consistency:

* Fresh profile API: correct XS extent, collapsed culvert faces at 2634.289993.
* Recalculated profile API: upstream XS 0.943 moves from 2634.289993 to 2506.819992, while both faces remain 2634.289993.
* PDF rendering consumes these same working coordinates (`target=s.cumdist`), so it draws the collapsed/stale faces.
* DXF debug and entity inspection confirm both `STRUCT_CULVERT` face glyphs use the identical 2634.289993 station before and after recalculation. The profile polyline shifts after recalculation.

Thus export creation succeeds, but exported engineering coordinates around the culvert are incorrect.

## G. MUNCIE VS 1033 NORTH COMPARISON

| Topic | Muncie | 1033 North |
|---|---|---|
| XS ownership | Upstream XS | Upstream XS, independently confirmed by GIS centerline |
| Canonical fresh extent | 15458.599995 | 3229.059963 |
| Fresh/recalc agreement | Yes after lateral correction | **No; recalc loses 127.470001 ft** |
| Lateral structures | 2 | 0 |
| Bridges | 0 | 0 |
| Culverts | 0 | 1 box culvert |
| Inline structures | 0 | 0 |
| Multiple openings | 0 | 0 |
| Structure CUMDIST concern | Width must not accumulate; range still unknown | Enclosing XS interval is split into US gap/barrel/DS gap; faces collapse on fresh load and recalc drops two pieces |

Confirmed across both models:

* Upstream XS owns `Len Channel` to the next downstream XS.
* The previous fresh-load algorithm was shifted one row.
* Structure dimensions cannot be blindly added as extra main-channel distance.

Confirmed only in Muncie:

* Explicit lateral US connectivity and HW/TW mapping behavior.
* Lateral width exclusion and temporary US-XS label anchor.

New issue found in 1033 North:

* The candidate XS-only recalculation is incompatible with an existing culvert interval partition.
* Fresh culvert faces are collapsed even though raw distances define separate locations.

Still unknown:

* Bridges, inline structures, and multiple openings are absent from both validation models.
* A generalized structure-aware distance algorithm is not validated.
* Authoritative lateral start/end interpolation remains unvalidated.

## H. CURRENT IMPLEMENTATION — VALID / CONDITIONAL / PROBLEMATIC

| Type | Assessment | Reason |
|---|---|---|
| Ordinary XS without structure interval | **Valid across both models** | Raw text/HDF placement and independent GIS measurements agree |
| Lateral | **Conditionally valid** | Does not add width and uses explicit US anchor; extended range remains unresolved |
| Culvert | **Problematic** | Builder partitions an XS interval into three working lengths, while recalc excludes two; fresh faces also collapse |
| Bridge | **Unknown** | Not present in either model; current generic face-width logic is unvalidated |
| Inline | **Unknown** | Not present in either model |
| Multiple opening | **Unknown** | Not present in either model; current reader uses only the first matching culvert group |

The design “reconstruct only from ordinary XS rows” is correct only if ordinary XS rows retain the full raw interval. It is incorrect when production construction mutates an XS length to one partition component and stores the remaining components on structure rows.

## I. NEW ISSUES DISCOVERED

1. Candidate recalculation shortens 1033 North by 127.470001 ft.
2. Fresh culvert UP/DN faces collapse at the US XS despite explicit 5/110/17.47 partition data.
3. Recalculation leaves structure faces stale while shifting the upstream profile and XS chain.
4. PDF and DXF reproduce the inconsistent coordinates.
5. The current HDF reader exposes the 5.0/110/17.47 partition correctly, but the current shared canonical helper receives the post-partition XS length during recalculation rather than the raw 132.47 interval.
6. Existing Muncie tests did not exercise any structure that replaces/partitions an ordinary XS interval.

## J. WHAT MUST BE RESOLVED BEFORE COMMITTING

The candidate should not be committed as a general CUMDIST correction yet.

At minimum:

1. Define a canonical source for the unmodified ordinary-XS reach length so recalculation cannot mistake a structure partition component for the full interval.
2. Decide whether recalculation should reconstruct XS coordinates from preserved raw XS lengths, validated structure partitions, or a typed interval model. It must not blindly include every structure row or blindly exclude every structure row.
3. Add the 1033 fixture regression: fresh and recalculated XS coordinates must remain identical at 3229.059963 ft.
4. Add exact culvert-face assertions: DS 2519.289993, UP 2629.289993, US XS 2634.289993.
5. Verify PDF and DXF consume those same distinct face coordinates.
6. Keep bridge/inline/multiple-opening behavior out of any fix until matching real datasets establish their rules.

## K. RECOMMENDED NEXT ACTION

Make one narrowly scoped culvert correction backed by this raw partition:

* Preserve the raw XS `Len Channel=132.47` separately from display/partition rows.
* Keep the ordinary XS chain based on that raw length.
* Derive this culvert's face coordinates from the proven `US gap + barrel + DS residual = enclosing interval` relationship.
* Ensure recalculation is idempotent and does not treat the barrel as extra reach length.
* Add 1033-specific endpoint and face-coordinate tests before revisiting any other structure type.

Do not generalize this rule to bridges, multiple openings, inline structures, or laterals.

Evidence artifacts: `1033_xs_intervals.csv`, `1033_candidate_evidence.json`, and `1033_application_validation.json`.
