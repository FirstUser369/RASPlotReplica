# Phase 2 public export contract

Decision recorded before DXF implementation changes: retain the public exporter's
existing paper-layout coordinate model. One drawing unit is one inch, with all
entities in model space. This is a scaled longitudinal drawing, not world/GIS CAD
geometry. No requirement establishes the internal legacy_modelspace branch as
public; that branch is not part of this contract.

For each saved panel, width=(end station-start station)/h_scale and
height=(end elevation-start elevation)/v_scale, in inches. Source distances and
scales remain feet and feet/inch; this phase does not implement SI conversion.
PDF uses 72 points/inch and fixed axes. DXF uses INSUNITS=1 (inches), MEASUREMENT=0
(English). Plot origin on its sheet is (1.75,1.0) inches. Reserve 2.25 inches right
for title/legend and 1 inch above. Reject pages too small; never auto-rescale.

DXF panel_x/panel_y are absolute sheet lower-left origins in drawing inches;
explicit zero is a real origin. Omitted/null origins use horizontal sheet layout
with a 0.5-inch gap (Y=0). PDF gives each panel its own page: subtract the DXF sheet
origin to obtain page-local coordinates. The station/elevation origin remains the
saved panel start station/elevation in both formats. CAD sheet placement does not
translate engineering data within a PDF page.

Reject any selected profile, invert, visible structure elevation, landmark anchor
or displayed backwater outside the panel vertical range, evaluated on the active
station/revision intersection. Message names the saved panel and directs the user
to Panel Setup. Never silently change scale or clip engineering elevations.

Deliverable numbering is 1..N for the exported set. Saved IDs and starting-number
metadata are not mutated; they do not change the exported set's ordinal count.
Revision clipping changes visible data only, not saved axes dimensions or scale.

Dimensional tests measure serialized PDF clipping rectangles AND profile paths
in points, and serialized DXF polylines in inches. Tolerance: 0.001 PDF point
(1/72000 inch); 1e-8 DXF drawing inch. Autodesk acceptance remains separate.

## Implementation and measured results

The original three dimensional tests were run before implementation and failed
on the actual serialized PDF rectangle, DXF units and silent version fallback.
Measured pre-fix PDF rectangle: 1165.12 x 705.304 points = approximately
16.182222 x 9.795889 inches. Post-fix: 936 x 648 points = exactly 13 x 9 inches.
Standard DXF profile endpoints: (1.75,1.0) to (14.75,10.0); differences 13 and 9
inches. Plot border 13 x 9, sheet border 17 x 11. Other tests cover 250 ft/in,
10 ft/in, a 6 x 4 inch custom span, origins including explicit zero and negative
CAD sheet offsets, automatic multiple sheets, subsets and clipped revision ends.

Supported versions verified from serialized ACADVER: R2013=AC1027,
R2010=AC1024, R2007=AC1021, R2000=AC1015. Invalid versions fail with an actionable
error, including HTTP 400 through the export route. The mixed-coordinate internal
legacy_modelspace option is explicitly rejected by this validated public writer.

54 backend tests pass (20 new Phase 2 tests), zero failures/skips. Existing
Phase 1 identity/edit/source tests, ordinary CUMDIST, lateral zero-distance and
North culvert tests pass. Added sequential Muncie/North metadata isolation and
saved-panel subset API preservation checks. TypeScript and Vite build pass;
validation build output was directed to the OS temporary directory. Windows
sandbox initially blocked esbuild; approved elevated build completed. Existing
large-bundle and ezdxf closed-attribute deprecation warnings remain.

Baselines: Muncie extent 15458.599994659424 ft; North extent
3229.0599632263184 ft; North culvert faces 110 ft apart, all unchanged.

Frontend changes only preserve optional/null CAD origins across Panel Setup
round trips; no visual redesign. Previously supplied numeric zero is now honored
as an explicit origin. Multiple explicit equal origins intentionally overlap;
no topology or engineering scale is inferred from drawing sheet placement.

Acceptance artifacts and machine-readable measurements are in
`diagnostics/phase2-export-results/`. AutoCAD/Civil 3D opening, insertion into a
host drawing, linetype/font appearance and 1:1 paper plotting remain UNKNOWN
until tested in those products. Expected interpretation: inches in model space;
1 drawing inch represents h_scale horizontal feet and v_scale vertical feet.
A PDF print must use Actual Size/100%, not printer Fit to Page. Application PDF
geometry cannot prevent a printer driver from applying an additional scale.

Phase 1 connected multi-reach remains blocked and was not changed. No SI
conversion, new structure engineering, frequency changes, FDT/FIRM, DXF import
or raster workflow was implemented. The Thursday package was not rebuilt.
No commit or push.
