"""Real Edge acceptance for 25,000-XS export cancellation."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.benchmarks.large_model_benchmark import write_hdf_fixture
from diagnostics.phase5_browser import CDP


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="rasplot-phase9-large-edge-"))
    source = temp / "Large.p01.hdf"
    write_hdf_fixture(source, 25_000)
    log = (temp / "backend.log").open("w")
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp))
    backend = subprocess.Popen(
        [sys.executable, "-B", "-m", "uvicorn", "backend.app.main:app",
         "--host", "127.0.0.1", "--port", "8878"],
        cwd=ROOT, env=env, stdout=log, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW)
    browser = None
    cdp = None
    passed = []
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:8878/api/health", timeout=1)
                break
            except OSError:
                time.sleep(.1)
        browser = subprocess.Popen(
            ["node", str(ROOT / "diagnostics/phase5_edge_pipe.cjs"), str(temp / "edge")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
            encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        assert browser.stdout.readline().strip() == "READY"

        class PipeCDP(CDP):
            def call(self, method, params=None):
                browser.stdin.write(json.dumps({"method": method, "params": params or {}}) + "\n")
                browser.stdin.flush()
                response = json.loads(browser.stdout.readline())
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})

        cdp = PipeCDP()
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:8878/?acceptance=phase9-large"})
        cdp.wait("document.querySelector('input[type=text]')", 30)
        cdp.js("window.clickButton=(text,root=document)=>{const b=[...root.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith(text));if(!b)throw Error('Button missing: '+text);b.click()};window.input=(el,value)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};window.confirm=()=>true;window.originalFetch=window.fetch;window.opened=null;window.fetch=async(...args)=>{const r=await originalFetch(...args);if(String(args[0])==='/api/source'&&r.ok)window.opened=await r.clone().json();return r};")
        cdp.js(f"input(document.querySelector('input[type=text]'),{json.dumps(str(source))});clickButton('Open HEC-RAS Model')")
        cdp.wait("window.opened && document.querySelector('.js-plotly-plot')", 180)
        passed.append("25,000-XS HDF opened in Edge")
        cdp.js("window.errors=[];window.addEventListener('error',e=>errors.push(String(e.error?.stack||e.message)));window.addEventListener('unhandledrejection',e=>errors.push(String(e.reason?.stack||e.reason)));window.before=JSON.stringify(await originalFetch('/api/db/'+opened.session_id).then(r=>r.json()).then(d=>({version:d.engineering_version,rows:d.working.length})));clickButton('Panel Setup')")
        cdp.wait("document.querySelector('.panel-setup-modal')", 60)
        cdp.wait("[...document.querySelectorAll('.panel-setup-modal button')].some(b=>b.textContent.trim()==='Auto Generate' && !b.disabled)", 180)
        cdp.js("window.hScale=[...document.querySelectorAll('.panel-setup-controls label')].find(l=>l.textContent.includes('Auto Generate horizontal scale')).querySelector('input');input(hScale,'50000');hScale.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));clickButton('Auto Generate',document.querySelector('.panel-setup-modal'))")
        cdp.wait("document.querySelector('.panel-setup-error') || document.querySelectorAll('.panel-setup-table tbody tr').length===1 || window.errors.length", 180)
        if cdp.js("window.errors.length"):
            raise AssertionError("Panel Setup browser error: "+str(cdp.js("window.errors.join('\\n')")))
        if cdp.js("Boolean(document.querySelector('.panel-setup-error'))"):
            raise AssertionError("Panel Setup error: "+str(cdp.js("document.querySelector('.panel-setup-error').textContent")))
        cdp.wait("!document.querySelector('.panel-setup-footer-actions button:last-child').disabled", 30)
        cdp.js("document.querySelector('.panel-setup-footer-actions button:last-child').click()")
        cdp.wait("!document.querySelector('.panel-setup-modal')", 60)
        cdp.js("window.beforeExport=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());window.beforeEngineeringVersion=beforeExport.engineering_version;window.beforeEngineeringState=JSON.stringify({working:beforeExport.working,panels:beforeExport.panels,panel_review_state:beforeExport.panel_review_state,revision_start:beforeExport.header.revision_start_station,revision_end:beforeExport.header.revision_end_station})")

        for output in ("PDF", "DXF"):
            cdp.js("clickButton('Export')")
            cdp.wait("document.querySelector('.export-submit') && !document.querySelector('.export-submit').disabled", 60)
            cdp.js(f"const s=document.querySelector('.export-dialog select');s.value='{output.lower()}';s.dispatchEvent(new Event('change',{{bubbles:true}}))")
            cdp.wait(f"document.querySelector('.export-submit').textContent.includes('{output}')")
            started = time.perf_counter()
            cdp.js("document.querySelector('.export-submit').click()")
            cdp.wait("document.querySelector('.export-status').textContent.includes('RENDERING')", 180)
            cdp.js("clickButton('Cancel Export',document.querySelector('.export-dialog'))")
            cdp.wait("document.querySelector('.export-error')?.textContent.toLowerCase().includes('cancelled')", 60)
            assert not cdp.js("Boolean(document.querySelector('.export-status a'))")
            passed.append(f"{output} cancelled during rendering in {time.perf_counter()-started:.2f}s with no download")
            print("PASS " + passed[-1], flush=True)
            cdp.js("document.querySelector('[aria-label=\"Close export\"]').click()")

        unchanged = cdp.js("window.afterExport=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());JSON.stringify({working:afterExport.working,panels:afterExport.panels,panel_review_state:afterExport.panel_review_state,revision_start:afterExport.header.revision_start_station,revision_end:afterExport.header.revision_end_station})===beforeEngineeringState")
        assert unchanged
        assert not list(temp.rglob("*.partial"))
        passed.append("session unchanged and no partial artifacts remain")
        print("PASS " + passed[-1], flush=True)

        for output in ("PDF", "DXF"):
            cdp.js("clickButton('Export')")
            cdp.wait("document.querySelector('.export-submit') && !document.querySelector('.export-submit').disabled", 60)
            cdp.js(f"const s=document.querySelector('.export-dialog select');s.value='{output.lower()}';s.dispatchEvent(new Event('change',{{bubbles:true}}))")
            cdp.wait(f"document.querySelector('.export-submit').textContent.includes('{output}')")
            started = time.perf_counter()
            cdp.js("document.querySelector('.export-submit').click()")
            cdp.wait("Boolean(document.querySelector('.export-status a'))", 300)
            filename = cdp.js("document.querySelector('.export-status a').textContent.trim()")
            size = cdp.js("(await originalFetch(document.querySelector('.export-status a').href).then(r=>r.arrayBuffer())).byteLength")
            assert size > 0
            passed.append(f"{output} completed in {time.perf_counter()-started:.2f}s: {filename} ({size} bytes)")
            print("PASS " + passed[-1], flush=True)
            cdp.js("document.querySelector('[aria-label=\"Close export\"]').click()")
        screenshot = cdp.call("Page.captureScreenshot", {"format": "png"})
        (temp / "acceptance.png").write_bytes(base64.b64decode(screenshot["data"]))
        print("RESULT " + json.dumps({"passed": passed, "temp": str(temp)}), flush=True)
    finally:
        if browser:
            try:
                browser.stdin.close()
                browser.wait(timeout=5)
            except Exception:
                browser.terminate()
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
        log.close()


if __name__ == "__main__":
    main()
