# Muncie distance investigation — 2026-09-29

Investigation only. No production source changed, no live recalculation or export requested, no branch change. Existing uncommitted work preserved. Local session s1 matched the fresh production reader/builder at every working-row CUMDIST. Evidence: `muncie_cumdist_rows.csv` (all 63 rows), `muncie_cumdist_evidence.json` (raw structure attributes, neighbors, geometry-text excerpts, comparisons), and repeatable `trace_muncie_cumdist.py`. Script uses GET requests and reads HDF/text; recalculation affects deep copies only.

## CURRENT DATA SOURCE

Actual live plan: `../Muncie/Muncie.p04.hdf`, White / Muncie, 61 cross sections, Maximum WSEL. HDF identifies HEC-RAS 6.4.1 June 2023, US Customary units. Embedded `/Geometry` takes precedence over sibling geometry HDFs. `Muncie.p04` references `g04`; the text `Muncie.g04` corroborates the relevant lengths and lateral mappings.

`HdfReader._read_reaches_geometry` reads `/Geometry/Cross Sections/Attributes["Len Channel"]` into `CrossSection.length_channel`. `Len Left` and `Len Right` exist but are not used for CUMDIST. Missing/nonfinite channel values become zero. Cross sections are sorted descending by numeric RS. RS determines ordering/nearest structure association, not the longitudinal distance increment. Cross-section station/elevation coordinates and GIS centerline lengths do not supply the X axis.

Structures come from `/Geometry/Structures/Attributes`; `Upstream Distance`, `Weir Width`, type and RS are read. Culvert-only supplementary reads use `/Geometry/Structures/Culvert Groups/Attributes`: `Length`, `US Distance`, `Invert US`, `Invert DS`. Only the first matching culvert group is used. US/DS RS are retained only for exact type `Culvert`.

Muncie's lateral attributes contain `LW HW Distance` and `US RS`, but these are not retained as lateral positioning inputs. `Upstream Distance` is NaN for both and becomes 0 in the parsed object. `geometry_text_reader.py` provides supplemental culvert elevation/deck information; it does not establish the longitudinal CUMDIST axis or parse lateral HW station mapping. Its barrel station fields are transverse coordinates, not longitudinal X.

## CURRENT CUMDIST ALGORITHM

CUMDIST is reconstructed by the application, not read directly from an HDF CUMDIST column.

For upstream-first cross sections indexed 0..n-1, channel length L[i], start S:

* Initial load, `merge.compute_cumdist`: `C[n-1] = S`; `C[i] = S + sum(L[j] for j=i+1..n-1)`. It assigns before adding while iterating downstream-to-upstream. Therefore `C[i]-C[i+1] = L[i+1]`. The downstream-most raw length is included in every upstream coordinate; the upstream-most length is never represented in any output coordinate.
* `build_working_from_plan` copies those coordinates, inserts structure rows, and sets the downstream-most WORKING XS length to zero **after** the initial CUMDIST calculation. No automatic recalc follows.
* Explicit `rasplot_db.recalc_cumdist`: reverse the entire working-row list; give the first row S; for each following row add **that row's** `length_chnl`, then assign its CUMDIST. Thus for upstream-first working rows `R[i] = S + sum(length[k] for k=i..m-2)`. Structures participate exactly like XS rows.
* `merge_reaches` uses current-XS lengths before assignment except the first downstream XS. It differs from `compute_cumdist` even for one reach. At reach boundaries it adds the requested junction gap and then the next reach's downstream-most XS length; these may describe the same gap. The merged list is downstream-first, whereas the working builder/recalc assume upstream-first. Not exercised by this single-reach model.

Concrete normal-XS evidence:

| RS | Raw channel length | WORKING length | Initial/live CUMDIST | XS-only recalc |
|---|---:|---:|---:|---:|
| 237.6455 (downstream end) | 237.65 | 0 | 0 | 0 |
| 570.1187 | 332.47 | 332.47 | 237.65 | 332.47 |
| 865.5413 | 295.42 | 295.42 | 570.12 | 627.89 |

The 570.1187-to-237.6455 station-label difference is approximately 332.4732 and its geometry-text channel length is 332.47; the current initial spacing is 237.65. This corroborates a one-row displacement in initial accumulation for this model, but RS subtraction is not proposed as a general algorithm.

Editing LENGTHCHNL only patches that field and reloads the profile; CUMDIST remains unchanged until explicit recalculation. Recalc leaves saved panels and explicit revision limits unchanged.

## STRUCTURE HANDLING

All structures are first assigned to the cross section with closest numeric RS, then inserted after that XS in the working list. This is not hydraulic connectivity matching.

| Structure branch | Initial position | WORKING lengths and recalculation implications |
|---|---|---|
| Bridge / Inline / Weir, excluding Lateral | Dn at max(0, C[nearest]-a), Up at C[nearest]+a; a=max(1,(weir_width or 20)/2) | Both rows get full weir width; adjoining XS length stays intact. Rows are inserted Dn then Up, so recalc reverses the physical face order and adds two widths. `upstream_distance` is ignored. |
| Culvert with both neighbor RS, barrel length, US distance and both inverts | UP and DN both at C[nearest], initially collapsed | UP length=barrel length; DN length=max(0, US-XS channel length-barrel length-US distance). US XS length becomes US distance only if nearest XS is the identified US XS. Recalc can separate faces correctly only when insertion and partition assumptions hold. Negative residual is silently clamped; nearest-DS association can leave original US length intact and double count. |
| Culvert missing any required field | Single row at C[nearest] | Generic weir-width length; no barrel-distance positioning. Invert availability controls whether the distance-specific branch is used. |
| Lateral | Single row at C[nearest] | Full weir width stored as a distance increment; lateral length/offset/connectivity not used. |
| Multiple-opening / other type | No dedicated algorithm | Generic branch unless its type string happens to contain a recognized word; only first matching culvert group is supported. |

Muncie exercises only the lateral branch. Bridge/culvert/multiple-opening/inline findings are code-path findings, not model-specific numerical validation.

## CONSUMER CONSISTENCY

| Consumer | Coordinate source | Qualifications |
|---|---|---|
| On-screen profile | GET `/api/profile/s1`, preferentially `db.working.cumdist` | All 61 live XS and both structure payload coordinates equal DB values. Chart uses these directly. |
| Edit Profile Table | Same profile payload | No accumulation. Structure-placement filter can omit rows; RS 7300 is omitted because nearest XS is downstream of its RS bracket. |
| Panel Setup preview | Profile payload `cumdist` | Converts engineering station to preview pixels. |
| Auto panels / revision extent | WORKING XS CUMDIST min/max | Structure extrema excluded. Coverage=13*h_scale. Saved panels and explicit limits can become stale after recalc. |
| PDF | WORKING XS/structure CUMDIST | Uses engineering X directly. Panel clipping applies, but export does not pass the explicit revision interval to PDF rendering. |
| Normal DXF | Same WORKING CUMDIST | X=panel origin+left margin+(CUMDIST-panel start)/h_scale. Profile polylines clipped to panel AND revision interval. Structure visibility is panel-based. |
| Legacy DXF helper mode | Engineering profile X=CUMDIST | Optional path, not enabled by normal export route. Paired culverts use face coordinates; other structure types are not drawn in that branch. |

Common engineering anchors do not imply matching structure outlines. The chart uses fixed half-widths (40 ft bridge/culvert rectangles, 50 ft crest strokes); PDF uses fractions of panel width; normal DXF uses paper-space half-widths (0.12/0.10/0.15 inches). These are glyphs, not validated physical face extents. Legacy culvert drawing alone joins paired faces directly.

## MUNCIE DIAGNOSTIC RESULTS

Exactly two raw HDF structure records, both Lateral, both White / Muncie. No bridge, culvert, multiple-opening or inline structure is available here to validate those branches.

| River | Reach | Structure RS/type | Bracketing XS (US -> DS) | Nearest XS chosen | Initial/live structure CUMDIST | Derivation |
|---|---|---|---|---|---:|---|
| White | Muncie | 13214 / Lateral | 13214.80 -> 12817.36 | 13214.80, index 16 | 12817.360008 | Sum raw channel lengths at indexes 17..60; copy C[16], no offset |
| White | Muncie | 7300 / Lateral | 7490.833 -> 7158.903 | 7158.903, index 37 | 6868.350067 | Sum raw channel lengths at indexes 38..60; copy C[37], no offset |

Raw neighboring lengths (feet, rounded to two decimals; full float precision in CSV):

| Structure RS | Neighbor XS | Raw Left / Channel / Right | Initial/live XS CUMDIST |
|---|---|---|---:|
| 13214 | 13490.47 (next US) | 273.29 / 275.66 / 265.67 | 13214.800011 |
| 13214 | 13214.80 (US bracket/anchor) | 442.62 / 397.44 / 283.45 | 12817.360008 |
| 13214 | 12817.36 (DS bracket) | 343.35 / 325.34 / 250.83 | 12492.020012 |
| 7300 | 7490.833 (US bracket/HDF US RS) | 342.54 / 331.93 / 321.21 | 7158.910065 |
| 7300 | 7158.903 (DS bracket/chosen anchor) | 272.04 / 290.56 / 340.82 | 6868.350067 |
| 7300 | 6868.344 (next DS) | 247.24 / 241.79 / 233.68 | 6626.560074 |

Raw structure inputs and isolated recalculation:

| Structure RS | HDF US RS | LW HW Distance | Upstream Distance | Weir Width | Lateral profile station extent | Initial C | Recalc C |
|---|---|---:|---|---:|---|---:|---:|
| 13214 | 13214.80 | 3.60 | NaN (parser -> 0) | 20 | 0..1010.26 | 12817.360008 | 12619.710014 |
| 7300 | 7490.833 | 175.00 | NaN (parser -> 0) | 20 | 0..6092.970215 | 6868.350067 | 6650.700073 |

DS RS is blank and DS connection is `2D Interior Area` for both; culvert-group counts are zero. Neither has paired upstream/downstream faces in the application. Recalc derives RS 7300 as downstream XS 6868.344 recalc C 6630.700073 + width 20. RS 13214 becomes downstream XS 12817.36 recalc C 12599.710014 + width 20. The upstream XS adds its intact channel length after this extra width.

Geometry text independently records:

* `Muncie.g04:557`: `Lateral Weir Distance=3.6`; HW mapping `13214.80,-3.6`, `12817.36,513.9532`, `12492.03,892.8837`, `12227.69,1168.858`.
* `Muncie.g04:1326`: `Lateral Weir Distance=175`; HW mapping starts `7490.833,-175`, `7158.903,182.9282`, `6868.344,451.8975` and continues downstream.

These lateral-station separations differ from main-channel reach lengths. Therefore directly subtracting HW Distance from main-channel CUMDIST is not yet a proved repair; the lateral-to-channel mapping must be resolved first.

| Isolated calculation | Downstream C | Upstream C |
|---|---:|---:|
| Current initial/live | 0 | 15485.519993 |
| Recalc only XS rows | 0 | 15458.599995 |
| Recalc full WORKING (two laterals) | 0 | 15498.599995 |
| `merge_reaches([reach],[0])` | 0 | 15458.599995 |

Total change after recalc = (upstream raw length 210.73 - downstream raw length 237.65) + two lateral widths 40 = +13.08 ft. This small net change masks much larger local shifts. XS-only recalc is a comparison calculation, not an approved engineering replacement.

## POTENTIAL PROBLEMS

Confirmed for Muncie:

1. Initial and recalc algorithms assign different reach lengths to the same XS interval; recalc is not idempotent relative to an unchanged initial load.
2. Recalc adds 40 ft of lateral weir width to longitudinal channel distance without splitting the existing adjacent lengths.
3. Lateral positioning ignores supplied HW connectivity/offset; RS 7300 even anchors to a different XS than its HDF US RS.
4. Extended lateral structures are collapsed to single stations and RS 7300 disappears from the table's structure-row placement filter.

Additional code-path risks, not numerically exercised by Muncie: duplicated bridge widths and reversed face order after recalc; collapsed initial culvert faces; wrong-neighbor culvert insertion; clamped negative residuals; first-culvert-group-only treatment; missing/nonfinite lengths silently becoming zero; starred/nonnumeric RS not handled consistently; duplicate RS across reaches used as lookup/pair keys; merged order mismatch and possible junction double count; length edits leave stale CUMDIST; saved panels/explicit revision limits remain stale after recalc; PDF/DXF revision clipping differs. Geometry-text supplemental lookup scans all matching `.g*` files and keys by RS only, rather than selecting the plan-linked geometry/reach (relevant to culvert elevations, not the Muncie X calculation).

## FILES/FUNCTIONS RESPONSIBLE

* `backend/app/core/hdf_reader.py`: `_find_geometry` (177), `_sorted_rs` (137), `_read_reaches_geometry` (199); raw length assignment (238), structure attributes (280 onward), culvert association (309 onward).
* `backend/app/core/merge.py`: `compute_cumdist` (128), `merge_reaches` (38).
* `backend/app/core/rasplot_db.py`: `build_working_from_plan` (289), `recalc_cumdist` (476), `model_station_extent` (208), `auto_panels` (523), `build_db_from_plan` (615), `_geometry_text_structures_for_plan` (241).
* `backend/app/core/geometry_text_reader.py`: `parse_geometry_text_structures`; supplemental culvert geometry only.
* `backend/app/routers/hecras_api.py`: `open_source`, `get_profile` (304), `_structure_cumdist` (99; RS interpolation fallback, bypassed by current working structures), `patch_working`, `post_recalc_cumdist` (731), revision endpoints, `get_auto_end` (893), `select_reach` (946).
* `frontend/src/api/client.ts`: getProfile/patchWorking/recalcCumdist/getPanels/getRevisionLimits transport only; no distance algorithm.
* `frontend/src/App.tsx`: loadProfile (extent), onRecalcCumdist (340), editWorkingRowValue (435).
* `frontend/src/components/ProfileChart.tsx`: CUMDIST arrays/shape anchors; `ProfileTable.tsx`: rows useMemo (34), display values; `PanelSetupDialog.tsx`: stationToX and CUMDIST preview; `RevisionLimits.tsx`: backend extent validation.
* `backend/app/routers/report.py`: `render_profile_pdf_from_db` (41), `_resolved_revision_interval` (320), `export_profile` (329).
* `backend/app/core/dxf_export.py`: `export_dxf`, `clip_station_elevation_polyline` (113), `_draw_legacy_culvert`, `_draw_structure` (471).

## RECOMMENDED NEXT VALIDATION STEP

First agree on the reference origin (currently downstream-most XS=0, not RS zero), then hand-check a short normal-XS chain from the actual `g04` channel lengths. Independently map each lateral's HW station zero/end through its explicit HW XS station mapping to that agreed channel axis. Do not equate lateral length, weir width, RS labels, and main-channel stream distance.

Then use separate known bridge, culvert and multiple-opening fixtures with raw upstream gap, barrel/deck length, downstream gap and US/DS XS connectivity. Verify that the split intervals sum exactly to the enclosing XS channel interval and that initial load/recalc/export retain the same approved coordinates. Muncie alone cannot establish these structure rules. Only after those expected values are reviewed should a targeted production correction be proposed.

Validation performed: successful diagnostic execution using the project Python environment; raw embedded HDF and linked text inspection; equality of fresh/live coordinates for all working rows; equality of live profile payload and DB anchors; isolated full-row vs XS-only recalculation and one-reach merge. PDF/DXF paths were traced in source; no export artifact was generated or visually validated in this investigation. No frontend build needed because frontend source was not changed.
