from __future__ import annotations

import datetime as dt
import json
import msvcrt
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
import ctypes
import atexit

HOST = "127.0.0.1"
PORT = 8765
PORT_SEARCH_LIMIT = 32
STARTUP_TIMEOUT_SECONDS = 60


def acquire_launch_lock(directory: Path):
    """OS releases the byte lock even after a launcher crash."""
    handle = (directory / 'launcher.lock').open('a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise RuntimeError('RAS-Plot is already starting or running for this user.')
    return handle


def guard_child(process):
    """A Windows job closes its children if the launcher exits unexpectedly."""
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_mem', ctypes.c_size_t),
                    ('job_mem', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, int(process._handle)):
        if job: kernel.CloseHandle(job)
        raise OSError('Cannot establish backend process cleanup job.')
    return lambda: kernel.CloseHandle(job)


def port_is_occupied(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, port)) == 0


def choose_port(preferred: int = PORT, search_limit: int = PORT_SEARCH_LIMIT) -> int:
    """Choose a deterministic loopback port without disturbing its owner."""
    for candidate in range(preferred, preferred + search_limit):
        if not port_is_occupied(candidate):
            return candidate
    raise RuntimeError(
        f"No available local port was found in {preferred}-{preferred + search_limit - 1}."
    )


def log_message(log_path: Path, message: str) -> None:
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"[{stamp}] {message}\n")


def health_is_ready(status: int, body: object) -> bool:
    return (
        status == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and body.get("application") == "RAS-Plot Replica"
    )


def wait_for_health(process: subprocess.Popen[bytes], log_path: Path, health_url: str) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_message(log_path, f"Backend exited during startup with code {process.returncode}.")
            return False
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                body = json.loads(response.read().decode("utf-8"))
                if health_is_ready(response.status, body):
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    log_message(log_path, f"Backend did not become healthy within {STARTUP_TIMEOUT_SECONDS} seconds.")
    return False


def stop_backend(process: subprocess.Popen[bytes], log_path: Path) -> None:
    if process.poll() is not None:
        return
    log_message(log_path, "Stopping backend.")
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=10)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log_message(log_path, f"Backend stopped with code {process.returncode}.")


def show_failure(message: str, log_path: Path) -> int:
    log_message(log_path, message)
    print()
    print(f"ERROR: {message}")
    print(f"Startup log: {log_path}")
    print()
    try:
        input("Press ENTER to close this window...")
    except EOFError:
        pass
    return 1


def main() -> int:
    package_root = Path(__file__).resolve().parent.parent
    python_exe = package_root / "runtime" / "python" / "python.exe"
    app_dir = package_root / "app"
    config_path = package_root / "config" / "settings.local.json"
    user_root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'RAS-Plot'
    logs_dir = user_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        launch_lock = acquire_launch_lock(user_root)
    except RuntimeError as exc:
        print(str(exc)); return 1
    atexit.register(launch_lock.close)
    # Delete only our dated log files after 30 days; never walk user data.
    for old in logs_dir.glob('rasplot-*.log'):
        if old.is_file() and time.time() - old.stat().st_mtime > 30 * 86400:
            try: old.unlink()
            except OSError: pass
    log_path = logs_dir / f"rasplot-{dt.datetime.now():%Y%m%d-%H%M%S}.log"

    if not python_exe.is_file():
        return show_failure("Bundled Python runtime is missing.", log_path)
    try:
        port = choose_port()
    except RuntimeError as exc:
        return show_failure(str(exc), log_path)
    health_url = f"http://{HOST}:{port}/api/health"
    app_url = f"http://{HOST}:{port}/"
    if port != PORT:
        log_message(log_path, f"Preferred port {PORT} was occupied; selected {port} without stopping its owner.")

    environment = os.environ.copy()
    environment["PYTHONHOME"] = str(python_exe.parent)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["RASPLOT_CONFIG"] = str(config_path)
    environment["MPLCONFIGDIR"] = str(logs_dir / "matplotlib-cache")
    temp_dir = user_root / 'temp'
    temp_dir.mkdir(exist_ok=True)
    environment['TEMP'] = environment['TMP'] = str(temp_dir)
    manifest = package_root / 'release-manifest.json'
    if manifest.is_file():
        release_identity = json.loads(manifest.read_text(encoding='utf-8-sig'))
        environment['RASPLOT_BUILD_ID'] = release_identity['build_id']
        environment['RASPLOT_RELEASE'] = release_identity.get('release', 'UNSPECIFIED')
        environment['RASPLOT_PURPOSE'] = release_identity.get('purpose', 'Engineering Pilot')

    command = [
        str(python_exe), "-B", "-s", "-E", "-m", "uvicorn", "backend.app.main:app",
        "--host", HOST, "--port", str(port), "--no-access-log",
    ]
    log_message(log_path, "Starting bundled RAS-Plot backend.")
    log_stream = log_path.open("ab", buffering=0)
    process = None
    close_job = None
    try:
        process = subprocess.Popen(
            command,
            cwd=app_dir,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        close_job = guard_child(process)
    except Exception as exc:
        if process is not None: stop_backend(process, log_path)
        log_stream.close()
        return show_failure(f"Unable to start backend: {exc}", log_path)

    try:
        if not wait_for_health(process, log_path, health_url):
            stop_backend(process, log_path)
            return show_failure("RAS-Plot could not start. See the startup log for details.", log_path)

        log_message(log_path, "Backend health check succeeded.")
        print()
        print("RAS-Plot is running.")
        print(f"Browser address: {app_url}")
        print()
        print("Press ENTER here to close RAS-Plot cleanly.")
        if os.environ.get("RASPLOT_NO_BROWSER") != "1":
            webbrowser.open(app_url, new=1)

        if os.environ.get("RASPLOT_TEST_EXIT_AFTER_HEALTH") == "1":
            log_message(log_path, "Launcher test requested clean shutdown after health check.")
            return 0

        while process.poll() is None:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\r", "\n"):
                    break
            time.sleep(0.2)

        if process.poll() is not None:
            print(f"The RAS-Plot backend stopped unexpectedly. See {log_path}")
            input("Press ENTER to close this window...")
            return 1
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop_backend(process, log_path)
        if close_job: close_job()
        log_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
