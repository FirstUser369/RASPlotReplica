# Muncie lateral structure placement and distance semantics

Investigation date: 2026-09-29. No production files changed.

## A. LATERAL RAW DATA — RS 13214

HDF `/Geometry/Structures/Attributes`, structure index/SID 0:

| Field | Value | Observable role |
|---|---|---|
| Type / Mode | Lateral / Weir/Gate/Culverts | Lateral hydraulic structure |
| River / Reach / RS | White / Muncie / 13214 | Structure identifier and label |
| US Type / River / Reach / RS | XS / White / Muncie / 13214.80 | Explicit headwater-side connected reach and upstream reference XS |
| DS Type / SA/2D / DS RS | 2D / 2D Interior Area / blank | Tailwater connects to a 2D area; no downstream river XS is declared in the structure attributes |
| Upstream Distance | NaN | Not populated; current reader converts it to 0.0 |
| LW HW Position / Distance | 0 / 3.6 | Headwater reference position and offset. The connectivity table independently places XS 13214.80 at lateral station -3.6 relative to weir station 0 |
| LW TW Position / Distance | 0 / NaN | No single tailwater distance populated |
| Weir Width | 20.0 | Same as geometry text `Lateral Weir WD=20`; distinct from longitudinal weir length |
| Weir shape / coefficient | Broad Crested / 2.0 | Hydraulic weir parameters |
| Max submergence | 0.98 | Hydraulic parameter |
| Linear routing coefficients | +0.05 / -0.05 | Hydraulic/routing parameters |
| Use override HW connectivity | 1 | Dedicated HW connectivity table is active |
| SNN ID | 19 | Internal structure/network identifier; connectivity dataset uses structure index SID 0, not this value |

Other HDF datasets:

* `Table Info` points to two `Profile Data` pairs: `(0,952.2)`, `(1010.26,952.0)`. The lateral profile therefore spans station **0..1010.26 ft**.
* `Centerline Info` selects 17 GIS points. Their polyline length is **1010.26415 ft**, matching the profile station extent within 0.004 ft.
* `User Defined Weir Connectivity`, SID 0, HW entries are:

| Main-reach XS | Lateral station |
|---:|---:|
| 13214.80 | -3.6000 |
| 12817.36 | 513.9532 |
| 12492.03 | 892.8837 |
| 12227.69 | 1168.8580 |

This same mapping occurs in `Muncie.g04` lines 574–577. The geometry-text block starts at line 553 and also contains `Lateral Weir Distance=3.6`, `Lateral Weir WD=20`, the two station/elevation points, and 17 centerline coordinates.

The structure begins at lateral station 0, 3.6 lateral-station feet after the tied XS at station -3.6. It ends at station 1010.26, between the ties at 892.8837 and 1168.858.

## B. LATERAL RAW DATA — RS 7300

HDF `/Geometry/Structures/Attributes`, structure index/SID 1:

| Field | Value | Observable role |
|---|---|---|
| Type / Mode | Lateral / Weir/Gate/Culverts | Lateral hydraulic structure |
| River / Reach / RS | White / Muncie / 7300 | Structure identifier and label |
| US Type / River / Reach / RS | XS / White / Muncie / 7490.833 | Explicit headwater-side connected reach and upstream reference XS |
| DS Type / SA/2D / DS RS | 2D / 2D Interior Area / blank | Tailwater connects to a 2D area; no downstream river XS is declared in the structure attributes |
| Upstream Distance | NaN | Not populated; current reader converts it to 0.0 |
| LW HW Position / Distance | 0 / 175.0 | Connectivity table places XS 7490.833 at lateral station -175 relative to weir station 0 |
| LW TW Position / Distance | 0 / NaN | No single tailwater distance populated |
| Weir Width | 20.0 | Same as geometry text `Lateral Weir WD=20`; not longitudinal weir length |
| Weir shape / coefficient | Broad Crested / 2.0 | Hydraulic weir parameters |
| Max submergence | 0.98 | Hydraulic parameter |
| Linear routing coefficients | +0.05 / -0.05 | Hydraulic/routing parameters |
| Use override HW connectivity | 1 | Dedicated HW connectivity table is active |
| SNN ID | 40 | Internal structure/network identifier |

Other HDF datasets:

* `Profile Data` has six pairs from station **0 to 6092.97 ft**. This is the longitudinal lateral-weir station/elevation profile.
* `Centerline Info` selects 53 GIS points. Their polyline length is **6092.96956 ft**, agreeing with the profile length within 0.001 ft.
* `User Defined Weir Connectivity`, SID 1, contains 22 HW ties from XS 7490.833 at station -175 through XS 1174.213 at station 6136.081. The complete list is preserved in `muncie_lateral_raw_evidence.json`.
* The start bracket is `7490.833,-175` to `7158.903,182.9282`.
* The end bracket is `1469.294,5961.809` to `1174.213,6136.081`.

The same data occurs in the geometry-text block beginning at line 1322: `Lateral Weir Distance=175`, `Lateral Weir WD=20`, the six-point profile, 53 centerline points, and the HW mapping.

## C. HEC-RAS CONNECTIVITY INTERPRETATION

The data establishes the following relationships without relying on the application UI:

1. The structure `RS` is an identifier/river-station label. It is not sufficient to recover the detailed longitudinal relationship.
2. `US Type/River/Reach/RS` identifies the headwater reach and its explicit upstream reference XS.
3. `User Defined Weir Connectivity` maps main-reach XS identifiers to stations measured along the lateral structure alignment.
4. Lateral station 0 is the beginning of the station/elevation profile and centerline. A negative first HW station means the connected XS lies before the structure start along that alignment.
5. The final profile station is the structure's longitudinal end. It lies between two explicit main-reach HW ties in both structures.
6. `DS Type=2D` and `DS SA/2D=2D Interior Area` describe the tailwater connection. There is no explicit downstream river XS in `DS RS`.
7. `Weir Width=20`/`Lateral Weir WD=20` is a separate weir parameter. It cannot be longitudinal structure length: the centerline/profile lengths are 1010.26 and 6092.97 ft. It also cannot be an enclosing main-channel XS interval, because those intervals remain fully present in the ordinary-XS geometry.

The exact hydraulic definition of `WD` is not documented in repository code. Its field name, broad-crested-weir context, and separation from centerline stationing support treating it as a weir cross-sectional/hydraulic width parameter. The raw model conclusively rules out treating it as additional main-channel stream distance.

## D. CANONICAL XS BRACKETS AND CUMDIST

Canonical CUMDIST uses the validated ordinary-XS rule and downstream-most XS=0.

To produce candidate main-channel endpoint positions, lateral station 0 and the final lateral profile station were linearly mapped between their adjacent explicit HW ties. This is a transparent geometric interpolation candidate. The files establish the ties and brackets; they do not explicitly state an interpolation formula for a RASPLOT profile axis.

| Structure | Endpoint | HW tie bracket | Canonical bracket C | Lateral stations | Fraction from upstream tie | Candidate C | Inside bracket? |
|---|---|---|---|---|---:|---:|---|
| 13214 | Start, station 0 | 13214.80 → 12817.36 | 12977.1500 → 12579.7100 | -3.6 → 513.9532 | 0.006956 | **12974.3855** | Yes |
| 13214 | End, station 1010.26 | 12492.03 → 12227.69 | 12254.3700 → 11990.0300 | 892.8837 → 1168.858 | 0.425316 | **12141.9420** | Yes |
| 7300 | Start, station 0 | 7490.833 → 7158.903 | 7253.1901 → 6921.2601 | -175 → 182.9282 | 0.488925 | **7090.9012** | Yes |
| 7300 | End, station 6092.97 | 1469.294 → 1174.213 | 1231.6400 → 936.5600 | 5961.809 → 6136.081 | 0.752623 | **1009.5560** | Yes |

Thus the source data supports ranges, in upstream-to-downstream CUMDIST order:

* RS 13214: approximately **12974.39 to 12141.94 ft** along the main-channel profile.
* RS 7300: approximately **7090.90 to 1009.56 ft** along the main-channel profile.

These main-channel spans are not equal to the lateral centerline lengths because the two alignments have different geometry. The explicit station ties demonstrate that difference.

## E. CURRENT APPLICATION PLACEMENT

`rasplot_db.build_working_from_plan` indexes every structure by the XS with the smallest absolute numeric RS difference. A lateral receives one generic structure row after that XS, with the selected XS's CUMDIST.

| Structure | Candidate XS | Absolute RS difference | Selected? | Current live structure CUMDIST | Canonical C of selected XS |
|---|---:|---:|---|---:|---:|
| 13214 | 13214.80 | 0.80 | Yes | 12817.3600 (old initial axis) | 12977.1500 |
| 13214 | 12817.36 | 396.64 | No | — | 12579.7100 |
| 7300 | 7490.833 | 190.833 | No | — | 7253.1901 |
| 7300 | 7158.903 | 141.097 | Yes | 6868.3501 (old initial axis) | 6921.2601 |

The live values use the already-proven incorrect initial ordinary-XS axis. Changing only that axis would move the selected anchors to the canonical XS coordinates above, but would not fix lateral placement.

The API helper `_structure_cumdist` can interpolate by numeric RS, but it is bypassed for this session: `get_profile` prefers the already-created WORKING structure row CUMDIST. Neither path uses lateral connectivity.

## F. WHY CURRENT PLACEMENT DIFFERS

RS 7300 differs because of **nearest numeric RS**:

* `abs(7300 - 7158.903) = 141.097`
* `abs(7490.833 - 7300) = 190.833`

The loop therefore selects 7158.903 even though HDF `US RS` explicitly names 7490.833 and HW connectivity brackets station 0 between the two sections. Ordering is not the cause. The reader currently retains `US RS` only inside the exact `Culvert` branch, so the lateral `Structure.us_rs` remains blank. It also does not read the connectivity dataset or retain `LW HW Distance`.

RS 13214 happens to select its explicit US XS 13214.80 because the numeric labels are only 0.80 apart. This agreement is accidental; its correct start still lies slightly downstream of that XS according to the -3.6/513.9532 mapping.

## G. WHY RECALCULATION ADDS 40 FT

The exact code/data chain is:

1. `hdf_reader._read_reaches_geometry`, lines 290–305, reads HDF `Weir Width=20` into `Structure.weir_width`.
2. `rasplot_db.build_working_from_plan`, lines 394–410, sends a lateral through the generic non-culvert branch, creates one row, and assigns `length_chnl=s.weir_width`, or 20 ft.
3. Both ordinary XS enclosing intervals remain unmodified.
4. `rasplot_db.recalc_cumdist`, lines 482 and 490–492, reverses **all** WORKING rows and adds every row's `length_chnl`, including both lateral rows.
5. Two lateral rows × 20 ft = **40 ft of artificial main-channel CUMDIST**.

The 20-ft values are not main-channel reach lengths, lateral longitudinal lengths, or offsets. They are the HDF/geometry-text weir-width parameter. The profile/centerline evidence proves the longitudinal structure lengths are instead 1010.26 and 6092.97 ft. Even those longitudinal lateral lengths must not be added to main-channel CUMDIST.

## H. PROPOSED PROFILE REPRESENTATION

The source data supports representing each lateral as a **start/end range along the main-channel profile**, derived from its explicit HW station mapping and the canonical XS chain. It does not support adding a structure row into the cumulative-distance sequence.

A single X coordinate loses essential information: RS 7300 parallels the main reach for roughly 6,081 main-channel CUMDIST feet, while RS 13214 spans roughly 832 ft. If a consumer requires a single label anchor, the start coordinate is the most traceable choice because station 0 is the start of the actual weir profile. Such an anchor should remain presentation metadata alongside the range, not a distance-bearing WORKING row.

The proposed evidence model is therefore:

* `start_cumdist`: main-channel position of lateral station 0;
* `end_cumdist`: main-channel position of final lateral profile station;
* optional `label_cumdist=start_cumdist` unless a product convention is later selected;
* `length_chnl=0` or exclusion from ordinary-XS accumulation;
* retain lateral profile length and 20-ft weir width as separate fields with separate meanings.

## I. REMAINING UNCERTAINTIES

* The model provides connectivity ties but no explicit statement that RASPLOT should linearly interpolate main-channel CUMDIST between them. Linear interpolation is the defensible candidate used above, not yet a documented HEC-RAS/RASPLOT rule.
* Repository code does not document the precise hydraulic definition of `Lateral Weir WD`. The data proves what it is not; external HEC-RAS documentation would be needed to name its exact physical direction with authority.
* `LW HW Position=0`, `Lateral Weir Pos=0`, and `Lateral Weir Connection Pos and Dist=0` are present, but their enumerated meanings are not documented locally.
* The HDF `SNN ID` differs from connectivity `SID`; array index correlation is observable and exact for this file, but its schema contract is not documented locally.
* Tailwater connectivity maps to a 2D area using many TW face/point identifiers. It does not affect main-channel CUMDIST, but its exact spatial semantics are outside this profile investigation.
* A final PDF/DXF glyph convention for an extended lateral needs a product decision after the engineering range is accepted.

## J. EXACT FUNCTIONS THAT WOULD EVENTUALLY REQUIRE CHANGES

No changes were made. A later implementation would likely need targeted changes in:

* `backend/app/core/hdf_reader.py`: `Structure` fields and `_read_reaches_geometry` to retain lateral `US/DS` connectivity, LW distances, profile extent, structure index, and `User Defined Weir Connectivity` rows.
* `backend/app/core/rasplot_db.py`: `build_working_from_plan` nearest-RS indexing and generic lateral row construction; `recalc_cumdist` must accumulate ordinary XS intervals without adding lateral rows.
* `backend/app/routers/hecras_api.py`: `_structure_cumdist` and `get_profile` payload to expose start/end/label positions rather than numeric-RS interpolation or a single copied XS coordinate.
* `frontend/src/api/client.ts`: structure response typing for start/end coordinates.
* `frontend/src/components/ProfileChart.tsx` and `ProfileTable.tsx`: display an extended lateral range and avoid nearest-RS table insertion.
* `backend/app/routers/report.py::render_profile_pdf_from_db` and `backend/app/core/dxf_export.py::export_dxf/_draw_structure`: consume the same start/end representation.

Diagnostic evidence: `validate_muncie_lateral_semantics.py`, `muncie_lateral_summary.csv`, and `muncie_lateral_raw_evidence.json`.
