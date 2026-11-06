"""Production-bundle browser acceptance using installed Edge/CDP, no added package.

Run with the portable Python from the repository root. Uses isolated temporary
fixtures/artifacts and its own backend/browser ports; never modifies the pilot.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CDP:
    def js(self,expression):
        if expression.startswith("(async"): expression="await "+expression
        r=self.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True,'replMode':True})
        if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
        return r.get('result',{}).get('value')

    def wait(self,expression,seconds=25):
        end=time.time()+seconds
        while time.time()<end:
            if self.js("Boolean(await ("+expression+"))"):return
            time.sleep(.15)
        raise AssertionError('Timed out: '+expression+'\n'+str(self.js('document.body.innerText')).strip()[:5000])


def main():
    import h5py
    import numpy as np
    from backend.tests.test_structures import NORTH
    temp=Path(tempfile.mkdtemp(prefix='rasplot-phase5-browser-'))
    source=temp/'Project.revA.p04.hdf'
    shutil.copyfile(NORTH,source)
    # Synthetic duplicate-row profile fixture, preserving North geometry and
    # the real plan-linked text path for its validated culvert.
    with h5py.File(source,'r+') as f:
        info=f['Plan Data/Plan Information']
        from backend.app.core.hdf_reader import _s
        info.attrs['Geometry Filename']=str(NORTH.parent / _s(info.attrs['Geometry Filename']))
        parent=f['Results/Steady/Output/Output Blocks/Base Output/Steady Profiles']
        ws=parent['Cross Sections/Water Surface'][()]
        del parent['Cross Sections/Water Surface'];del parent['Profile Names']
        parent['Cross Sections/Water Surface']=np.concatenate([ws,ws+.1])
        parent['Profile Names']=np.array([b'steady-row-1',b'Flood, future'])
    log=open(temp/'backend.log','w')
    env=dict(os.environ,TEMP=str(temp),TMP=str(temp))
    backend=subprocess.Popen([sys.executable,'-B','-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8877'],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    browser=None;cdp=None;passed=[]
    def check(name,condition):
        assert condition,name
        passed.append(name);print('PASS '+name,flush=True)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen('http://127.0.0.1:8877/api/health',timeout=1);break
            except OSError:time.sleep(.1)
        browser=subprocess.Popen(
            ['node', str(ROOT/'diagnostics/phase5_edge_pipe.cjs'), str(temp/'edge')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
            encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
        assert browser.stdout.readline().strip() == 'READY', 'Edge debugging pipe did not start'
        class PipeCDP(CDP):
            def __init__(self):pass
            def call(self,method,params=None):
                browser.stdin.write(json.dumps({'method':method,'params':params or {}})+'\n');browser.stdin.flush()
                line=browser.stdout.readline()
                if not line:raise RuntimeError('Edge debugging pipe closed')
                response=json.loads(line)
                if 'error' in response:raise RuntimeError(response['error'])
                return response.get('result',{})
        cdp=PipeCDP()
        cdp.call('Page.enable');cdp.call('Emulation.setDeviceMetricsOverride',{'width':1366,'height':768,'deviceScaleFactor':1,'mobile':False});cdp.call('Page.navigate',{'url':'http://127.0.0.1:8877'})
        cdp.wait("document.querySelector('input[type=text]') !== null")
        cdp.js("window.clickButton=(text,root=document)=>{const b=[...root.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith(text));if(!b)throw Error('Button missing: '+text);b.click()};window.input=(el,value)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};window.confirm=()=>true;window.prompt=()=> '0';window.requests=[];window.originalFetch=window.fetch;window.fetch=async(...args)=>{const r=await window.originalFetch(...args);if(String(args[0])==='/api/source' && r.ok)window.opened=await r.clone().json();return r};")
        cdp.js(f"input(document.querySelector('input[type=text]'),{json.dumps(str(source))});clickButton('Open')")
        cdp.wait('window.opened && document.querySelector(".js-plotly-plot")')
        check('production bundle opens multiple-period plan HDF',True)
        # Select/edit through the user-facing table. The first label deliberately
        # collides with the second immutable profile ID.
        cdp.js("clickButton('Edit Profile Table')")
        cdp.wait('document.querySelector(".edit-table-modal") !== null')
        cdp.js("window.cell=document.querySelector('.edit-table-modal input[data-profile-name=steady-row-0]');window.rowIdentity=cell.dataset.xsId;window.before=Number(cell.value);input(cell,String(before+.25));cell.dispatchEvent(new FocusEvent('focusout',{bubbles:true}))")
        cdp.wait("(async()=>{const d=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());const row=d.working.find(r=>r.source_identity===rowIdentity && r.kind==='xs');return row && row.profiles['steady-row-0']===before+.25})()")
        check('table edit uses profile ID despite display-name collision',cdp.js("(async()=>{const d=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());const r=d.working.find(r=>r.source_identity===rowIdentity && r.kind==='xs');return Boolean(r) && Math.abs(r.profiles['steady-row-1']-(before+.1))<.001})()"))
        cdp.js("window.cell=[...document.querySelectorAll('.edit-table-modal input[data-profile-name=steady-row-0]')][10];input(cell,'');cell.dispatchEvent(new FocusEvent('focusout',{bubbles:true}))")
        cdp.wait("(async()=>{const d=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());return d.working.some(r=>r.kind==='xs' && r.profiles['steady-row-0']===null)})()")
        cdp.js("clickButton('Done');clickButton('Panel Setup')")
        cdp.wait("document.querySelectorAll('[data-profile-segment=wsel]').length===2")
        check('Panel Setup preserves missing WSEL as two segments',True)
        cdp.js("window.saved=await originalFetch('/api/db/'+opened.session_id+'/panels').then(r=>r.json());input(document.querySelector('.panel-setup-controls input'),'100');clickButton('Auto Generate')")
        cdp.wait("document.querySelectorAll('.panel-setup-table tbody tr').length>1")
        check('Auto Generate remains draft',cdp.js("JSON.stringify(saved)===JSON.stringify(await originalFetch('/api/db/'+opened.session_id+'/panels').then(r=>r.json()))"))
        cdp.js("document.querySelector('[aria-label=\"Close panel setup\"]').click()")
        check('Cancel leaves committed panels unchanged',cdp.js("JSON.stringify(saved)===JSON.stringify(await originalFetch('/api/db/'+opened.session_id+'/panels').then(r=>r.json()))"))
        cdp.js("clickButton('Export')")
        cdp.wait("document.querySelector('.export-submit') && !document.querySelector('.export-submit').disabled")
        cdp.js("document.querySelector('.export-submit').click()")
        cdp.wait("document.querySelector('.export-error')?.textContent.includes('review')")
        check('stale saved panels block export with actionable error',True)
        cdp.js("document.querySelector('[aria-label=\"Close export\"]').click();clickButton('Panel Setup')")
        cdp.wait("document.querySelector('.panel-setup-modal') && !document.querySelector('.panel-setup-modal').textContent.includes('Loading')")
        cdp.wait("!document.querySelector('.panel-setup-footer-actions button:last-child').disabled")
        cdp.js("document.querySelector('.panel-setup-footer-actions button:last-child').click()")
        cdp.wait("!document.querySelector('.panel-setup-modal') || document.querySelector('.panel-setup-error')")
        if cdp.js("Boolean(document.querySelector('.panel-setup-modal'))"):
            raise AssertionError("Panel Apply remained open: "+str(cdp.js("document.querySelector('.panel-setup-error')?.textContent")))
        check('Apply reviews preserved panels',cdp.js("!(await originalFetch('/api/db/'+opened.session_id).then(r=>r.json())).panels_stale"))
        cdp.js("clickButton('Export')")
        cdp.wait("document.querySelector('.export-submit') && !document.querySelector('.export-submit').disabled")
        cdp.js("document.querySelector('.export-submit').click()")
        cdp.wait("document.querySelector('.export-status a')?.textContent.toLowerCase().includes('.pdf')")
        check('PDF export completes with owned download',cdp.js("(await originalFetch(document.querySelector('.export-status a').href)).status===200"))
        cdp.js("window.formatSelect=document.querySelector('.export-dialog select');formatSelect.value='dxf';formatSelect.dispatchEvent(new Event('change',{bubbles:true}))")
        cdp.wait("document.querySelector('.export-submit').textContent==='Export DXF'")
        cdp.js("document.querySelector('.export-submit').click()")
        cdp.wait("document.querySelector('.export-status a')?.textContent.toLowerCase().includes('.dxf')")
        check('DXF export completes with owned download',cdp.js("(await originalFetch(document.querySelector('.export-status a').href)).status===200"))
        cdp.js("document.querySelector('[aria-label=\"Close export\"]').click();window.beforeRevisionPanels=await originalFetch('/api/db/'+opened.session_id+'/panels').then(r=>r.json());window.revision=document.querySelector('[aria-label=\"Revision Limits\"]');clickButton('Edit limits',revision)")
        cdp.wait("revision.querySelectorAll('input[type=number]').length===2")
        cdp.js("input(revision.querySelectorAll('input[type=number]')[0],'10');input(revision.querySelectorAll('input[type=number]')[1],'3000');clickButton('Apply',revision)")
        cdp.wait("(async()=>{const d=await originalFetch('/api/db/'+opened.session_id).then(r=>r.json());return d.header.revision_start_station===10 && d.panels_stale})()")
        check('Revision Limits marks preserved panels for review',cdp.js("JSON.stringify(beforeRevisionPanels.panels)===JSON.stringify((await originalFetch('/api/db/'+opened.session_id+'/panels').then(r=>r.json())).panels)"))
        cdp.js("window.baseFetch=window.fetch;window.failMetadata=true;window.fetch=async(...args)=>{if(failMetadata && String(args[0])==='/api/db/'+opened.session_id){failMetadata=false;return new Response(JSON.stringify({detail:'Acceptance metadata failure'}),{status:500,headers:{'Content-Type':'application/json'}})}return baseFetch(...args)};clickButton('Export')")
        cdp.wait("document.querySelector('.export-error')?.textContent.includes('Acceptance metadata failure')")
        check('metadata failure stops loading and blocks export',cdp.js("!document.querySelector('.export-status').textContent.includes('Loading project metadata') && document.querySelector('.export-submit').disabled"))
        cdp.js("document.querySelector('[aria-label=\"Close export\"]').click();window.fetch=baseFetch;window.delayed=false;window.fetch=async(...args)=>{const r=await baseFetch(...args);if(!delayed && String(args[0]).startsWith('/api/profile/')){delayed=true;await new Promise(resolve=>setTimeout(resolve,600))}return r};window.primary=document.querySelectorAll('.controls-row select')[1];primary.value='steady-row-1';primary.dispatchEvent(new Event('change',{bubbles:true}))")
        cdp.wait('window.delayed')
        cdp.js("primary.value='steady-row-0';primary.dispatchEvent(new Event('change',{bubbles:true}))")
        cdp.wait("document.querySelector('.js-plotly-plot')?.data?.[0]?.name?.includes('steady-row-1')")
        time.sleep(.8)
        check('late profile response cannot replace newer selection',cdp.js("document.querySelectorAll('.controls-row select')[1].value==='steady-row-0' && document.querySelector('.js-plotly-plot').data[0].y.includes(null)"))
        cdp.js("window.fetch=baseFetch")
        screenshot=cdp.call('Page.captureScreenshot',{'format':'png'})
        (temp/'acceptance.png').write_bytes(base64.b64decode(screenshot['data']))
        (temp/'result.json').write_text(json.dumps({'passed':passed},indent=2))
        print('RESULT '+json.dumps({'passed':passed,'temp':str(temp)}),flush=True)
    finally:
        if cdp:
            try:browser.stdin.close()
            except Exception:pass
        if browser:
            try:browser.wait(timeout=5)
            except subprocess.TimeoutExpired:browser.terminate()
        backend.terminate()
        try:backend.wait(timeout=5)
        except subprocess.TimeoutExpired:backend.kill()
        log.close()


if __name__=='__main__':main()
