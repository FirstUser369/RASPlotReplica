"""Exercise the real Windows picker using the packaged Python runtime."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.core.windows_file_picker import choose_plan_hdf

user = ctypes.WinDLL("user32", use_last_error=True)
callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user.GetDlgItem.restype = wintypes.HWND
user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.SendMessageW.restype = ctypes.c_ssize_t
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def drive_dialog(path, process_id):
    hwnd = None
    deadline = time.monotonic() + 15
    while not hwnd and time.monotonic() < deadline:
        windows = []
        @callback
        def visit(handle, unused):
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(handle, title, len(title))
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if title.value == "Select HEC-RAS Plan HDF" and pid.value == process_id:
                windows.append(handle)
            return True
        user.EnumWindows(visit, 0)
        hwnd = windows[0] if windows else None
        time.sleep(.1)
    assert hwnd, "Native picker did not appear"
    time.sleep(.5)  # Allow Explorer controls to finish initialization.
    try:
        if path:
            control = user.GetDlgItem(hwnd, 1148)
            if not control:
                control = user.GetDlgItem(hwnd, 1152)
            assert control, "File name control not found"
            text = ctypes.create_unicode_buffer(str(path))
            user.SendMessageW(control, 0x000C, 0, ctypes.addressof(text))
            time.sleep(.2)
            user.PostMessageW(user.GetDlgItem(hwnd, 1), 0x00F5, 0, 0)
        else:
            user.PostMessageW(hwnd, 0x0111, 2, 0)
    except Exception:
        user.PostMessageW(hwnd, 0x0010, 0, 0)
        raise


def exercise(path):
    import os
    results = []
    def pick():
        try:
            results.append(choose_plan_hdf())
        except Exception as exc:
            results.append(exc)
    thread = threading.Thread(target=pick, daemon=True)
    thread.start()
    drive_dialog(path, os.getpid())
    thread.join(10)
    assert not thread.is_alive(), "Picker did not finish"
    assert results == ([str(path)] if path else [None]), results
    print("PASS native picker", "original path" if path else "cancel", flush=True)


if __name__ == "__main__":
    exercise(None)
    exercise(ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf")
