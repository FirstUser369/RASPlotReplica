"""Native file selection for the local Windows application; no file copying."""
import ctypes
from ctypes import wintypes
import os
from threading import Lock

_picker_lock = Lock()


def choose_plan_hdf() -> str | None:
    if os.name != "nt":
        raise RuntimeError("Browse is available in the Windows application. Paste a path instead.")
    if not _picker_lock.acquire(blocking=False):
        raise RuntimeError("A file picker is already open.")
    try:
        class OPENFILENAMEW(ctypes.Structure):
            _fields_ = [
                ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", ctypes.c_ssize_t),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                ("FlagsEx", wintypes.DWORD),
            ]

        dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
        dialog.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
        dialog.GetOpenFileNameW.restype = wintypes.BOOL
        dialog.CommDlgExtendedError.restype = wintypes.DWORD
        filename = ctypes.create_unicode_buffer(32768)
        options = OPENFILENAMEW()
        options.lStructSize = ctypes.sizeof(options)
        options.lpstrFilter = "HEC-RAS Plan HDF (*.p??.hdf)\0*.p??.hdf\0HDF files (*.hdf)\0*.hdf\0\0"
        options.nFilterIndex = 1
        options.lpstrFile = ctypes.cast(filename, wintypes.LPWSTR)
        options.nMaxFile = len(filename)
        options.lpstrTitle = "Select HEC-RAS Plan HDF"
        # Explorer dialog, existing file/path, preserve the process working directory.
        options.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008
        if dialog.GetOpenFileNameW(ctypes.byref(options)):
            return filename.value
        error = dialog.CommDlgExtendedError()
        if error:
            raise RuntimeError(f"Windows file picker failed ({error}). Paste the HDF path instead.")
        return None
    finally:
        _picker_lock.release()


def _choose_project_file(*, save: bool) -> str | None:
    if os.name != "nt":
        raise RuntimeError("Project file selection is available in the Windows application.")
    if not _picker_lock.acquire(blocking=False):
        raise RuntimeError("A file picker is already open.")
    try:
        class OPENFILENAMEW(ctypes.Structure):
            _fields_ = [
                ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", ctypes.c_ssize_t),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                ("FlagsEx", wintypes.DWORD),
            ]
        dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
        method = dialog.GetSaveFileNameW if save else dialog.GetOpenFileNameW
        method.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
        method.restype = wintypes.BOOL
        dialog.CommDlgExtendedError.restype = wintypes.DWORD
        filename = ctypes.create_unicode_buffer(32768)
        options = OPENFILENAMEW()
        options.lStructSize = ctypes.sizeof(options)
        options.lpstrFilter = "RAS-Plot Project (*.rasplot.json)\0*.rasplot.json\0JSON files (*.json)\0*.json\0\0"
        options.nFilterIndex = 1
        options.lpstrFile = ctypes.cast(filename, wintypes.LPWSTR)
        options.nMaxFile = len(filename)
        options.lpstrTitle = "Save RAS-Plot Project" if save else "Open RAS-Plot Project"
        options.lpstrDefExt = "rasplot.json"
        options.Flags = 0x00080000 | 0x00000800 | (0x00000008 if not save else 0x00000002)
        if method(ctypes.byref(options)):
            value = filename.value
            if save and not value.lower().endswith(".rasplot.json"):
                value += ".rasplot.json"
            return value
        error = dialog.CommDlgExtendedError()
        if error:
            raise RuntimeError(f"Windows project file picker failed ({error}).")
        return None
    finally:
        _picker_lock.release()


def choose_project_open() -> str | None:
    return _choose_project_file(save=False)


def choose_project_save() -> str | None:
    return _choose_project_file(save=True)
