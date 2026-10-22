# RAS-Plot Replica RC4 — Engineering Pilot

RC4 is a portable Windows engineering-pilot build for reviewing HEC-RAS profile data, constructing validated flooding sources, editing channel lengths, recalculating continuous CUMDIST, arranging panels, and exporting supported PDF/DXF profiles.

## Launch

Copy the complete `RAS-Plot-RC4` folder to a writable local location and double-click `Start RAS-Plot.cmd`. The bundled Python runtime starts a loopback-only server and opens the browser. No installed Python, Node.js, administrator rights, or internet connection is required for the core workflow. Online basemaps require internet access.

## Open a HEC-RAS plan

Use **Browse...** beside **HEC-RAS Plan HDF**, select a `*.p??.hdf` plan file, then choose **Open**. The application reads the original local path; it does not copy or upload the HDF.

## Build a flooding source

For multi-reach models, open Model Workspace, order connected reaches downstream to upstream, review every junction transition and its provenance, name the source, validate it, and build it. Missing or ambiguous connections remain blocked.

## Edit and recalculate CUMDIST

Open **Edit Profile Table**, edit the intended cross section using its river/reach/station identity, and save. CUMDIST becomes DIRTY. Use **Recalculate CUMDIST** before previewing panels or exporting. Original HDF channel lengths remain available separately from working values.

## Panels

Open **Panel Setup** after CUMDIST is fresh. Auto Generate or edit panels, select a panel to view its overlay, use the station/elevation move controls, review shared boundaries/gaps/overlaps, and choose **Apply**. Saved panels retain their individual scales and origins and must be reviewed again after dependent engineering changes.

## Export PDF/DXF

Set Revision Limits, select profiles by stable identity, review project information and datum provenance, confirm saved panel coverage, then export PDF or DXF. Unsupported structures, stale CUMDIST, stale panels, missing coverage, invalid scales, and required unknown datum conditions block authoritative export with an actionable message.

## Troubleshooting

- **Port 8765 is in use:** close the other RAS-Plot window/server and launch again.
- **CUMDIST DIRTY:** open the working table, correct edits if needed, then recalculate.
- **Panels require review:** reopen Panel Setup and Apply the reviewed panels.
- **Structure export blocked:** narrow Revision Limits only when engineering scope permits, or obtain a validated structure model.
- **UNKNOWN datum:** confirm the vertical datum externally before relying on elevations for a regulated deliverable.
- Logs and temporary artifacts are stored under `%LOCALAPPDATA%\RAS-Plot`, not inside the package.
