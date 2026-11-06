# Muncie ordinary cross-section distance validation

Date: 2026-09-29. Investigation only; no production code changed.

## 1. CANONICAL XS INTERVAL RULE

For two adjacent ordinary cross sections, the upstream cross section owns the three reach lengths to the next downstream cross section. Moving downstream-to-upstream, add the **upstream cross section's `Len Channel`**. Therefore answer **B** is correct, with the ordering qualification that HEC-RAS stores Muncie's cross sections upstream-first and places the reach lengths on the current/upstream XS record.

With upstream-first cross sections `XS[0..n-1]`, downstream origin `C[n-1]=S`, and channel length `L[i]` stored on `XS[i]`:

`C[i] = C[i+1] + L[i]`, for `i=n-2..0`.

Equivalently, `C[i] = S + sum(L[j], j=i..n-2)`. The downstream endpoint's length is not an adjacent-XS interval; the upstream endpoint's length is.

The `Len Left`, `Len Channel`, and `Len Right` fields form one LOB/channel/ROB reach-length triplet on that same upstream XS record. CUMDIST uses only the channel member.

## 2. RAW EVIDENCE

The embedded HDF source is `/Geometry/Cross Sections/Attributes`. Its fields are `Len Left`, `Len Channel`, and `Len Right`. The corresponding `Muncie.g04` source records are `Type RM Length L Ch R = 1,<RS>,<LOB>,<Channel>,<ROB>`.

All 60 upstream HDF channel lengths agree with the corresponding geometry-text channel fields at the text file's two-decimal precision. There is no HDF/text disagreement.

The selected continuous segment contains 11 XS and 10 intervals, from RS 3268.276 through the downstream endpoint RS 237.6455. It is entirely downstream of the lateral at RS 7300 and crosses no structure.

For every interval, the river-station difference independently matches the **upstream** record's channel length within 0.006 ft. Examples:

* `3268.276 - 2920.440 = 347.836`, and the RS 3268.276 record is `341.55 / 347.84 / 356.57`.
* `2290.221 - 1980.776 = 309.445`, and the RS 2290.221 record is `293.25 / 309.45 / 326.22`.
* `570.1187 - 237.6455 = 332.4732`, and the RS 570.1187 record is `383.75 / 332.47 / 321.89`.

The next downstream record carries a different triplet. That downstream triplet describes its own next downstream interval, not the interval arriving from upstream.

## 3. 8–12 ROW HAND-CHECK TABLE

Distances and cumulative values are feet. `Text L/Ch/R` is the raw triplet on the upstream geometry-text record. Canonical cumulative distance uses the downstream-most XS as zero.

| Upstream RS | Downstream RS | Upstream HDF Ch | Downstream HDF Ch | Upstream text L/Ch/R | Initial interval | Recalc/canonical interval | Initial C at upstream | Canonical C at upstream |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 3268.276 | 2920.440 | 347.84 | 337.49 | 341.55/347.84/356.57 | 337.49 | 347.84 | 2920.44 | 3030.63 |
| 2920.440 | 2582.948 | 337.49 | 292.73 | 326.36/337.49/345.59 | 292.73 | 337.49 | 2582.95 | 2682.79 |
| 2582.948 | 2290.221 | 292.73 | 309.45 | 297.38/292.73/287.28 | 309.45 | 292.73 | 2290.22 | 2345.30 |
| 2290.221 | 1980.776 | 309.45 | 237.67 | 293.25/309.45/326.22 | 237.67 | 309.45 | 1980.77 | 2052.57 |
| 1980.776 | 1743.103 | 237.67 | 273.81 | 305.01/237.67/171.91 | 273.81 | 237.67 | 1743.10 | 1743.12 |
| 1743.103 | 1469.294 | 273.81 | 295.08 | 338.45/273.81/217.46 | 295.08 | 273.81 | 1469.29 | 1505.45 |
| 1469.294 | 1174.213 | 295.08 | 308.67 | 326.76/295.08/293.53 | 308.67 | 295.08 | 1174.21 | 1231.64 |
| 1174.213 | 865.5413 | 308.67 | 295.42 | 359.34/308.67/300.36 | 295.42 | 308.67 | 865.54 | 936.56 |
| 865.5413 | 570.1187 | 295.42 | 332.47 | 323.21/295.42/289.85 | 332.47 | 295.42 | 570.12 | 627.89 |
| 570.1187 | 237.6455 | 332.47 | 237.65 | 383.75/332.47/321.89 | 237.65 | 332.47 | 237.65 | 332.47 |

Raw geometry-text line references for these records are 1923, 1946, 1969, 1990, 2013, 2034, 2059, 2085, 2116, 2154, and 2200.

## 4. INITIAL-LOAD RESULT

Current `merge.compute_cumdist` assigns the current cumulative value and then adds the current row's length while iterating downstream-to-upstream. Consequently, each upstream/downstream CUMDIST difference equals the **downstream XS length**.

Across all 60 Muncie intervals, every initial interval equals the downstream HDF channel length. Its full extent is **15,485.51999282837 ft**. This equals the sum of XS lengths at indexes 1..60: it excludes the upstream endpoint's 210.73 ft and includes the downstream endpoint's 237.65 ft.

## 5. RECALCULATE RESULT

When applied to ordinary XS rows only, `recalc_cumdist` starts the downstream endpoint at zero, then adds the next/current upstream row's `length_chnl` before assigning CUMDIST. Every interval therefore equals the upstream HDF channel length.

The ordinary-XS result is **15,458.599994659424 ft**, exactly the sum of the upstream members of all 60 adjacent pairs, `XS[0..59]`. This is the independently reconstructed canonical result.

The production recalculation endpoint must not be evaluated using the full current WORKING table because structure rows add their lengths; that is the separate structure problem intentionally excluded here.

## 6. WHICH CURRENT IMPLEMENTATION IS CORRECT

For an ordinary-XS-only, upstream-first list, the accumulation behavior in `rasplot_db.recalc_cumdist` is correct. The initial-load behavior in `merge.compute_cumdist` is incorrect for the raw HEC-RAS reach-length ownership demonstrated above.

This conclusion applies only to ordinary XS intervals. It does not approve running the present recalculation over structure rows.

## 7. EXACT FUNCTION/LINE RESPONSIBLE

The incorrect implementation is `backend/app/core/merge.py::compute_cumdist`, lines 132–134. Line 133 appends CUMDIST before line 134 adds `xs.length_channel`; this shifts each stored reach length one interval upstream.

`backend/app/core/rasplot_db.py::recalc_cumdist`, lines 490–492, adds the current upstream row length before assigning it. That is the correct ordering for ordinary XS rows, although accepting all WORKING rows is incorrect for the separate structure issue.

## 8. EXPECTED FULL-MODEL CUMDIST USING ONLY ORDINARY XS INTERVALS

With downstream-most RS 237.6455 fixed at 0.0 ft, expected upstream-most RS 15696.24 CUMDIST is:

**15,458.599994659424 ft** using raw HDF float values, or **15,458.60 ft** at geometry-text precision.

The current initial-load value is 15,485.51999282837 ft. Its +26.919998168946 ft difference is exactly downstream endpoint length 237.649993896484 minus upstream endpoint length 210.729995727539.

Repeatable evidence is in `validate_muncie_xs_intervals.py`, `muncie_xs_interval_handcheck.csv`, and `muncie_xs_interval_evidence.json`.
