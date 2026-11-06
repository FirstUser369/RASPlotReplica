"""Read-only model/session trace; recalculation runs only on deep copies.

Run from repo root: .venv/Scripts/python.exe diagnostics/trace_muncie_cumdist.py
Outputs are confined to diagnostics/muncie_cumdist_*.
"""
import csv
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.core.hdf_reader import HdfReader
from backend.app.core.merge import compute_cumdist, merge_reaches
from backend.app.core.rasplot_db import build_working_from_plan, recalc_cumdist


def get(path):
    with urlopen('http://localhost:8765/api/' + path) as response:
        return json.load(response)


def clean(value):
    if isinstance(value, bytes):
        return value.decode().strip()
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


session = get('session/s1')
live = get('db/s1')
profile = get('profile/s1?reach_idx=0&profiles=Maximum%20WSEL')
path = Path(session['plan_path'])
assert path.name == 'Muncie.p04.hdf', path
plan = HdfReader(path).read_plan()
reach = plan.reaches[0]
assert (reach.river, reach.reach, len(reach.cross_sections)) == ('White', 'Muncie', 61)
rows = build_working_from_plan(plan)
recalculated = recalc_cumdist(deepcopy(rows))
xs_only = recalc_cumdist(deepcopy([r for r in rows if r.kind == 'xs']))
live_by_id = {r['id']: r for r in live['working']}
recalc_by_id = {r.id: r for r in recalculated}
xs_recalc = {r.rs: r.cumdist for r in xs_only}
profile_by_rs = {r['rs']: r for r in profile['cross_sections']}
with h5py.File(path, 'r') as hdf:
    raw_xs = [{k: clean(r[k]) for k in r.dtype.names} for r in hdf['Geometry/Cross Sections/Attributes'][()]]
    raw_structs = [{k: clean(r[k]) for k in r.dtype.names} for r in hdf['Geometry/Structures/Attributes'][()]]
    raw_attrs = {k: clean(v) for k, v in hdf.attrs.items() if k != 'Projection'}
raw_by_rs = {r['RS']: r for r in raw_xs}
records = []
for row in rows:
    raw = raw_by_rs.get(row.rs, {}) if row.kind == 'xs' else {}
    records.append(dict(id=row.id, river=row.river, reach=row.reach, rs=row.rs,
        kind=row.kind, structure=row.struc, raw_left=raw.get('Len Left'),
        raw_channel=raw.get('Len Channel'), raw_right=raw.get('Len Right'),
        working_length=row.length_chnl, initial_cumdist=row.cumdist,
        live_cumdist=live_by_id[row.id]['cumdist'],
        recalc_all_cumdist=recalc_by_id[row.id].cumdist,
        recalc_xs_only=xs_recalc.get(row.rs),
        profile_cumdist=profile_by_rs.get(row.rs, {}).get('cumdist')))
with (ROOT / 'diagnostics/muncie_cumdist_rows.csv').open('w', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)

structure_details = []
for raw, struct in zip(raw_structs, reach.structures):
    matched = [r for r in rows if r.kind == 'struct' and r.rs == struct.rs]
    idx = matched[0].source_xs_index
    neighbors = [records[next(r.id - 1 for r in rows if r.kind == 'xs' and r.source_xs_index == j)]
                 for j in range(max(0, idx - 1), min(len(reach.cross_sections), idx + 2))]
    structure_details.append(dict(raw=raw, parsed=asdict(struct),
        working=[asdict(r) for r in matched], neighbors=neighbors,
        profile_chainage_extent=[p[0] for p in struct.profile_data]))

geometry_lines = []
geometry_path = path.parent / 'Muncie.g04'
lines = geometry_path.read_text(errors='replace').splitlines()
for i, line in enumerate(lines):
    if line.startswith('Type RM Length L Ch R') or line.startswith('Lateral Weir '):
        geometry_lines.append({'line': i + 1, 'text': line})
summary = dict(path=str(path), geometry_source=str(plan.geometry_path), hdf_attributes=raw_attrs,
    xs_count=len(reach.cross_sections), structure_count=len(reach.structures),
    initial_extent=[min(r.cumdist for r in rows if r.kind == 'xs'), max(r.cumdist for r in rows if r.kind == 'xs')],
    recalc_extent=[min(r.cumdist for r in recalculated if r.kind == 'xs'), max(r.cumdist for r in recalculated if r.kind == 'xs')],
    xs_only_recalc_extent=[min(xs_recalc.values()), max(xs_recalc.values())],
    live_matches_fresh=all(abs(r.cumdist-live_by_id[r.id]['cumdist']) < 1e-8 for r in rows),
    profile_matches_live=all(abs(r['cumdist']-profile_by_rs[r['rs']]['cumdist']) < 1e-8 for r in live['working'] if r['kind']=='xs'),
    structure_profile_matches_live=all(any(s['rs']==r['rs'] and s['type']==r['struc'] and s['cumdist']==r['cumdist'] for s in profile['structures']) for r in live['working'] if r['kind']=='struct'),
    structures=structure_details, geometry_text_selected_lines=geometry_lines,
    merge_one_reach_extent=[min(merge_reaches([reach], [0]).cumdist), max(merge_reaches([reach], [0]).cumdist)],
    live_panels=live['panels'], live_header=live['header'])
(ROOT / 'diagnostics/muncie_cumdist_evidence.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
print(json.dumps({k:v for k,v in summary.items() if k not in ('structures','geometry_text_selected_lines','live_panels','live_header')}, indent=2))
for s in structure_details:
    print('STRUCTURE', s['raw']['RS'], 'raw', {k:s['raw'][k] for k in ['Type','US RS','DS RS','Upstream Distance','LW HW Distance','LW TW Distance','Weir Width']})
    for n in s['neighbors']:
        print('NEIGHBOR', json.dumps(n))
    for r in s['working']:
        print('FACE', r['rs'], r['cumdist'], 'recalc', recalc_by_id[r['id']].cumdist)
