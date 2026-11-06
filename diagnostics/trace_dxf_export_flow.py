"""Exercise real export handlers with synthetic sessions and temporary outputs."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.core.rasplot_db import RasplotDatabase, WorkingRow, ProfileMeta, PanelDef
from backend.app.routers import report


def sample():
    db = RasplotDatabase()
    db.working = [WorkingRow(id=i+1, river='Diagnostic', reach='Synthetic', rs=str(x),
                            cumdist=x, minchel=1070+i, profiles={'PF 1': 1080+i*10})
                  for i, x in enumerate((0, 900, 1200, 5200, 8000))]
    db.profiles = [ProfileMeta(name='PF 1', selected=True)]
    db.panels = [PanelDef(7, 0, 6500, 1070, 1115, panel_x=3, panel_y=4),
                 PanelDef(9, 6000, 12500, 1080, 1125, panel_x=28, panel_y=12)]
    db.header.revision_start_station = 1000
    db.header.revision_end_station = 5200
    return db


def snapshot(db):
    return {'panels': [asdict(p) for p in db.panels],
            'revision': [db.header.revision_start_station, db.header.revision_end_station],
            'header_scales': [db.header.h_scale, db.header.v_scale]}


def main():
    scenarios = [
        ('dxf_defaults', {}),
        ('dxf_dialog_defaults', {'use_current_view': True, 'fit_to_one_panel': True}),
        ('dxf_preserve', {'use_current_view': False}),
        ('dxf_fit_overrides_false', {'use_current_view': False, 'fit_to_one_panel': True}),
        ('dxf_single_overrides_false', {'use_current_view': False, 'single_panel': True}),
        ('dxf_empty_panels_fallback', {'use_current_view': False}),
        ('pdf_defaults', {'format': 'pdf'}),
        ('pdf_preserve', {'format': 'pdf', 'use_current_view': False}),
        ('pdf_alias', {}),
        ('invalid_format', {'format': 'invalid'}),
        ('dxf_export_failure', {'use_current_view': True, 'fit_to_one_panel': True}),
        ('dxf_no_xs', {}),
    ]
    with TemporaryDirectory(prefix='rasplot_export_trace_') as temp:
        for name, options in scenarios:
            db = sample()
            if name == 'dxf_empty_panels_fallback': db.panels = []
            if name == 'dxf_no_xs': db.working = []
            before = snapshot(db)
            original_panels = db.panels
            sid = 'diagnostic_' + name
            with patch.dict(report._SESSIONS, {sid: {'db': db}}), patch.object(report, '_cache_dir', return_value=temp):
                try:
                    if name == 'pdf_alias':
                        result = report.build_profile_pdf(report.ReportRequest(session_id=sid))
                    elif name == 'dxf_export_failure':
                        with patch.object(report, 'export_dxf', side_effect=RuntimeError('diagnostic export failure')):
                            result = report.export_profile(report.ExportOptions(
                                **{'session_id': sid, 'format': 'dxf', **options}))
                    else:
                        result = report.export_profile(report.ExportOptions(**{'session_id': sid, 'format': 'dxf', **options}))
                    outcome = {k: v for k, v in result.items() if k != 'path'}
                    assert Path(result['path']).is_file()
                except Exception as error:
                    outcome = {'error': str(error)}
            after = snapshot(db)
            print(json.dumps({'scenario': name, 'before': before, 'after': after,
                              'panels_changed': before['panels'] != after['panels'],
                              'panel_list_replaced': original_panels is not db.panels,
                              'revision_changed': before['revision'] != after['revision'],
                              'outcome': outcome}))
            assert before['panels'] == after['panels']
            assert original_panels is db.panels
            assert before['revision'] == after['revision']
    print('Temporary export outputs cleaned; no production or benchmark files used as writable targets.')


if __name__ == '__main__':
    main()
