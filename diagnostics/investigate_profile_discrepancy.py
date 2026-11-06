"""Read-only source comparison; writes only the requested diagnostic PNG."""
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import sys

import ezdxf
import h5py
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.core.hdf_reader import HdfReader
from backend.app.core.rasplot_db import build_db_from_plan, recalc_cumdist

DATA = ROOT / 'RAS Plot Database and Model Linework'
HDF = DATA / 'Model Data/1033_North LOMR As-Built.p04.hdf'
OLD = DATA / 'Model Exported Linework.dxf'
NEW = ROOT / 'diagnostics/phase1_legacy_profile.dxf'
MDB = DATA / 'Model Exported.mdb'
PNG = ROOT / 'diagnostics/rasplot_profile_discrepancy_zoom.png'


def mdb_rows():
    # 32-bit Jet provider, read-only connection and default read-only recordset.
    command = """
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$c=New-Object -ComObject ADODB.Connection
try {
$c.Mode=1
$c.Open('Provider=Microsoft.Jet.OLEDB.4.0;Data Source=RAS Plot Database and Model Linework/Model Exported.mdb;Mode=Read;Persist Security Info=False;')
$r=$c.Execute('SELECT * FROM [Working] ORDER BY [cumdist]')
while(-not $r.EOF){
 $v=@{}
 foreach($f in $r.Fields){
  $value=$f.Value
  if($value -is [single]){$value=[double]$value}
  $v[$f.Name]=$value
 }
 $v|ConvertTo-Json -Compress
 $r.MoveNext()
}
$r.Close()
}finally{if($c.State -ne 0){$c.Close()}}
"""
    exe = Path(os.environ['WINDIR']) / 'SysWOW64/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                             base64.b64encode(command.encode('utf-16le')).decode()],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]


def lines(doc, layer):
    return [np.array(e.get_points('xy'), dtype=float) for e in doc.modelspace()
            if e.dxftype() == 'LWPOLYLINE' and e.dxf.layer == layer]


def main():
    sources = [HDF, OLD, NEW, MDB, HDF.with_suffix('').with_suffix('.f03'),
               HDF.with_suffix('').with_suffix('.g04')]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    legacy = mdb_rows()
    for r in legacy:
        r['rs'] = round(r['rs'], 4)  # Match station labels, not float32 storage noise.
    plan = HdfReader(HDF).read_plan()
    db = build_db_from_plan(plan, reach_idx=0)
    recalc_cumdist(db.working, start=2775.0)
    old_doc, new_doc = ezdxf.readfile(OLD), ezdxf.readfile(NEW)
    old, new = (max(lines(doc, 'WS100'), key=len) for doc in (old_doc, new_doc))
    print('COUNTS OLD', len(old), 'NEW', len(new), 'MDB', len(legacy), 'WORKING', len(db.working))
    with h5py.File(HDF, 'r') as f:
        attrs = f['Geometry/Cross Sections/Attributes'][()]
        info = f['Geometry/Cross Sections/Station Elevation Info'][()]
        terrain = f['Geometry/Cross Sections/Station Elevation Values'][()]
        base = f['Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Cross Sections']
        water, flow = base['Water Surface'][0], base['Flow'][0]
        raw = {}
        for i, a in enumerate(attrs):
            start, count = info[i]
            points = terrain[start:start+count]
            channel = points[(points[:, 0] >= a['Left Bank']) & (points[:, 0] <= a['Right Bank'])]
            raw[float(a['RS'])] = dict(wsel=float(water[i]), flow=float(flow[i]),
                                     minchel=float(channel[:, 1].min()), length=float(a['Len Channel']))
    compare = []
    for oi, (ox, oy) in enumerate(old):
        ni = int(np.argmin(abs(new[:, 0] - ox)))
        nx, ny = new[ni]
        if abs(nx-ox) < .01:
            row = min(legacy, key=lambda r: abs(r['cumdist']-ox))
            compare.append((oi, ni, row['rs'], ox, nx, nx-ox, oy, ny, ny-oy))
        else:
            row = min(legacy, key=lambda r: abs(r['cumdist']-ox))
            y = float(np.interp(ox, new[::-1, 0], new[::-1, 1]))
            print('EXTRA OLD', oi, row['struc'], 'X', ox, 'Y', oy,
                  'OLD_ELEV', 1069+oy/100, 'NEW_LINE_Y', y, 'NEW_LINE_ELEV', 1069+y/100,
                  'OLD_MINUS_NEW_FT', (oy-y)/100)
    print('ALL COMPARABLE VERTICES; zero-based; deltas NEW-OLD; sorted by abs(delta Y)')
    print('old_i,new_i,RS,old_x,new_x,dx,old_y,new_y,dy')
    for row in sorted(compare, key=lambda r: -abs(r[-1])):
        print(','.join(str(v) if i<2 else f'{v:.9f}' for i,v in enumerate(row)))
    errors = []
    cad_errors = []
    for r in legacy:
        oi = int(np.argmin(abs(old[:, 0]-r['cumdist'])))
        expected = 100*(r['Prof1']-1069)
        cad_errors.append((r['rs'], r['struc'], old[oi, 1]-expected))
        if not r['struc']:
            errors.append((r['rs'], raw[r['rs']]['wsel']-r['Prof1']))
        if r['rs'] in (.928, .9425, .943, .948):
            current = next(w for w in db.working if float(w.rs)==r['rs'] and w.struc.upper()==r['struc'].upper())
            print('TRACE MDB', json.dumps(r, sort_keys=True))
            print('TRACE CURRENT', dict(RS=current.rs, CUMDIST=current.cumdist,
                  PF1=current.profiles['PF 1'], MINCHEL=current.minchel,
                  TYPE=current.struc or current.kind, LENGTHCHNL=current.length_chnl))
            print('TRACE OLD DXF', oi, old[oi].tolist(), 'ELEV',1069+old[oi,1]/100,
                  'MDB_EXPECTED_Y',expected,'DELTA_Y',old[oi,1]-expected)
            if not r['struc']:
                idx = next(i for i,x in enumerate(plan.reaches[0].cross_sections) if float(x.rs)==r['rs'])
                ni = int(np.argmin(abs(new[:,0]-current.cumdist)))
                print('TRACE RAW',raw[r['rs']], 'READER_WSEL',plan.profiles[0].wsel[idx],
                      'NEW_DXF',new[ni].tolist(),'NEW_ELEV',1069+new[ni,1]/100)
    maximum = max(abs(e) for _, e in errors)
    print('HDF_MINUS_MDB_MAX', maximum, 'STATIONS', [r for r, e in errors if abs(e)==maximum])
    print('MDB_EQUALS_FLOAT32_HDF_ROUNDED_TO_3_DECIMALS', all(
        float(np.float32(round(raw[r['rs']]['wsel'], 3))) == r['Prof1']
        for r in legacy if not r['struc']))
    print('HDF_MINUS_MDB_GT_0.001',[r for r in errors if abs(r[1])>.001])
    print('HDF_MINUS_MDB_GT_0.01',[r for r in errors if abs(r[1])>.01])
    print('OLD_DXF_MINUS_MDB_EXPECTED_Y_MAX',max(cad_errors,key=lambda r:abs(r[2])))
    new_errors=[]
    for w in db.working:
        if w.kind=='xs':
            ni=int(np.argmin(abs(new[:,0]-w.cumdist)))
            new_errors.append(abs(1069+new[ni,1]/100-raw[float(w.rs)]['wsel']))
    print('NEW_DXF_MINUS_RAW_HDF_MAX_FT',max(new_errors))
    # Piecewise-linear separation reaches its extrema at the union of breakpoints.
    knots=np.unique(np.concatenate((old[:,0],new[:,0])))
    knots=knots[(knots>=max(old[:,0].min(),new[:,0].min())) & (knots<=min(old[:,0].max(),new[:,0].max()))]
    separation=np.interp(knots,old[:,0],old[:,1])-np.interp(knots,new[::-1,0],new[::-1,1])
    k=int(np.argmax(abs(separation)))
    print('MAX_SAME_X_SEPARATION',knots[k],separation[k],'DXF units',separation[k]/100,'ft')
    fig, ax=plt.subplots(figsize=(12,7),dpi=180,layout='constrained')
    ax.plot(*old.T,color='#185caa',lw=2.5,marker='o',ms=5,label='OLD WS100 (includes structure rows)')
    ax.plot(*new.T,color='#dd7711',lw=2,ls='--',marker='x',ms=7,label='NEW WS100 (real cross sections)')
    for doc,color,style,label in [(old_doc,'#555555','-','OLD culvert'),(new_doc,'#a577a8','--','NEW culvert')]:
        for i,line in enumerate(lines(doc,'CULVERT')):
            ax.plot(*line.T,color=color,ls=style,lw=1.4,label=label if i==0 else None)
    for oi,label,offset in [(24,'XS RS 0.928\nCUMDIST 5276.820',(-110,-48)),
                            (25,'CULVERT-DN, RS 0.9425\nCUMDIST 5294.291',(-175,40)),
                            (26,'CULVERT-UP, RS 0.9425\nCUMDIST 5404.291',(25,-65)),
                            (27,'XS RS 0.943\nCUMDIST 5409.291',(32,15)),
                            (28,'XS RS 0.948\nCUMDIST 5451.840',(22,-25))]:
        ax.annotate(label,old[oi],xytext=offset,textcoords='offset points',fontsize=8,
                    arrowprops=dict(arrowstyle='-',color='#666666'))
    x=old[25,0]; oy=old[25,1]; ny=np.interp(x,new[::-1,0],new[::-1,1])
    ax.annotate('',(x,oy),xytext=(x,ny),arrowprops=dict(arrowstyle='<->',color='#b62035',lw=1.8))
    ax.text(5335,3803,f'Same-X difference at downstream face: {(oy-ny)/100:.6f} ft\nOld plateau uses upstream XS water surface.',fontsize=9,color='#932033')
    ax.set(xlim=(5220,5515),ylim=(3770,4380),xlabel='DXF X = CUMDIST (ft)',
           ylabel='DXF Y = 100 × (elevation − 1069)',title='Culvert profile discrepancy: extra legacy structure vertices')
    sec=ax.secondary_yaxis('right',functions=(lambda y:1069+y/100,lambda e:100*(e-1069)))
    sec.set_ylabel('Engineering elevation (ft)')
    ax.grid(alpha=.22)
    ax.legend(loc='upper left',fontsize=8)
    fig.savefig(PNG)
    plt.close(fig)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==before[p] for p in sources), 'Source changed during diagnostic'
    print('SOURCE_HASHES_UNCHANGED',len(sources),'PNG',PNG)


if __name__=='__main__':
    main()
