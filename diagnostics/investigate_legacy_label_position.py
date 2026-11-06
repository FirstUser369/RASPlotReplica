"""Read-only label investigation. Prints evidence; writes no output files."""
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import ezdxf
import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'RAS Plot Database and Model Linework'


def database():
    command = """
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$engine=New-Object -ComObject DAO.DBEngine.36
$db=$engine.OpenDatabase((Join-Path (Get-Location) 'RAS Plot Database and Model Linework/Model Exported.mdb'),$false,$true)
try {
foreach($t in $db.TableDefs){
 if($t.Name -notlike 'MSys*'){
 $fields=@();foreach($f in $t.Fields){$fields+=@{name=$f.Name;type=$f.Type}}
 @{kind='schema';table=$t.Name;fields=$fields}|ConvertTo-Json -Depth 5 -Compress
 }
}
foreach($table in @('Working','Landmark','panel','profiles','workheader','noteField','noteText','page')){
 $r=$db.OpenRecordset('SELECT * FROM ['+$table+']',4)
 $count=0
 while(-not $r.EOF){
  $v=@{};foreach($f in $r.Fields){$value=$f.Value;if($value -is [single]){$value=[double]$value};$v[$f.Name]=$value}
  @{kind='row';table=$table;values=$v}|ConvertTo-Json -Depth 5 -Compress
  $count++;$r.MoveNext()
 }
 @{kind='count';table=$table;count=$count}|ConvertTo-Json -Compress
 $r.Close()
}
}finally{$db.Close()}
"""
    exe=Path(os.environ['WINDIR'])/'SysWOW64/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-EncodedCommand',
                           base64.b64encode(command.encode('utf-16le')).decode()],
                          cwd=ROOT,capture_output=True,text=True,check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]


def main():
    hdf=DATA/'Model Data/1033_North LOMR As-Built.p04.hdf'
    companions=[hdf.with_suffix('').with_suffix('.'+ext) for ext in ('g04','p04','f03')]
    sources=[hdf,*companions,DATA/'Model Exported.mdb',DATA/'Model Exported Linework.dxf']
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    records=database()
    rows=[r['values'] for r in records if r['kind']=='row' and r['table']=='Working']
    for r in records:
        if r['kind']!='row' or r['table']!='Working' or r['values'].get('labeltext') or r['values'].get('struc'):
            print('MDB',json.dumps(r))
    doc=ezdxf.readfile(sources[-1])
    texts=[e for e in doc.modelspace() if e.dxftype() in ('TEXT','MTEXT')]
    for e in texts:
        text=e.dxf.text if e.dxftype()=='TEXT' else e.text
        print('TEXT',repr(text),e.dxf.layer,tuple(e.dxf.insert),e.dxf.get('height'),e.dxf.get('rotation',0))
        if any(word in text.upper() for word in ('WEST','OLIVE','AVENUE')):
            print('MATCH_ATTRIBUTES',e.dxf.all_existing_dxf_attribs())
            print('MATCH_DEFAULTS',{key:getattr(e.dxf,key) for key in ('width','style','halign','valign','extrusion')})
            print('STYLE',doc.styles.get(e.dxf.style).dxf.all_existing_dxf_attribs())
            label=e
    print('TEXT_COUNT',len(texts))
    dn=next(r for r in rows if r['struc'].upper()=='CULVERT-DN')
    up=next(r for r in rows if r['struc'].upper()=='CULVERT-UP')
    x,y=label.dxf.insert.x,label.dxf.insert.y
    midpoint=(dn['cumdist']+up['cumdist'])/2
    candidates={'DN + 30':dn['cumdist']+30,'midpoint':midpoint,
                'midpoint - 25':midpoint-25,'midpoint + 30':midpoint+30,
                'midpoint - 30':midpoint-30,'DN + height':dn['cumdist']+label.dxf.height,
                'DN + half height':dn['cumdist']+label.dxf.height/2,
                'DN + 0.625 height':dn['cumdist']+.625*label.dxf.height,
                'DN + 0.06 HScale':dn['cumdist']+.06*500,
                'grid 5300':5300}
    for r in rows:
        if round(r['rs'],4) in (.928,.943,.948):candidates['XS '+str(round(r['rs'],4))]=r['cumdist']
    for name,value in candidates.items():print('X_RULE',name,'prediction',value,'observed-predicted',x-value)
    elevation=1069+y/100
    print('LABEL_ELEV',elevation)
    refs={'panel start':1070,'panel end':1115,'panel midpoint':1092.5}
    for tag,r in [('DN',dn),('UP',up)]:
        for field in ('minchel','lowchord','highchord','Prof1'):refs[tag+' '+field]=r[field]
    for r in rows:
        if round(r['rs'],4) in (.928,.943,.948):
            for field in ('minchel','Prof1'):refs['XS '+str(round(r['rs'],4))+' '+field]=r[field]
    culvert=next(e for e in doc.modelspace() if e.dxftype()=='LWPOLYLINE' and e.dxf.layer=='CULVERT')
    points=np.array(culvert.get_points('xy'))
    print('CULVERT_POINTS',points.tolist())
    refs['culvert bbox top']=1069+points[:,1].max()/100
    refs['culvert bbox bottom']=1069+points[:,1].min()/100
    refs['culvert bbox midpoint']=1069+(points[:,1].max()+points[:,1].min())/200
    for name,value in refs.items():print('Y_REF',name,'elev',value,'label-minus-ref-ft',elevation-value,'dxf',100*(elevation-value))
    border=next(e for e in doc.modelspace() if e.dxftype()=='INSERT' and e.dxf.name=='BORDER')
    print('BORDER_INSERT',border.dxf.all_existing_dxf_attribs())
    print('BORDER_BASE',tuple(doc.blocks['BORDER'].block.dxf.base_point))
    for layer in ('GRID_MAJOR','GRID_MINOR'):
        ys=set();xs=set()
        for e in border.virtual_entities():
            if e.dxf.layer!=layer:continue
            if e.dxftype()=='LWPOLYLINE':pts=np.array(e.get_points('xy'))
            elif e.dxftype()=='LINE':pts=np.array([tuple(e.dxf.start)[:2],tuple(e.dxf.end)[:2]])
            else:continue
            if np.ptp(pts[:,1])<1e-7:ys.add(round(float(pts[0,1]),6))
            if np.ptp(pts[:,0])<1e-7:xs.add(round(float(pts[0,0]),6))
        print('GRID',layer,'horizontal_Y',sorted(ys),'vertical_X',sorted(xs))
    print('PANEL_FRACTION',(y-100)/(4600-100),'MAJOR_BOXES',(y-100)/500,'MINOR_BOXES',(y-100)/50,'FROM_TOP',4600-y)
    needle='WEST OLIVE AVENUE'
    for path in companions:
        text=path.read_text(errors='replace')
        print('SOURCE_SEARCH',path.name,'exact',needle in text,'case_insensitive',needle.casefold() in text.casefold())
        for line in text.splitlines():
            if 'olive' in line.casefold():print('RELATED_TEXT',line)
    hits=[];related=[]
    def search(value,path):
        a=np.asarray(value)
        if a.dtype.names:
            for field in a.dtype.names:search(a[field],path+' field '+field)
        elif a.dtype.kind in 'SUO':
            for item in a.reshape(-1):
                text=item.decode('utf8','replace') if isinstance(item,bytes) else str(item)
                if needle.casefold() in text.casefold():hits.append((path,text,needle in text))
                elif 'olive' in text.casefold():related.append((path,text))
    with h5py.File(hdf,'r') as f:
        def visit(name,obj):
            search(obj.name,obj.name)
            for key,value in obj.attrs.items():search(key,obj.name);search(value,obj.name+' @'+key)
            if isinstance(obj,h5py.Dataset):search(obj[()],obj.name)
        visit('',f);f.visititems(visit)
    print('HDF_EXACT_OR_CASE_INSENSITIVE_HITS',hits,'RELATED',related)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p] for p in sources)
    print('SOURCE_HASHES_UNCHANGED',len(sources))


if __name__=='__main__':
    main()
