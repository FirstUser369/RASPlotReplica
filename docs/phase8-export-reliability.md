# Phase 8 export and persistence contract

## Shared drawing contract

PDF and Engineering DXF consume the same immutable
`EngineeringDrawingDefinition`. It contains the selected stable profile IDs,
revision-clipped panels, ground/profile polylines (including separate runs for
missing WSEL), validated structure faces and culvert envelopes, grid, datum,
units, annotations, leaders, panel IDs, origins, and saved scales.

| Output | Coordinates | Units | Intentional differences |
|---|---|---|---|
| Engineering PDF | Matplotlib engineering axes placed on a physical sheet | PDF points/inches for the page; engineering station/elevation on axes | Multipage sheet presentation |
| Engineering DXF | Engineering coordinates transformed by each panel's H/V scale and paper origin | `$INSUNITS=1` (inches) | Panels are arranged side-by-side in model space |
| CAD Review DXF | Untransformed station/elevation | Raw engineering coordinates; R12 has no claimed CRS | One selected panel; paired PNG/PGW uses the same bounds |

River-station labels remain identities. Drawing X coordinates use continuous
CUMDIST. No exporter fills missing hydraulic values, recomputes WSEL, changes a
datum, or changes a reviewed panel scale.

Independent tests parse PDF content with pypdf and DXF entities with ezdxf.
They verify page/plot dimensions, axes, physical scale, layers, entity types,
vertices, structure faces and outlines, gaps, panel origins, `$INSUNITS`, R12
CAD Review coordinates, and PNG/PGW affine alignment. Real-model and synthetic
SI/US equivalence tests remain separate.

## Long-running exports

PDF and Engineering DXF use a bounded two-worker background queue. Status is a
named stage, not an invented percentage: validation, drawing definition,
rendering, writing, finalization, completed, failed, or cancelled. The export
uses an immutable database snapshot and checks cancellation between panels and
drawing entities. Output is first written as a session-contained `.partial`
file and is atomically renamed and registered only after successful rendering.
Cancelled/failed temporary files are not downloadable artifacts.

The browser polls session-owned job status. Switching engineering context
cancels the obsolete job request. The current project/session remains usable;
no export job writes source HDF or project JSON state.

## Persistence recovery

Project files retain strict schema and size validation, canonical finite JSON,
atomic same-directory writes, source SHA-256 verification, semantic identity
verification, versioned unit/review evidence, candidate-session restoration,
and fail-closed panel review. Missing, relocated, changed, corrupt, unstable,
or unit-incompatible sources never inherit approval by filename or display
label alone.
