"""Local production-browser handoff smoke test using Edge CDP and portable Python."""
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
APP = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
OUT = ROOT / "diagnostics/handoff-results"
OUT.mkdir(exist_ok=True)
URL = "http://127.0.0.1:18765"
MUNCIE = ROOT.parent / "Muncie/Muncie.p04.hdf"
NORTH = ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"


def http(path, data=None, method=None):
    req = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def start_server(log):
    environment = os.environ.copy()
    if APP != ROOT:
        environment["RASPLOT_CONFIG"] = str(APP.parent / "config/settings.local.json")
    process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "18765"],
                               cwd=APP, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(100):
        try:
            if http("/api/health")["ok"]:
                return process
        except Exception:
            time.sleep(.2)
    process.terminate()
    raise AssertionError("Backend startup failed")


def stop_server(process):
    process.send_signal(signal.CTRL_BREAK_EVENT)
    process.wait(timeout=15)
    # Windows can report 3 for CTRL_BREAK even after Uvicorn shuts down cleanly.
    import socket
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", 18765)) != 0, "Server port remains open"


class Browser:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

    def command(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            response = json.loads(self.ws.recv(timeout=60))
            if response.get("id") == self.seq:
                assert "error" not in response, response
                return response.get("result", {})

    def js(self, expression):
        result = self.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        assert "exceptionDetails" not in result, result
        return result.get("result", {}).get("value")

    def wait(self, expression):
        for _ in range(150):
            if self.js(expression):
                return
            time.sleep(.2)
        raise AssertionError("Timed out: " + expression + "\n" + str(self.js("document.body.innerText")))

    def click(self, text, scope="document"):
        self.js(f"[...{scope}.querySelectorAll('button')].find(b => b.textContent.trim() === {json.dumps(text)}).click()")

    def value(self, selector, value):
        self.js(f"(() => {{const e={selector}; Object.getOwnPropertyDescriptor(e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype,'value').set.call(e,{json.dumps(str(value))}); e.dispatchEvent(new Event('input',{{bubbles:true}})); e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")

    def field(self, label):
        return f"[...document.querySelectorAll('.export-fields label')].find(e=>e.firstChild.textContent === {json.dumps(label)}).querySelector('input,select')"

    def open_plan(self, path):
        previous = self.js("window.lastSession?.session_id")
        self.value("document.getElementById('plan-path')", str(path))
        self.click("Open")
        self.wait(f"window.lastSession && window.lastSession.session_id !== {json.dumps(previous)} && [...document.querySelectorAll('.open-bar button')].some(b=>b.textContent==='Open' && !b.disabled)")
        return self.js("window.lastSession.session_id")

    def export_dialog(self):
        self.js("document.querySelector('.controls-row > button').click()")
        self.wait("document.querySelector('.export-body') && !document.querySelector('.export-body').disabled")

    def shot(self, name):
        data = self.command("Page.captureScreenshot", {"format": "png"})["data"]
        (OUT / name).write_bytes(base64.b64decode(data))


def main():
    log = (OUT / "browser-backend.log").open("wb")
    process = start_server(log)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    profile = tempfile.mkdtemp(prefix="rasplot-handoff-edge-")
    edge_log = (OUT / "edge.log").open("wb")
    edge = subprocess.Popen([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "--headless=new", "--disable-gpu", "--no-first-run", "--remote-debugging-port=19222", "--remote-allow-origins=*", "--user-data-dir=" + profile, "about:blank"], startupinfo=startup, stdout=edge_log, stderr=edge_log)
    try:
        for _ in range(25):
            try:
                with urllib.request.urlopen("http://127.0.0.1:19222/json", timeout=1) as response:
                    targets = json.load(response)
                target = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(.2)
        else:
            raise AssertionError("Edge remote debugging did not start; see edge.log")
        with connect(target["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
            b = Browser(ws)
            b.command("Page.enable")
            b.command("Network.enable")
            b.command("Network.setBlockedURLs", {"urls": ["*unpkg.com*", "*basemap.nationalmap.gov*"]})
            b.command("Emulation.setDeviceMetricsOverride", {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False})
            b.command("Page.navigate", {"url": URL})
            b.wait("!!document.getElementById('plan-path')")
            from validate_handoff_picker import drive_dialog
            b.value("document.getElementById('plan-path')", "unchanged-on-cancel")
            b.click("Browse...")
            drive_dialog(None, process.pid)
            b.wait("[...document.querySelectorAll('.open-bar button')].some(b=>b.textContent==='Browse...' && !b.disabled)")
            assert b.js("document.getElementById('plan-path').value") == "unchanged-on-cancel"
            b.click("Browse...")
            drive_dialog(NORTH, process.pid)
            b.wait(f"document.getElementById('plan-path').value === {json.dumps(str(NORTH))}")
            print("PASS browser Browse original path and cancel via portable backend", flush=True)
            b.js("""window.realFetch=window.fetch; window.exportCalls=0; window.fetch=async (...args)=>{if(String(args[0]).endsWith('/report/export')) {window.exportCalls++; if(window.failExport) return new Response(JSON.stringify({detail:'Test export failure'}),{status:400,headers:{'Content-Type':'application/json'}});} const r=await window.realFetch(...args); if(String(args[0])==='/api/source' && r.ok) window.lastSession=await r.clone().json(); return r;};""")
            sid = b.open_plan(MUNCIE)
            b.export_dialog()
            metadata = {k: b.js(b.field(k) + ".value") for k in ["Stream name", "Community", "County", "State"]}
            assert metadata["Stream name"] == http(f"/api/db/{sid}/header")["stream_name"]
            assert all(metadata[k] == "" for k in ["Community", "County", "State"]), metadata
            print("PASS Muncie active metadata", metadata, flush=True)
            for key, value in [("Community", "MUNCIE, CITY OF"), ("County", "DELAWARE COUNTY"), ("State", "IN")]:
                b.value(b.field(key), value)
            b.click("Export PDF")
            b.wait("!!document.querySelector('.export-status a')")
            assert http(f"/api/db/{sid}/header")["community"] == "MUNCIE, CITY OF"
            b.click("Cancel")
            sid = b.open_plan(NORTH)
            b.export_dialog()
            metadata = {k: b.js(b.field(k) + ".value") for k in ["Stream name", "Community", "County", "State"]}
            assert all(metadata[k] == "" for k in ["Community", "County", "State"]), metadata
            assert "MUNCIE" not in metadata["Stream name"].upper()
            assert metadata["Stream name"] == http(f"/api/db/{sid}/header")["stream_name"]
            print("PASS North session metadata isolation", metadata, flush=True)
            b.shot("export-laptop.png")
            for width, height in [(1366,768),(1093,614),(800,600)]:
                b.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
                assert b.js("(() => {const d=document.querySelector('.export-dialog').getBoundingClientRect(), f=document.querySelector('.export-footer').getBoundingClientRect();return d.left>=0 && d.right<=innerWidth && d.top>=0 && f.bottom<=innerHeight && document.querySelector('.export-body').scrollWidth<=document.querySelector('.export-body').clientWidth+1;})()"), (width,height)
            b.shot("export-small-laptop.png")
            b.command("Emulation.setDeviceMetricsOverride", {"width":1366,"height":768,"deviceScaleFactor":1,"mobile":False})
            b.click("Clear Selection")
            assert b.js("document.querySelector('.export-submit').disabled")
            b.click("Select All")
            assert not b.js("document.querySelector('.export-submit').disabled")
            assert not b.js("document.querySelector('.export-body').innerText.includes('Rebuild panels')")
            b.click("Cancel")
            b.value("document.querySelector('[aria-label=\"Revision Limits\"] input')", 100)
            b.value("document.querySelectorAll('[aria-label=\"Revision Limits\"] input')[1]", 2600)
            b.click("Apply", "document.querySelector('[aria-label=\"Revision Limits\"]')")
            b.wait("document.querySelector('[aria-label=\"Revision Limits\"]').innerText.includes('Revision limits saved')")
            assert http(f"/api/db/{sid}/revision-limits")["revision_start_station"] == 100
            panels = http(f"/api/db/{sid}/panels")
            b.click("Panel Setup")
            b.wait("!!document.querySelector('.panel-setup-table input')")
            b.click("Apply", "document.querySelector('.panel-setup-footer')")
            b.wait("!document.querySelector('.panel-setup-modal')")
            assert http(f"/api/db/{sid}/panels") == panels
            b.export_dialog()
            assert "100 - 2,600" in b.js("document.querySelector('.export-body').innerText")
            b.js("window.failExport=true")
            b.click("Export PDF")
            b.wait("!!document.querySelector('.export-error')")
            assert "Test export failure" in b.js("document.querySelector('.export-error').textContent")
            b.js("window.failExport=false")
            for fmt in ["pdf", "dxf"]:
                b.value(b.field("Format"), fmt)
                count = b.js("window.exportCalls")
                b.js("document.querySelector('.export-submit').click(); document.querySelector('.export-submit').click()")
                b.wait("!!document.querySelector('.export-status a')")
                assert b.js("window.exportCalls") == count+1
                assert b.js("document.querySelector('.export-status a').textContent") == "Download " + fmt.upper()
                link = b.js("document.querySelector('.export-status a').href")
                with urllib.request.urlopen(link) as response:
                    content = response.read()
                (OUT / ("north-profile." + fmt)).write_bytes(content)
                if fmt == "pdf":
                    assert content.startswith(b"%PDF")
                else:
                    import ezdxf
                    assert len(ezdxf.readfile(OUT / "north-profile.dxf").modelspace()) > 0
            assert http(f"/api/db/{sid}/panels") == panels
            b.shot("export-success.png")
            b.click("Cancel")
            b.click("Reset to Full Model", "document.querySelector('[aria-label=\"Revision Limits\"]')")
            b.wait("document.querySelector('[aria-label=\"Revision Limits\"]').innerText.includes('Using full model extent')")
            assert not http(f"/api/db/{sid}/revision-limits")["explicit"]
            print("PASS responsive layout, saved panels, revision apply/reset, inline error, duplicate-click prevention, PDF and DXF downloads", flush=True)
            html = urllib.request.urlopen(URL).read().decode()
            assert "unpkg.com" not in html
            b.command("Browser.close")
        stop_server(process)
        assert "Application shutdown complete." in (OUT / "browser-backend.log").read_text()
        process = start_server(log)
        assert http("/api/health")["ok"]
        stop_server(process)
        print("PASS shutdown/restart and production HTML", flush=True)
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)
        if edge.poll() is None:
            edge.terminate(); edge.wait(timeout=10)
        log.close()
        edge_log.close()


if __name__ == "__main__":
    main()
