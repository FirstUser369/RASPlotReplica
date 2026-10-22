# RAS-Plot Team Review Package

This portable Windows package is for engineering evaluation of the current RAS-Plot working build. It is not a certified regulatory product.

## Start and stop

1. Extract the entire ZIP to a writable local folder. Paths containing spaces are supported.
2. Double-click **Start RAS-Plot.cmd**. Keep its console window open.
3. Wait for the default browser to open after the local health check succeeds.
4. To stop RAS-Plot, return to the console and press **Enter**, or close that console window.

The package uses its bundled 64-bit Python 3.11 runtime. It does not require Python, Node.js, administrator rights, installation, or an internet connection for the core workflow. The optional online basemap requires internet access. RAS-Plot binds only to `127.0.0.1`. If port 8765 is occupied, it selects another local port without stopping the existing process. A second RAS-Plot launch for the same Windows user is rejected.

## Suggested review workflow

1. Choose **Browse...**, select a HEC-RAS plan `*.p??.hdf`, and choose **Open**.
2. Select a reach/profile, or construct and validate a connected flooding source.
3. Review the profile, edit only intended working values, and recalculate CUMDIST when marked dirty.
4. Set Revision Limits and review/apply Panel Setup.
5. Export PDF and Engineering DXF. Confirm the datum, scales, panel extents, linework, annotations, and structure warnings.
6. Use **Save As...** for a `.rasplot.json` project, restart the app, and reopen it to verify persistence.

The HDF is read from its original location and is not modified. Project files are written only where the reviewer chooses. Browser downloads control the destination of PDF/DXF artifacts. Logs and runtime scratch data are under `%LOCALAPPDATA%\RAS-Plot`; deleting scratch data does not delete reviewer-selected project files or browser downloads.

## Supported Windows and limitations

Use a supported 64-bit Windows 10 or Windows 11 desktop. Windows Server, ARM Windows, network shares, managed-browser restrictions, and every HEC-RAS schema have not been validated. Unknown datum remains UNKNOWN. Unsupported or ambiguous structures and stationing remain export-blocking. FDT/FIRM, DXF import, SI conversion, and datum transformation are outside the reviewed workflow. See `known-limitations.md` and `support-matrix.md`.

## Feedback

Copy **Feedback Template.md**, fill it in, and send it with screenshots and the log path shown by any startup error. Do not send confidential HDF/project data unless your project rules permit it.
