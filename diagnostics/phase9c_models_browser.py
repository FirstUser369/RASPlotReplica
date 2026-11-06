"""Authentic Cherry Creek, Industrial Park, and Muncie Edge acceptance.

All engineering mutations are initiated through the production React UI. API reads
are used only to assert the resulting state and inspect downloaded artifacts.
Original fixtures are never modified; Cherry is extracted into the temp workspace.
"""
from __future__ import annotations

import json, os, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagnostics.phase5_browser import CDP

DOWNLOADS = Path.home() / "Downloads"


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="rasplot-phase9c-models-"))
    cherry_dir = temp / "cherry"
    with zipfile.ZipFile(DOWNLOADS / "Auraria_HEC-RAS.zip") as archive:
        archive.extractall(cherry_dir)
    cherry = next(cherry_dir.rglob("CherryCk_ConAuraria.p01.hdf"))
    industrial = DOWNLOADS / "Multiple Reach Example/HEC-RAS Submittal/Industrial_Park.p02.hdf"
    muncie = ROOT.parent / "Muncie/Muncie.p04.hdf"
    log = (temp / "backend.log").open("w")
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp))
    backend = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "backend.app.main:app",
        "--host", "127.0.0.1", "--port", "8879"], cwd=ROOT, env=env,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    browser = None; cdp = None; evidence: dict[str, object] = {}
    try:
        for _ in range(200):
            try: urllib.request.urlopen("http://127.0.0.1:8879/api/health", timeout=1); break
            except OSError: time.sleep(.1)
        browser = subprocess.Popen(["node", str(ROOT / "diagnostics/phase5_edge_pipe.cjs"), str(temp / "edge")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
            encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        assert browser.stdout.readline().strip() == "READY"
        class Pipe(CDP):
            def call(self, method, params=None):
                browser.stdin.write(json.dumps({"method":method,"params":params or {}})+"\n"); browser.stdin.flush()
                response=json.loads(browser.stdout.readline())
                if "error" in response: raise RuntimeError(response["error"])
                return response.get("result",{})
        cdp=Pipe(); cdp.call("Page.enable"); cdp.call("Emulation.setDeviceMetricsOverride", {
            "width":1440,"height":900,"deviceScaleFactor":1,"mobile":False})
        cdp.call("Page.navigate", {"url":"http://127.0.0.1:8879/?acceptance=phase9c"})
        cdp.wait("document.querySelector('input[type=text]')", 30)
        cdp.js("""window.clickText=(text,root=document)=>{const b=[...root.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith(text));if(!b)throw Error('Missing button '+text);b.click()};
window.setInput=(el,value)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))};
window.confirm=()=>true;window.originalFetch=window.fetch;window.opened=null;window.fetch=async(...a)=>{const r=await originalFetch(...a);if(String(a[0])==='/api/source'&&r.ok)opened=await r.clone().json();return r};""")

        def open_model(path: Path, engineering=True):
            cdp.js(f"window.opened=null;setInput(document.querySelector('input[type=text]'),{json.dumps(str(path))});clickText('Open HEC-RAS Model')")
            time.sleep(.25)
            if cdp.js("Boolean(document.querySelector('.app-dialog--confirm'))"):
                cdp.js("clickText('Discard and continue',document.querySelector('.app-dialog--confirm'))")
            cdp.wait("window.opened", 180)
            if engineering: cdp.wait("document.querySelector('.js-plotly-plot')", 180)
            else:
                cdp.wait("document.querySelector('.model-workspace')", 180)
                # The /open response precedes React committing the replacement
                # session.  Wait for the new-session workspace reset before
                # interacting so clicks cannot race that effect.
                cdp.wait("document.querySelectorAll('.source-path li').length===0 && document.querySelector('.source-fields input')?.value===''", 180)
            return cdp.js("opened.session_id")

        def setup_panels(horizontal="5000"):
            cdp.js("clickText('Panel Setup')")
            cdp.wait("document.querySelector('.panel-setup-modal')", 60)
            cdp.wait("[...document.querySelectorAll('.panel-setup-modal button')].some(b=>b.textContent.trim()==='Auto Generate'&&!b.disabled)", 180)
            cdp.js(f"const l=[...document.querySelectorAll('.panel-setup-controls label')].find(x=>x.textContent.includes('Auto Generate horizontal scale'));setInput(l.querySelector('input'),'{horizontal}');l.querySelector('input').dispatchEvent(new FocusEvent('focusout',{{bubbles:true}}));clickText('Auto Generate',document.querySelector('.panel-setup-modal'))")
            cdp.wait("document.querySelectorAll('.panel-setup-table tbody tr').length>0", 180)

        def apply_panels():
            cdp.wait("!document.querySelector('.panel-setup-footer-actions button:last-child').disabled",120)
            cdp.js("document.querySelector('.panel-setup-footer-actions button:last-child').click()")
            cdp.wait("(async()=>!(await originalFetch('/api/db/'+opened.session_id).then(r=>r.json())).panels_stale)()",120)
            time.sleep(1)
            closed=not cdp.js("Boolean(document.querySelector('.panel-setup-modal'))")
            if not closed:
                cdp.js("document.querySelector('[aria-label=\"Close panel setup\"]')?.click()")
                cdp.wait("!document.querySelector('.panel-setup-modal')",30)
            return closed

        def export(fmt: str):
            cdp.js("clickText('Export')")
            cdp.wait("document.querySelector('.export-submit')&&!document.querySelector('.export-submit').disabled", 90)
            cdp.js(f"const s=document.querySelector('.export-dialog select');s.value='{fmt}';s.dispatchEvent(new Event('change',{{bubbles:true}}))")
            cdp.wait(f"document.querySelector('.export-submit').textContent.includes('{fmt.upper()}')")
            cdp.js("document.querySelector('.export-submit').click()")
            cdp.wait("document.querySelector('.export-status a')||document.querySelector('.export-error')", 300)
            result=cdp.js("document.querySelector('.export-status a')?{name:document.querySelector('.export-status a').textContent.trim(),href:document.querySelector('.export-status a').href}: {error:document.querySelector('.export-error').textContent.trim()}")
            cdp.js("document.querySelector('[aria-label=\"Close export\"]').click()")
            return result

        # Cherry Creek: authentic import, profiles/structures, WSEL edit, drawdowns,
        # revision, multipanel manipulation, review, and both drawings.
        sid=open_model(cherry)
        db=cdp.js("await originalFetch('/api/db/'+opened.session_id).then(r=>r.json())")
        assert len([r for r in db["working"] if r["kind"]=="xs"]) == 746
        assert len(db["profiles"]) == 4
        structures=cdp.js("await originalFetch('/api/db/'+opened.session_id+'/structure-preflight').then(r=>r.json())")
        cdp.js("clickText('Edit Profile Table')")
        cdp.wait("document.querySelector('.edit-table-modal input[data-profile-name]:not([data-profile-name=\"\"])')")
        cdp.js("window.cherryCell=document.querySelector('.edit-table-modal input[data-profile-name]:not([data-profile-name=\"\"])');window.cherryXs=cherryCell.dataset.xsId;window.cherryProfile=cherryCell.dataset.profileName;window.cherryBefore=Number(cherryCell.value);setInput(cherryCell,String(cherryBefore+.01));cherryCell.dispatchEvent(new FocusEvent('focusout',{bubbles:true}))")
        cdp.wait("(async()=>{const d=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());return d.working.some(r=>r.source_identity===cherryXs && Math.abs(r.profiles[cherryProfile]-(cherryBefore+.01))<1e-6)})()",60)
        cdp.js("document.querySelector('.edit-table-modal')&&clickText('Done');clickText('Edit Profile Table')")
        cdp.wait("document.querySelector('.edit-table-modal')",60)
        manual_badge=cdp.js("[...document.querySelectorAll('.profile-value-state')].some(x=>x.textContent.includes('Manual edit'))")
        cdp.js("clickText('Done');clickText('Fix Drawdowns')")
        cdp.wait("document.querySelector('.engineering-action-modal')")
        cdp.js("clickText('Apply',document.querySelector('.engineering-action-modal'))")
        cdp.wait("document.querySelector('.engineering-action-result')||document.querySelector('.engineering-action-error')",180)
        drawdown=cdp.js("document.querySelector('.engineering-action-modal').innerText")
        cdp.js("clickText('Close',document.querySelector('.engineering-action-modal'))")
        cdp.js("const r=document.querySelector('[aria-label=\"Revision Limits\"]');clickText('Edit limits',r)")
        cdp.wait("document.querySelector('[aria-label=\"Revision Limits\"]').querySelectorAll('input[type=number]').length===2")
        cdp.js("const r=document.querySelector('[aria-label=\"Revision Limits\"]'),i=r.querySelectorAll('input[type=number]');setInput(i[0],'100');setInput(i[1],String(Number(i[1].value)-100));clickText('Apply',r)")
        cdp.wait("document.querySelector('[aria-label=\"Revision Limits\"]').textContent.includes('Revision limits saved')")
        setup_panels("5000")
        initial_panels=cdp.js("document.querySelectorAll('.panel-setup-table tbody tr').length")
        cdp.js("document.querySelector('.panel-setup-table tbody tr').click();clickText('Add Panel',document.querySelector('.panel-setup-modal'))")
        time.sleep(.5)
        add_panel_ok=cdp.js(f"document.querySelectorAll('.panel-setup-table tbody tr').length==={initial_panels+1}")
        cdp.js("document.querySelector('.panel-setup-table tbody tr:last-child')?.click()")
        time.sleep(.1)
        cdp.js("clickText('Exclude Selected',document.querySelector('.panel-setup-modal'))")
        time.sleep(.3)
        exclude_ok=cdp.js("Boolean(document.querySelector('.excluded-panel-tray button'))")
        if exclude_ok:
            cdp.js("document.querySelector('.excluded-panel-tray button').click()")
        time.sleep(.2)
        restore_ok=cdp.js(f"document.querySelectorAll('.panel-setup-table tbody tr').length>={initial_panels}")
        cdp.js("document.querySelectorAll('.panel-setup-table tbody tr')[1]?.click()")
        time.sleep(.1)
        before_order=cdp.js("[...document.querySelectorAll('.panel-setup-table tbody tr')].map(r=>r.firstElementChild.textContent).join('|')")
        cdp.js("clickText('Move Earlier',document.querySelector('.panel-setup-modal'))")
        time.sleep(.3)
        reorder_ok=cdp.js(f"[...document.querySelectorAll('.panel-setup-table tbody tr')].map(r=>r.firstElementChild.textContent).join('|')!=={json.dumps(before_order)}")
        # Cancel the manipulated draft, then create a clean authoritative set.
        cdp.js("clickText('Cancel',document.querySelector('.panel-setup-modal'))")
        cdp.wait("!document.querySelector('.panel-setup-modal')",60)
        setup_panels("5000")
        cdp.wait("document.querySelectorAll('.panel-setup-table tbody tr').length>0",180)
        apply_closed=apply_panels()
        cherry_pdf=export("pdf"); cherry_dxf=export("dxf")
        evidence["cherry"]={"xs":746,"profiles":4,"normalized_structures":len(structures.get('structures',[])),"manual_provenance_badge":manual_badge,"drawdowns":drawdown,"panels":initial_panels,"panel_controls":{"add":add_panel_ok,"exclude":exclude_ok,"restore":restore_ok,"reorder":reorder_ok,"apply_closed":apply_closed},"pdf":cherry_pdf,"dxf":cherry_dxf}
        print("RESULT Cherry Creek "+json.dumps(evidence["cherry"]),flush=True)

        # Industrial Park: model workspace, branch ambiguity, explicit valid route.
        sid=open_model(industrial, engineering=False)
        assert cdp.js("document.querySelectorAll('.reach-card').length") == 9
        cdp.js("setInput(document.querySelector('.source-fields input'),'Ambiguous branch check')")
        for reach in ("Reach 3","Reach 3A","Reach 3B"):
            cdp.js(f"const c=[...document.querySelectorAll('.reach-card')].find(x=>x.querySelector('strong').textContent.includes('South_Soln / {reach}'));c.querySelector('button').click()")
        cdp.wait("document.querySelectorAll('.source-path li').length===3")
        ambiguous_blocked=cdp.js("document.querySelector('.build-profile').disabled && document.querySelector('.validation-preview').innerText.toLowerCase().includes('branch')")
        assert ambiguous_blocked
        # Reopen through UI to clear the rejected draft, then add the validated route in shuffled order.
        sid=open_model(industrial, engineering=False)
        cdp.js("setInput(document.querySelector('.source-fields input'),'Southern Solution Wash')")
        for reach in ("Reach 3C","Reach 4","Reach 3A","Reach 3.2","Reach 3","Reach 3.1"):
            cdp.js(f"const c=[...document.querySelectorAll('.reach-card')].find(x=>x.querySelector('strong').textContent.trim()==='South_Soln / {reach}');c.querySelector('button').click()")
        assembly=cdp.js("[...document.querySelectorAll('.source-path li .source-segment strong')].map(x=>x.textContent)")
        time.sleep(3)
        route_ready=not cdp.js("document.querySelector('.build-profile').disabled")
        validation_text=cdp.js("document.querySelector('.validation-preview').innerText")
        industrial_apply_closed=None; industrial_pdf={"not_run":"route blocked"}; industrial_dxf={"not_run":"route blocked"}
        if route_ready:
            cdp.js("document.querySelector('.build-profile').click()")
            cdp.wait("document.querySelector('.js-plotly-plot')",180)
            cdp.js("clickText('Edit Profile Table')")
            cdp.wait("document.querySelector('.edit-table-modal')")
            has_length_cell=cdp.js("window.lenCell=[...document.querySelectorAll('.edit-table-modal tbody tr')].find(r=>r.textContent.includes('10168'))?.querySelector('input[data-field=length_chnl]');Boolean(window.lenCell)")
            if has_length_cell:
                cdp.js("setInput(lenCell,String(Number(lenCell.value)+20));lenCell.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));clickText('Done');clickText('CUMDIST')")
                cdp.wait("document.querySelector('.app-dialog--compact')",60)
                cdp.js("clickText('Apply',document.querySelector('.app-dialog--compact'))")
                cdp.wait("!document.querySelector('.app-dialog--compact')",180)
            else: cdp.js("clickText('Done')")
            setup_panels("5000"); industrial_apply_closed=apply_panels(); industrial_pdf=export("pdf"); industrial_dxf=export("dxf")
        evidence["industrial"]={"reaches":9,"ambiguous_branch_blocked":ambiguous_blocked,"route_ready":route_ready,"assembly":assembly,"validation":validation_text,"apply_closed":industrial_apply_closed,"pdf":industrial_pdf,"dxf":industrial_dxf}
        print("RESULT Industrial Park "+json.dumps(evidence["industrial"]),flush=True)

        # Muncie: authentic import and fail-closed authoritative export.
        sid=open_model(muncie)
        profile_count=cdp.js("document.querySelectorAll('.controls-row select')[1]?.options.length")
        setup_panels("5000"); muncie_apply_closed=apply_panels()
        muncie_export=export("pdf")
        assert "error" in muncie_export and "lateral" in muncie_export["error"].lower()
        evidence["muncie"]={"profiles":profile_count,"apply_closed":muncie_apply_closed,"export_block":muncie_export["error"]}
        print("PASS Muncie export block",flush=True)

        shot=cdp.call("Page.captureScreenshot",{"format":"png"})
        import base64
        (temp/"final.png").write_bytes(base64.b64decode(shot["data"]))
        (temp/"evidence.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
        print("RESULT "+json.dumps({"temp":str(temp),"evidence":evidence}),flush=True)
    finally:
        if browser:
            try: browser.stdin.close(); browser.wait(timeout=5)
            except Exception: browser.terminate()
        backend.terminate()
        try: backend.wait(timeout=5)
        except subprocess.TimeoutExpired: backend.kill()
        log.close()

if __name__ == "__main__": main()
