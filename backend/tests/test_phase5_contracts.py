"""Phase 5 bypass regressions. Synthetic tests do not expand engineering support."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import time
import unittest
import h5py
import numpy as np
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.hdf_reader import HdfReader, _s, AlignmentError
from backend.app.core.hydraulics import resolve_profiles
from backend.app.core.rasplot_db import build_db_from_plan, PanelDef, auto_panels
from backend.app.core.state_contract import stationing_dirty, panels_stale, review_panels, validate_collection
from backend.app.core.local_state import register_artifact, owned_path, ARTIFACTS, cleanup_artifacts
from backend.app.core.export_geometry import validate_export
from backend.app.routers import hecras_api as api, report
from backend.tests.test_hydraulics import model
from backend.tests.test_structures import NORTH, MUNCIE


class Phase5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = patch('tempfile.tempdir', self.tmp.name)
        self.cache.start(); self.addCleanup(self.cache.stop)
        self.plan, self.db = model()
        self.sid = api._new_id()
        api._SESSIONS[self.sid] = {'plan':self.plan,'db':self.db,'reach_idx':0}
        self.addCleanup(lambda: api._SESSIONS.pop(self.sid,None))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def patch_length(self, value):
        return api.patch_working(self.sid,[api.WorkingPatch(id=self.db.working[0].id,length_chnl=value)])

    def test_length_dirty_source_preserved_and_recalculation_clears(self):
        source=self.db.working[0].source_length_chnl
        panels=deepcopy(self.db.panels)
        self.patch_length(200)
        self.assertTrue(stationing_dirty(self.db))
        self.assertEqual(self.db.working[0].source_length_chnl,source)
        with self.assertRaisesRegex(ValueError,'DIRTY'):
            validate_export(self.db,self.db.panels,self.db.profiles,(17,11),(0,400))
        api.post_recalc_cumdist(self.sid,api.CumdistRequest(start=17))
        self.assertFalse(stationing_dirty(self.db))
        self.assertEqual(self.db.working[0].cumdist-self.db.working[1].cumdist,200)
        self.assertEqual(min(r.cumdist for r in self.db.working),17)
        self.assertEqual(self.db.panels,panels)
        self.assertTrue(panels_stale(self.db))

    def test_unchanged_edit_stays_fresh(self):
        self.patch_length(100)
        self.assertFalse(stationing_dirty(self.db));self.assertFalse(panels_stale(self.db))

    def test_label_annotations_invalidate_review_only_when_changed(self):
        review_panels(self.db)
        row = self.db.working[0]
        api.patch_working(self.sid, [api.WorkingPatch(id=row.id, xs_id=row.source_identity,
                                                       label_letter=row.label_letter,
                                                       label_text=row.label_text or "")])
        self.assertFalse(panels_stale(self.db))
        api.patch_working(self.sid, [api.WorkingPatch(id=row.id, xs_id=row.source_identity,
                                                       label_letter="ZZ")])
        self.assertTrue(panels_stale(self.db))
        review_panels(self.db)
        api.patch_working(self.sid, [api.WorkingPatch(id=row.id, xs_id=row.source_identity,
                                                       label_text="Reviewed crossing")])
        self.assertTrue(panels_stale(self.db))
        with self.assertRaisesRegex(ValueError, "review"):
            validate_export(self.db, self.db.panels, self.db.profiles, (17, 11), (0, 400))

    def test_panel_stale_reasons_use_reliable_dependency_categories(self):
        review_panels(self.db)
        row = self.db.working[0]
        api.patch_working(self.sid, [api.WorkingPatch(id=row.id, xs_id=row.source_identity, label_text="Changed")])
        result = self.db.to_dict()
        self.assertTrue(result["panels_stale"])
        categories = {item["category"] for item in result["panel_stale_reasons"]}
        self.assertEqual(categories, {"cross-section annotations"})
        self.assertEqual(result["panel_stale_reasons"][0]["affected_panels"], [panel.display_id for panel in self.db.panels])
        change = result["panel_stale_reasons"][0]["changes"][0]
        self.assertEqual(change["field"], "label_text")
        self.assertEqual(change["previous"], None)
        self.assertEqual(change["current"], "Changed")

    def test_wsel_invalidation_identifies_changed_value_and_affected_panel(self):
        self.db.panels = [
            PanelDef(1, 0, 199, 0, 45, h_scale=50, v_scale=5),
            PanelDef(2, 199, 400, 0, 45, h_scale=50, v_scale=5),
        ]
        review_panels(self.db)
        row = max((item for item in self.db.working if item.kind == "xs"), key=lambda item: item.cumdist)
        profile_id = self.db.profiles[0].profile_id
        original = row.profiles[self.db.profiles[0].key]
        api.patch_working(self.sid, [api.WorkingPatch(
            id=row.id, xs_id=row.source_identity, profiles={profile_id: original + 1.25})])
        reason = next(item for item in self.db.to_dict()["panel_stale_reasons"]
                      if item["category"] == "working geometry and hydraulics")
        self.assertEqual(reason["affected_panels"], ["2"])
        change = next(item for item in reason["changes"] if item["field"].startswith("WSEL "))
        self.assertEqual(change["previous"], original)
        self.assertEqual(change["current"], original + 1.25)

    def test_panel_scale_mutation_is_attributed_to_that_panel(self):
        review_panels(self.db)
        panel = self.db.panels[0]
        previous = panel.v_scale
        panel.v_scale = previous * 2
        reason = next(item for item in self.db.to_dict()["panel_stale_reasons"]
                      if item["category"] == "panel definitions and scales")
        self.assertEqual(reason["affected_panels"], [panel.display_id])
        change = next(item for item in reason["changes"] if item["field"] == "v_scale")
        self.assertEqual((change["previous"], change["current"]), (previous, previous * 2))

    def test_auto_letter_reports_noop_and_changed_counts(self):
        review_panels(self.db)
        first = api.post_auto_letter(self.sid, every=5)
        self.assertEqual(first, {
            "ok": True, "cross_sections_evaluated": 5,
            "letters_assigned": 2, "letters_changed": 2, "no_op": False,
        })
        self.assertTrue(panels_stale(self.db))
        review_panels(self.db)
        unchanged = api.post_auto_letter(self.sid, every=5)
        self.assertEqual(unchanged["letters_changed"], 0)
        self.assertTrue(unchanged["no_op"])
        self.assertFalse(panels_stale(self.db))
        self.db.working[0].label_letter = "ZZ"
        review_panels(self.db)
        second = api.post_auto_letter(self.sid, every=5)
        self.assertEqual(second["cross_sections_evaluated"], 5)
        self.assertEqual(second["letters_assigned"], 2)
        self.assertEqual(second["letters_changed"], 1)
        self.assertFalse(second["no_op"])
        self.assertTrue(panels_stale(self.db))

    def test_internal_length_swap_same_extent_is_dirty(self):
        self.db.working[0].length_chnl=150;self.db.working[1].length_chnl=50
        self.assertTrue(stationing_dirty(self.db))

    def test_dirty_preview_and_panel_operations_block(self):
        self.patch_length(200)
        self.assertEqual(self.client.get(f'/api/profile/{self.sid}').status_code,409)
        self.assertEqual(self.client.put(f'/api/db/{self.sid}/panels',json={'auto':True,'draft':True,'panels':[]}).status_code,400)

    def test_profile_name_id_collision_edits_only_requested_id(self):
        plan,_=model(('steady-row-1','Other'))
        for i,p in enumerate(plan.profiles):p.profile_id=f'steady-row-{i}'
        db=build_db_from_plan(plan);api._SESSIONS[self.sid].update(plan=plan,db=db)
        api.patch_working(self.sid,[api.WorkingPatch(id=db.working[0].id,profiles={'steady-row-0':99})])
        self.assertEqual(db.working[0].profiles,{'steady-row-0':99,'steady-row-1':15})
        self.assertEqual(db.working[0].raw_profiles['steady-row-0'],15)
        self.assertEqual(resolve_profiles(db.profiles,['steady-row-1'])[0].source_name,'Other')
        with self.assertRaises(ValueError):resolve_profiles(db.profiles,['Other'])

    def test_mapping_requires_id_even_with_name(self):
        with self.assertRaises(HTTPException):api.put_profiles_meta(self.sid,[api.ProfileMetaUpdate(name='P',frequency='1%')])

    def test_preview_supplies_shared_gap_segments(self):
        key=self.db.profiles[0].profile_id
        api.patch_working(self.sid,[api.WorkingPatch(id=3,profiles={key:None})])
        response=self.client.get(f'/api/profile/{self.sid}').json()
        self.assertEqual([len(run) for run in response['profile_segments'][key]],[2,2])

    def test_stale_async_context_cannot_mutate_state(self):
        url=f'/api/db/{self.sid}/working'
        current={'X-Rasplot-Context':'browser:2','X-Rasplot-Reach':'0'}
        old={'X-Rasplot-Context':'browser:1','X-Rasplot-Reach':'0'}
        self.assertEqual(self.client.patch(url,headers=current,json=[{'id':1,'label_text':'new'}]).status_code,200)
        self.assertEqual(self.client.patch(url,headers=old,json=[{'id':1,'label_text':'old'}]).status_code,409)
        self.assertEqual(self.db.working[0].label_text,'new')

    def test_wrong_reach_context_cannot_edit(self):
        response=self.client.patch(f'/api/db/{self.sid}/working',headers={'X-Rasplot-Reach':'1'},json=[{'id':1,'label_text':'wrong reach'}])
        self.assertEqual(response.status_code,409)
        self.assertIsNone(self.db.working[0].label_text)

    def test_inventory_missing_attributes_blocks_real_north(self):
        self.assertTrue(NORTH.exists(),'North fixture required')
        contents=BytesIO(NORTH.read_bytes())
        with h5py.File(contents,'r+') as f:del f['Geometry/Structures/Attributes']
        data=contents.getvalue();original=h5py.File
        with patch('backend.app.core.hdf_reader.h5py.File',side_effect=lambda *a,**k:original(BytesIO(data),'r')):
            plan=HdfReader(NORTH).read_plan()
        db=build_db_from_plan(plan)
        self.assertEqual(db.structure_inventory['state'],'INCOMPLETE')
        with self.assertRaisesRegex(ValueError,'inventory'):
            validate_export(db,db.panels,db.profiles,(17,11),(0,max(r.cumdist for r in db.working if r.kind=='xs')))

    def test_partial_culvert_group_inventory_blocks(self):
        contents=BytesIO(NORTH.read_bytes())
        with h5py.File(contents,'r+') as f:del f['Geometry/Structures/Culvert Groups/Attributes']
        data=contents.getvalue();original=h5py.File
        with patch('backend.app.core.hdf_reader.h5py.File',side_effect=lambda *a,**k:original(BytesIO(data),'r')):
            plan=HdfReader(NORTH).read_plan()
        self.assertEqual(plan.structure_inventory['state'],'INCOMPLETE')

    def test_missing_whole_inventory_cannot_override_linked_text(self):
        contents=BytesIO(NORTH.read_bytes())
        with h5py.File(contents,'r+') as f:del f['Geometry/Structures']
        data=contents.getvalue();original=h5py.File
        with patch('backend.app.core.hdf_reader.h5py.File',side_effect=lambda *a,**k:original(BytesIO(data),'r')):
            plan=HdfReader(NORTH).read_plan()
        self.assertEqual(plan.structure_inventory['state'],'INCOMPLETE')

    def test_legacy_omitted_datum_stays_unknown(self):
        captured=[]
        def render(path,db,**kw):captured.append(deepcopy(db.header));Path(path).write_bytes(b'pdf')
        with patch.object(report,'render_profile_pdf_from_db',side_effect=render):
            response=self.client.post('/api/report/profile.pdf',json={'session_id':self.sid})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(captured[0].vertical_datum,'UNKNOWN');self.assertEqual(captured[0].datum_provenance,'UNKNOWN')
        self.assertEqual(captured[0].state,'')

    def test_collection_gap_reports_interval(self):
        panels=[PanelDef(1,0,100,0,45),PanelDef(2,300,400,0,45)]
        with self.assertRaisesRegex(ValueError,'100 to 300'):validate_collection(panels,(0,400))

    def test_collection_overlap_allowed_and_duplicate_ids_rejected(self):
        panels=[PanelDef(1,0,300,0,45),PanelDef(2,200,400,0,45)]
        validate_collection(panels,(0,400))
        panels[1].panel=1
        with self.assertRaisesRegex(ValueError,'unique'):validate_collection(panels,(0,400))

    def test_all_invalid_scales_bounded_before_generation(self):
        for value in (0,-1,float('nan'),float('inf')):
            for field in ('h_scale','v_scale'):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):auto_panels(self.db.working,**{field:value})
        with self.assertRaisesRegex(ValueError,'500'):auto_panels(self.db.working,h_scale=.00001)

    def test_invalid_panel_numeric_and_bounds(self):
        for field,value in [('panel_x',float('nan')),('v_scale',0),('end_station',-1),('end_elevation',-1)]:
            with self.subTest(field=field),self.assertRaises(ValueError):validate_collection([replace(self.db.panels[0],**{field:value})])

    def test_draft_generation_never_mutates_committed_panels_or_header(self):
        before=deepcopy(self.db.to_dict())
        response=api.put_panels(self.sid,api.PanelsReplace(panels=[],auto=True,draft=True,h_scale=25,v_scale=6))
        self.assertGreater(len(response['panels']),1)
        self.assertEqual(self.db.to_dict(),before)

    def test_panel_content_analysis_endpoint_is_read_only_proposal(self):
        saved=deepcopy(self.db.panels)
        result=api.analyze_panels(self.sid,api.PanelContentRequest(
            panels=[api.PanelIn(**asdict(panel)) for panel in self.db.panels],
            rounding_increment=5,page_size="11x17"))
        self.assertTrue(result["proposal_only"])
        self.assertEqual(len(result["panels"]),len(saved))
        self.assertIn("required_minimum_elevation",result["panels"][0])
        self.assertEqual(self.db.panels,saved)

    def test_auto_panels_split_for_vertical_as_well_as_horizontal_capacity(self):
        key = self.db.profiles[0].profile_id
        rows = [
            replace(self.db.working[0], cumdist=0, minchel=100, profiles={key: 102}),
            replace(self.db.working[1], cumdist=1000, minchel=200, profiles={key: 202}),
        ]
        steep = auto_panels(rows, h_scale=500, v_scale=5, station_range=(0, 1000))
        self.assertGreater(len(steep), 1)
        validate_collection(steep, (0, 1000))
        self.assertTrue(all(panel.end_station - panel.start_station <= 6500 + 1e-7 for panel in steep))
        self.assertTrue(all(panel.end_elevation - panel.start_elevation == 45 for panel in steep))
        self.assertTrue(all(steep[index].end_station >= steep[index + 1].start_station
                            for index in range(len(steep) - 1)))
        for panel in steep:
            for station in (panel.start_station, min(panel.end_station, 1000)):
                elevation = 102 + station / 10
                self.assertLessEqual(panel.start_elevation - 1e-7, elevation)
                self.assertLessEqual(elevation, panel.end_elevation + 1e-7)

    def test_auto_panels_single_panel_partial_revision_and_impossible_vertical_range(self):
        key = self.db.profiles[0].profile_id
        panels = auto_panels(self.db.working, h_scale=500, v_scale=50,
                             station_range=(100, 300))
        self.assertEqual(len(panels), 1)
        validate_collection(panels, (100, 300))
        coincident = [
            replace(self.db.working[0], cumdist=0, minchel=0, profiles={key: 100}),
            replace(self.db.working[1], cumdist=100, minchel=1, profiles={key: 101}),
        ]
        with self.assertRaisesRegex(ValueError, "Increase vertical scale"):
            auto_panels(coincident, h_scale=500, v_scale=5, station_range=(0, 100))

    def test_apply_preserves_independent_scales_origins_and_reviews(self):
        api.patch_working(self.sid,[api.WorkingPatch(id=1,profiles={self.db.profiles[0].profile_id:16})])
        self.assertTrue(panels_stale(self.db))
        p=PanelDef(7,0,400,8,53,panel_x=0,panel_y=12,h_scale=40,v_scale=5)
        api.put_panels(self.sid,api.PanelsReplace(panels=[api.PanelIn(**asdict(p))],h_scale=500))
        self.assertEqual(self.db.panels,[p]);self.assertFalse(panels_stale(self.db))

    def test_panel_suffix_persists_through_legacy_apply_draft_and_reload(self):
        panel = PanelDef(27, 0, 400, 0, 45, h_scale=40, v_scale=5, panel_suffix=" p ")
        api.put_panels(self.sid, api.PanelsReplace(panels=[api.PanelIn(**asdict(panel))]))
        self.assertEqual(self.db.panels[0].panel_suffix, "P")
        self.assertEqual(api.get_panels(self.sid)["panels"][0]["display_id"], "27P")
        self.assertEqual(self.db.to_dict()["panels"][0]["display_id"], "27P")

        legacy_payload = asdict(self.db.panels[0])
        legacy_payload.pop("panel_suffix")
        legacy_payload["start_elevation"] = 1
        api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**legacy_payload)]))
        self.assertEqual(self.db.panels[0].panel_suffix, "P")
        self.assertEqual(self.db.panels[0].start_elevation, 1)
        saved = deepcopy(self.db.panels)

        draft_payload = asdict(self.db.panels[0])
        draft_payload.update(panel_suffix="Q", start_elevation=2)
        draft = api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**draft_payload)], draft=True))
        self.assertEqual(draft["panels"][0]["display_id"], "27Q")
        self.assertEqual(self.db.panels, saved)
        self.assertEqual(api.get_panels(self.sid)["panels"][0]["display_id"], "27P")

        clear_payload = asdict(self.db.panels[0])
        clear_payload["panel_suffix"] = "  "
        api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**clear_payload)]))
        self.assertEqual(self.db.panels[0].panel, 27)
        self.assertEqual(self.db.panels[0].panel_suffix, "")
        self.assertEqual(api.get_panels(self.sid)["panels"][0]["display_id"], "27")

    def test_panel_suffix_validation_and_session_isolation(self):
        base = asdict(PanelDef(27, 0, 400, 0, 45))
        for value in ("P/", "P1", "ABCD", "ñ"):
            with self.subTest(suffix=value), self.assertRaises(ValueError):
                api.PanelIn(**{**base, "panel_suffix": value})

        first = PanelDef(27, 0, 400, 0, 45, panel_suffix="P")
        other_db = deepcopy(self.db)
        other_db.panels = [PanelDef(27, 0, 400, 0, 45)]
        api.put_panels(self.sid, api.PanelsReplace(panels=[api.PanelIn(**asdict(first))]))
        other_sid = "suffix-isolation"
        api._SESSIONS[other_sid] = {"db": other_db}
        self.addCleanup(lambda: api._SESSIONS.pop(other_sid, None))
        self.assertEqual(api.get_panels(self.sid)["panels"][0]["display_id"], "27P")
        self.assertEqual(api.get_panels(other_sid)["panels"][0]["display_id"], "27")

    def test_auto_panel_draft_preserves_only_matching_saved_suffixes(self):
        from backend.app.core.state_contract import review_panels
        saved = PanelDef(1, 0, 400, 0, 45, h_scale=500, v_scale=5, panel_suffix="P")
        self.db.panels = [saved]
        review_panels(self.db)
        response = api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**asdict(saved))], auto=True, draft=True,
            h_scale=25, v_scale=5, page_size="11x17"))
        self.assertGreater(len(response["panels"]), 1)
        self.assertEqual(response["panels"][0]["display_id"], "1P")
        self.assertTrue(all(panel["panel_suffix"] == "" for panel in response["panels"][1:]))
        self.assertEqual(self.db.panels, [saved])

    def test_panel_apply_persists_explicit_sheet_without_changing_scales(self):
        panel = PanelDef(1, 0, 400, 0, 45, h_scale=40, v_scale=5)
        response = api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**asdict(panel))], page_size="ANSI_C"))
        self.assertEqual(self.db.header.page_size, "ANSI_C")
        self.assertEqual(response["panels"][0]["h_scale"], 40)
        self.assertEqual(response["panels"][0]["v_scale"], 5)
        self.assertFalse(panels_stale(self.db))

    def test_revision_invalidates_without_replacing_panels(self):
        before=deepcopy(self.db.panels)
        api.put_revision_limits(self.sid,api.RevisionLimitsUpdate(revision_start_station=10,revision_end_station=390))
        self.assertEqual(self.db.panels,before);self.assertTrue(panels_stale(self.db))
        with self.assertRaisesRegex(HTTPException,'review'):
            report.export_profile(report.ExportOptions(session_id=self.sid,panel_numbers=[1]))

    def test_revision_length_recalc_review_export_transition(self):
        url = '/api/report/export'
        request = {'session_id': self.sid, 'panel_numbers': [1]}
        preserved = deepcopy(self.db.panels)
        api.put_revision_limits(self.sid, api.RevisionLimitsUpdate(
            revision_start_station=10, revision_end_station=390))
        self.patch_length(200)
        response = self.client.post(url, json=request)
        self.assertEqual(response.status_code, 409)
        self.assertIn('DIRTY', response.text)
        api.post_recalc_cumdist(self.sid, api.CumdistRequest(start=0))
        self.assertFalse(stationing_dirty(self.db))
        self.assertEqual(self.db.panels, preserved)
        self.assertTrue(panels_stale(self.db))
        response = self.client.post(url, json=request)
        self.assertEqual(response.status_code, 409)
        self.assertIn('review', response.text)
        api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**asdict(p)) for p in preserved]))
        self.assertFalse(panels_stale(self.db))
        response = self.client.post(url, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.panels, preserved)

    def test_revision_export_rejects_outside_but_allows_selected_saved_subset(self):
        api.put_revision_limits(self.sid, api.RevisionLimitsUpdate(
            revision_start_station=100, revision_end_station=300))
        panels = [PanelDef(1, 0, 100, 0, 45), PanelDef(2, 100, 200, 0, 45),
                  PanelDef(3, 200, 300, 0, 45)]
        api.put_panels(self.sid, api.PanelsReplace(
            panels=[api.PanelIn(**asdict(p)) for p in panels]))
        outside = self.client.post('/api/report/export', json={
            'session_id': self.sid, 'panel_numbers': [1]})
        self.assertEqual(outside.status_code, 400)
        self.assertIn('do not overlap', outside.text)

        selected = self.client.post('/api/report/export', json={
            'session_id': self.sid, 'panel_numbers': [2]})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()['panel_count'], 1)

    def test_export_snapshot_retains_validated_state_during_live_mutation(self):
        api.put_revision_limits(self.sid, api.RevisionLimitsUpdate(
            revision_start_station=10, revision_end_station=390))
        panel = PanelDef(1, 0, 400, 0, 45, h_scale=40, v_scale=5,
                         panel_x=2, panel_y=12)
        api.put_panels(self.sid, api.PanelsReplace(panels=[api.PanelIn(**asdict(panel))]))
        baseline = deepcopy(self.db)
        key = self.db.profiles[0].profile_id
        for fmt, renderer in [('pdf', 'render_profile_pdf_from_db'), ('dxf', 'export_dxf')]:
            with self.subTest(format=fmt):
                self.db = deepcopy(baseline)
                api._SESSIONS[self.sid]['db'] = self.db
                def render(path, snapshot, **kw):
                    expected = deepcopy(snapshot.to_dict())
                    self.assertIsNot(snapshot, self.db)
                    self.assertEqual(kw['profile_names'], [key])
                    self.assertEqual(kw['revision_interval'], (10, 390))
                    self.assertEqual(kw['panels'], [panel])
                    self.assertEqual(snapshot.header.datum_provenance, 'USER_ENTERED')
                    self.assertEqual(snapshot.header.vertical_datum, 'NAVD88')
                    self.assertFalse(stationing_dirty(snapshot))
                    self.assertFalse(panels_stale(snapshot))
                    self.db.working[0].length_chnl = 999
                    self.db.working[0].profiles[key] = 999
                    self.db.header.revision_end_station = 200
                    self.db.panels.clear()
                    self.assertEqual(snapshot.to_dict(), expected)
                    validate_export(snapshot, kw['panels'], snapshot.profiles,
                                    (17, 11), kw['revision_interval'])
                    Path(path).write_bytes(b'snapshot')
                with patch.object(report, renderer, side_effect=render):
                    response = self.client.post('/api/report/export', json={
                        'session_id': self.sid, 'format': fmt, 'profile_names': [key],
                        'panel_numbers': [panel.panel], 'vertical_datum': 'NAVD88',
                        'require_known_datum': True})
                self.assertEqual(response.status_code, 200, response.text)

    def test_repeated_pdf_dxf_exports_download_original_owned_bytes(self):
        other = api._new_id()
        api._SESSIONS[other] = {}
        self.addCleanup(lambda: api._SESSIONS.pop(other, None))
        artifacts = []
        for fmt in ('pdf', 'dxf', 'pdf', 'dxf'):
            response = self.client.post('/api/report/export', json={
                'session_id': self.sid, 'format': fmt})
            self.assertEqual(response.status_code, 200, response.text)
            path = response.json()['path']
            download = self.client.get(f'/api/source/{self.sid}/download', params={'path': path})
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.headers['content-type'], 'application/' + fmt)
            self.assertTrue(download.content)
            artifacts.append((path, download.content))
            denied = self.client.get(f'/api/source/{other}/download', params={'path': path})
            self.assertIn(denied.status_code, (400, 404))
        self.assertEqual(len({path for path, _ in artifacts}), 4)
        for path, original in artifacts:
            response = self.client.get(f'/api/source/{self.sid}/download', params={'path': path})
            self.assertEqual(response.content, original)

    def test_fdt_firm_disabled_http(self):
        for name in ('fdt.pdf','firm.zip'):
            response=self.client.post('/api/report/'+name,json={'session_id':self.sid})
            self.assertEqual(response.status_code,403);self.assertIn('disabled',response.text)

    def test_snapshot_unique_artifacts_and_concurrent_settings(self):
        original=deepcopy(self.db.to_dict())
        def render(path,db,**kw):time.sleep(.02);Path(path).write_text(db.header.stream_name)
        def export(name):
            return self.client.post('/api/report/export',json={'session_id':self.sid,'stream_name':name}).json()
        with patch.object(report,'render_profile_pdf_from_db',side_effect=render),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(export,['First','Second']))
        self.assertNotEqual(results[0]['path'],results[1]['path'])
        self.assertEqual([Path(r['path']).read_text() for r in results],['First','Second'])
        self.assertEqual(self.db.to_dict(),original)

    def test_multiperiod_filename_reaches_reader(self):
        source=Path(self.tmp.name)/'Project.revA.p04.hdf';source.touch()
        with patch.object(HdfReader,'read_plan',return_value=self.plan), patch.object(api,'plan_to_dict',return_value={}):
            result=api.open_source(api.OpenRequest(path=str(source)))
        api._SESSIONS.pop(result['session_id'])

    def test_string_array_shape_has_compatibility_error(self):
        self.assertEqual(_s(np.array([b'A'])),'A')
        with self.assertRaisesRegex(AlignmentError,'array shape'):_s(np.array([b'A',b'B']))

    def test_unknown_result_layout_rejected(self):
        source=Path(self.tmp.name)/'empty.p01.hdf'
        with h5py.File(source,'w'):pass
        with self.assertRaisesRegex(AlignmentError,'result layout'):HdfReader(source)._read_profiles(self.plan.reaches)

    def test_static_encoded_traversal_rejected(self):
        for path in ('/%2e%2e/main.py','/%2e%2e/routers/report.py'):
            self.assertEqual(self.client.get(path).status_code,404)

    def test_foreign_host_and_origin_rejected(self):
        for headers in ({'host':'foreign.example'},{'origin':'https://foreign.example'},{'sec-fetch-site':'cross-site'}):
            self.assertEqual(self.client.get('/api/health',headers=headers).status_code,403)

    def test_download_requires_registered_session_owner(self):
        path=Path(report._cache_dir(self.sid))/'test.pdf';path.write_bytes(b'pdf');register_artifact(self.sid,path)
        self.assertEqual(api.download_artifact(self.sid,str(path)).path,path)
        other=api._new_id();api._SESSIONS[other]={}
        try:
            with self.assertRaises(HTTPException):api.download_artifact(other,str(path))
        finally:api._SESSIONS.pop(other)
        with self.assertRaises(HTTPException):api.download_artifact(self.sid,str(path.parent/'unregistered.pdf'))

    def test_merge_containment_and_output_traversal(self):
        for name in ('../escape.pdf','C:\\escape.pdf','sub/file.pdf','sub\\file.pdf'):
            with self.subTest(name=name),self.assertRaises(HTTPException):
                report.merge_pdfs(report.PdfMergeRequest(session_id=self.sid,paths=['dummy'],out_name=name))
        outside=Path(self.tmp.name)/'contains-rasplotreplica.pdf';outside.touch()
        with self.assertRaises(HTTPException):report.merge_pdfs(report.PdfMergeRequest(session_id=self.sid,paths=[str(outside)]))

    def test_artifact_expiry_only_removes_registered_file(self):
        path=Path(report._cache_dir(self.sid))/'expired.pdf';path.write_bytes(b'pdf')
        unrelated=path.parent/'keep.pdf';unrelated.write_bytes(b'pdf')
        ARTIFACTS[str(path.resolve())]=(self.sid,0)
        cleanup_artifacts();self.assertFalse(path.exists());self.assertTrue(unrelated.exists())

    def test_session_ids_unpredictable(self):
        ids={api._new_id() for _ in range(50)}
        self.assertEqual(len(ids),50);self.assertTrue(all(len(s)>=24 for s in ids))

    def test_invalid_style_is_validation_error(self):
        for field,value in [('color','red<script>'),('linetype','BAD')]:
            response=self.client.put(f'/api/db/{self.sid}/profiles',json=[{'profile_id':self.db.profiles[0].profile_id,field:value}])
            self.assertEqual(response.status_code,400)

    def test_export_disk_failure_actionable_not_success(self):
        with patch.object(report,'render_profile_pdf_from_db',side_effect=OSError('disk full')):
            result=self.client.post('/api/report/export',json={'session_id':self.sid})
        self.assertEqual(result.status_code,500);self.assertIn('disk space',result.text)

    def test_export_render_failure_distinct_from_engineering(self):
        with patch.object(report,'render_profile_pdf_from_db',side_effect=RuntimeError('renderer')):
            result=self.client.post('/api/report/export',json={'session_id':self.sid})
        self.assertEqual(result.status_code,500);self.assertIn('software error',result.text)

    def test_nonfinite_rejected_input_has_valid_error_json(self):
        result=self.client.post('/api/report/export',content=json.dumps({'session_id':self.sid,'h_scale':float('inf')}),headers={'Content-Type':'application/json'})
        self.assertEqual(result.status_code,422)
        json.dumps(result.json(),allow_nan=False)

    def test_invalid_frequency_style_cannot_bypass_validation(self):
        result=self.client.put(f'/api/db/{self.sid}/frequencies',json=[{'frequency':'1%','color':'bad'}])
        self.assertEqual(result.status_code,400)

    def test_truncated_structure_profile_inventory_blocks(self):
        contents=BytesIO(MUNCIE.read_bytes())
        with h5py.File(contents,'r+') as f:
            del f['Geometry/Structures/Profile Data']
        data=contents.getvalue();original=h5py.File
        with patch('backend.app.core.hdf_reader.h5py.File',side_effect=lambda *a,**k:original(BytesIO(data),'r')):
            plan=HdfReader(MUNCIE).read_plan()
        self.assertEqual(plan.structure_inventory['state'],'MALFORMED')

    def test_recalculation_never_expands_disjoint_revision(self):
        api.put_revision_limits(self.sid, api.RevisionLimitsUpdate(
            revision_start_station=10, revision_end_station=100))
        result = api.post_recalc_cumdist(self.sid, api.CumdistRequest(start=200))
        self.assertTrue(result['revision_limits']['review_required'])
        self.assertEqual((self.db.header.revision_start_station, self.db.header.revision_end_station), (10, 100))
        self.assertFalse(stationing_dirty(self.db))
        response = self.client.post('/api/report/export', json={'session_id': self.sid})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'STALE_STATE')
        self.assertIn('Revision Limits', response.text)

    def test_panel_and_export_reject_obsolete_engineering_version(self):
        version = self.db.to_dict()['engineering_version']
        before = deepcopy(self.db.panels)
        api.patch_working(self.sid, [api.WorkingPatch(id=1, profiles={self.db.profiles[0].profile_id: 16})])
        response = self.client.put(f'/api/db/{self.sid}/panels', json={
            'panels': [asdict(p) for p in before], 'expected_engineering_version': version})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.db.panels, before)
        response = self.client.post('/api/report/export', json={
            'session_id': self.sid, 'expected_engineering_version': version})
        self.assertEqual(response.status_code, 409)

    def test_export_reports_source_state_and_artifact_identity(self):
        version = self.db.to_dict()['engineering_version']
        response = self.client.post('/api/report/export', json={
            'session_id': self.sid, 'expected_engineering_version': version})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['engineering_version'], version)
        self.assertEqual(response.json()['session_id'], self.sid)
        self.assertEqual(response.json()['artifact_id'], Path(response.json()['path']).stem)

    def test_landmark_elevation_and_inventory_invalidate_panel_review(self):
        from backend.app.core.rasplot_db import Landmark
        self.db.landmarks.append(Landmark(id=1, distance=100, elevation=999, text='Review'))
        self.assertTrue(panels_stale(self.db))
        self.db.landmarks.clear()
        self.assertFalse(panels_stale(self.db))
        self.db.structure_inventory = {'state': 'INCOMPLETE', 'reason': 'Missing inventory'}
        self.assertTrue(panels_stale(self.db))

    def test_scalar_profile_names_are_compatibility_error(self):
        path = Path(self.tmp.name) / 'strings.hdf'
        with h5py.File(path, 'w') as f:
            f['Profile Names'] = b'1%'
            with self.assertRaisesRegex(AlignmentError, 'string row array'):
                HdfReader._string_rows(f['Profile Names'])

    def test_corrupt_file_has_actionable_category(self):
        path = Path(self.tmp.name) / 'Project.revA.p04.hdf'
        path.write_bytes(b'not HDF')
        response = self.client.post('/api/source', json={'path': str(path)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'FILE_ACCESS')
        self.assertIn('uncorrupted', response.text)

    def test_error_categories_and_failed_render_cleanup(self):
        def fail(path, db, **kw):
            Path(path).write_bytes(b'partial')
            raise RuntimeError('private renderer internals')
        with patch.object(report, 'render_profile_pdf_from_db', side_effect=fail):
            response = self.client.post('/api/report/export', json={'session_id': self.sid})
        self.assertEqual(response.json()['code'], 'EXPORT_FAILURE')
        self.assertNotIn('private renderer', response.text)
        self.assertEqual(list(Path(report._cache_dir(self.sid)).glob('*.pdf')), [])
        response = self.client.post('/api/report/export', json={'session_id': self.sid, 'h_scale': 0})
        self.assertEqual(response.json()['code'], 'INVALID_INPUT')
        response = self.client.get('/api/health', headers={'origin': 'https://foreign.example'})
        self.assertEqual(response.json()['code'], 'SECURITY_REJECTION')

    def test_active_download_survives_expiry_cleanup(self):
        from backend.app.core.local_state import ACTIVE_DOWNLOADS
        path = Path(report._cache_dir(self.sid)) / 'active.pdf'; path.write_bytes(b'pdf')
        register_artifact(self.sid, path)
        key = str(path.resolve()); ARTIFACTS[key] = (self.sid, 0)
        ACTIVE_DOWNLOADS[key] = 1
        try:
            cleanup_artifacts(); self.assertTrue(path.exists())
        finally:
            ACTIVE_DOWNLOADS.pop(key, None)
        cleanup_artifacts(); self.assertFalse(path.exists())

    def test_restart_cleanup_only_expires_marked_artifacts(self):
        from backend.app.core.local_state import cleanup_previous_artifacts
        path = Path(report._cache_dir(self.sid)) / 'old.pdf'; path.write_bytes(b'pdf')
        unrelated = path.parent / 'user.pdf'; unrelated.write_bytes(b'user')
        marker = path.with_name(path.name + '.rasplot-artifact.json')
        marker.write_text(json.dumps({'sid': self.sid, 'file': path.name, 'created': 0}))
        cleanup_previous_artifacts()
        self.assertFalse(path.exists()); self.assertFalse(marker.exists()); self.assertTrue(unrelated.exists())

    def test_session_expiry_and_explicit_close(self):
        from backend.app.core.local_state import cleanup_sessions
        old = api._new_id(); api._SESSIONS[old] = {'last_access': 0}
        cleanup_sessions()
        self.assertNotIn(old, api._SESSIONS)
        self.assertIn(self.sid, api._SESSIONS)
        self.assertEqual(self.client.delete(f'/api/session/{self.sid}').status_code, 200)
        self.assertNotIn(self.sid, api._SESSIONS)

    def test_inventory_classification_and_release_scope(self):
        self.assertEqual(self.db.to_dict()['structure_inventory']['classification'], 'VERIFIED_NO_STRUCTURES')
        response = self.client.get('/api/support')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fixture_versions'], {
            'North': '5.0.7',
            'Muncie': '6.4.1',
            'Industrial Park': 'validated Phase 6/7 fixture',
        })
        self.assertNotIn('SI conversion', response.json()['unsupported'])
        self.assertIn('verified US/SI unit contract', response.json()['scope'])

    def test_local_route_preserves_browse_request_through_router_inclusion(self):
        chosen = r'C:\Models\Current.p04.hdf'
        async def local_client_app(scope, receive, send):
            if scope['type'] == 'http':
                scope = {**scope, 'client': ('127.0.0.1', 50000)}
            await app(scope, receive, send)

        with patch('backend.app.core.windows_file_picker.choose_plan_hdf', return_value=chosen):
            with TestClient(local_client_app, base_url='http://127.0.0.1') as local_client:
                response = local_client.post('/api/source/browse', json={})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {'path': chosen})
                # A route without its own Request still receives the wrapper's
                # private dependency through the same include-router path.
                self.assertEqual(local_client.get('/api/health').status_code, 200)

    def test_source_datum_provenance_survives_unchanged_export_label(self):
        self.db.header.vertical_datum = 'NAVD88'
        self.db.header.datum_provenance = 'SOURCE'
        snapshot = deepcopy(self.db)
        report._apply_export_options(snapshot, report.ExportOptions(
            session_id=self.sid, vertical_datum='NAVD88'))
        self.assertEqual(snapshot.header.datum_provenance, 'SOURCE')

    def test_merge_unreadable_owned_pdf_has_input_error(self):
        path = Path(report._cache_dir(self.sid)) / 'bad.pdf'; path.write_bytes(b'not PDF')
        register_artifact(self.sid, path)
        response = self.client.post('/api/report/merge.pdf', json={
            'session_id': self.sid, 'paths': [str(path)]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'INVALID_INPUT')

    def test_artifact_capacity_preserves_existing_file(self):
        from backend.app.core.local_state import require_artifact_capacity
        path = Path(report._cache_dir(self.sid)) / 'retain.pdf'; path.write_bytes(b'pdf')
        register_artifact(self.sid, path)
        with patch('backend.app.core.local_state.ARTIFACT_LIMIT', 1):
            with self.assertRaisesRegex(HTTPException, 'capacity|retained exports'):
                require_artifact_capacity(self.sid)
        self.assertEqual(path.read_bytes(), b'pdf')

    def test_packaging_rejects_changed_source_and_missing_assets(self):
        import runpy
        root = Path(__file__).resolve().parents[2]
        tools = runpy.run_path(str(root / 'packaging/release_manifest.py'))
        fake = Path(self.tmp.name) / 'source'
        frontend = fake / 'frontend'; frontend.mkdir(parents=True)
        for name in ('index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json'):
            (frontend / name).write_text('{}')
        before = Path(self.tmp.name) / 'before.json'
        before.write_text(json.dumps(tools['frontend_identity'](fake)))
        (frontend / 'index.html').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            tools['build_manifest'](fake, fake / 'stage', before)
        before.write_text(json.dumps(tools['frontend_identity'](fake)))
        static = fake / 'stage/app/backend/app/static'; static.mkdir(parents=True)
        (static / 'index.html').write_text('<script src="/assets/missing.js"></script>')
        with self.assertRaisesRegex(ValueError, 'missing asset'):
            tools['build_manifest'](fake, fake / 'stage', before)
