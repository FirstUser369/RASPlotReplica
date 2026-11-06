"""Run the preserved launcher twice, simulating its browser open and Enter key."""
import importlib.util
from pathlib import Path
import socket
import sys

package = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("handoff_launcher", package / "app/launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
opened = []
launcher.webbrowser.open = lambda url, **kwargs: opened.append(url) or True
launcher.msvcrt.kbhit = lambda: True
launcher.msvcrt.getwch = lambda: "\r"
for attempt in range(2):
    assert launcher.main() == 0
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", 8765)) != 0
    print(f"PASS portable launcher start/Enter shutdown {attempt + 1}", flush=True)
assert opened == ["http://127.0.0.1:8765/"] * 2
