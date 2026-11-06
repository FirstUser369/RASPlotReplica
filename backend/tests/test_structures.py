"""Phase 3 regression tests. Synthetic rule tests are not real-model acceptance."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import math
import json
import os

import ezdxf
import h5py
import numpy as np
from matplotlib.axes import Axes
from fastapi import HTTPException

from backend.app.core.hdf_reader import CrossSection, ReachGeometry, Structure, ProfileResult, HdfReader
from backend.app.core.geometry_text_reader import parse_geometry_text_structures
from backend.app.core.rasplot_db import build_db_from_plan, build_working_from_plan, recalc_cumdist
from backend.app.core.structure_model import models_from_rows,preflight,export_faces,HydraulicKind
from backend.app.core.dxf_export import export_dxf
from backend.app.core.export_geometry import analyze_panel_content, sheet_capacity
from backend.app.core.rasplot_db import PanelDef
from backend.app.routers.report import render_profile_pdf_from_db,export_profile,ExportOptions
from backend.app.routers.hecras_api import _SESSIONS,get_profile,structure_preflight,patch_working,WorkingPatch


def culvert(rs="201",source_id="S1",gap=5.,length=10.,us="300",ds="200",reach="A"):
    source=Structure("R",reach,rs,"Culvert","Box",0.,2.,us_rs=us,ds_rs=ds,
                     culvert_length=length,culvert_us_distance=gap,invert_us=10.,invert_ds=9.,source_id=source_id)
    source.source_attributes={"Culverts":1,"US Type":"XS","US River":"R","US Reach":reach,"US RS":us,
                              "DS Type":"XS","DS River":"R","DS Reach":reach,"DS RS":ds}
    source.openings=[{"source_id":source_id+"/group-1","Shape Name":"Box","Barrels":1,"Rise":4.,
                      "Span":4.,"Length":length,"US Distance":gap,"Invert US":10.,"Invert DS":9.,
                      "association":"synthetic explicit Structure ID"}]
    return source


def bridge(rs="201", source_id="B1", gap=5., width=10., us="300", ds="200"):
    source=Structure("R","A",rs,"Bridge","Reviewed bridge",gap,width,
                     us_rs=us,ds_rs=ds,source_id=source_id)
    source.source_attributes={"US Type":"XS","US River":"R","US Reach":"A","US RS":us,
                              "DS Type":"XS","DS River":"R","DS Reach":"A","DS RS":ds}
    source.source_profiles={
        "US BR Weir Profile":[(0.,20.),(10.,22.)],
        "US BR Lid Profile":[(2.,15.),(8.,16.)],
        "DS BR Weir Profile":[(0.,19.),(10.,21.)],
        "DS BR Lid Profile":[(2.,14.),(8.,15.)],
    }
    return source


def plan_and_text(sources):
    xs=[CrossSection("R","A",rs,"",length,0.,1.,minchel=8.) for rs,length in [("300",30.),("200",20.),("100",999.)]]
    plan=SimpleNamespace(reaches=[ReachGeometry("R","A",[],xs,list(sources))],
                         profiles=[ProfileResult("P",[13.,12.,11.],xs_ids=[x.identity for x in xs])],plan_path=Path("absent.p01.hdf"))
    text={}
    for s in sources:
        text.setdefault((s.river,s.reach,s.rs),[]).append({"source_path":"synthetic.g01","source_line":10,
            "culvert_records":["synthetic single group"],"deck_header":["0","2","0","0","2","2"],
            "rise":4.,"length":s.culvert_length,"invert_us":10.,"invert_ds":9.,
            "us_distance":s.culvert_us_distance,
            "barrel_us_station":50.,"barrel_ds_station":50.,
            "deck_up":[(0.,20.),(100.,20.)],"deck_dn":[(0.,19.),(100.,19.)]})
    return plan,text


def database(sources):
    plan,text=plan_and_text(sources)
    with patch("backend.app.core.rasplot_db.linked_text",return_value=text):
        db=build_db_from_plan(plan)
    return plan,db


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.pdf=Path(self.tmp.name)/"profile.pdf";self.dxf=Path(self.tmp.name)/"profile.dxf"
        self.addCleanup(lambda:_SESSIONS.pop("structure-test",None))

    def test_explicit_connections_win_over_wrong_nearest_rs(self):
        _,db=database([culvert()])
        m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"VALIDATED")
        self.assertEqual([f["station"] for f in m.faces],[45.,35.])
        self.assertEqual(m.upstream,("R","A","300"))
        self.assertEqual(m.downstream,("R","A","200"))
        self.assertEqual(m.occupied_interval,(35.,45.))
        self.assertEqual(m.upstream_gap+m.occupied_distance+m.downstream_residual,30.)
        before=[r.cumdist for r in db.working]
        recalc_cumdist(db.working)
        self.assertEqual(before,[r.cumdist for r in db.working])
        self.assertEqual([r.cumdist for r in db.working if r.kind=="xs"],[50.,20.,0.])

    def test_validated_single_box_exports_closed_longitudinal_outline(self):
        _,db=database([culvert()])
        from backend.app.core.engineering_drawing import (
            build_engineering_drawing, engineering_to_dxf_inches)
        drawing=build_engineering_drawing(db,db.panels,revision=(0.,50.))
        outline=next(line for line in drawing.panels[0].polylines
                     if line.kind=="CULVERT_OUTLINE")
        export_dxf(self.dxf,db)
        document=ezdxf.readfile(self.dxf)
        outlines=list(document.modelspace().query(
            'LWPOLYLINE[layer=="STRUCT_CULVERT_OUTLINE"]'))
        self.assertEqual(len(outlines),1)
        self.assertTrue(outlines[0].closed)
        points=list(outlines[0].get_points("xy"))
        expected=[engineering_to_dxf_inches(point,drawing.panels[0])
                  for point in outline.points]
        for actual,target in zip(points,expected):
            self.assertAlmostEqual(actual[0],target.x,places=8)
            self.assertAlmostEqual(actual[1],target.y,places=8)

        self.assertTrue(outline.closed)
        self.assertEqual(outline.provenance,"VALIDATED_SOURCE_OPENING_LONGITUDINAL_ENVELOPE")
        self.assertIn("Verified culvert opening outline",
                      [entry.label for entry in drawing.presentation.legend_entries])

    def test_source_backed_bridge_chords_are_validated_and_use_bridge_layer(self):
        _,db=database([bridge()]);m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"VALIDATED")
        self.assertEqual(m.occupied_interval,(35.,45.))
        self.assertEqual([(f["face"],f["low_chord"],f["high_chord"]) for f in m.faces],
                         [("UP",15.,22.),("DN",14.,21.)])
        self.assertIn("SOURCE HDF US BR Lid Profile",m.faces[0]["provenance"]["low_chord"])
        export_dxf(self.dxf,db)
        document=ezdxf.readfile(self.dxf)
        self.assertEqual(len(list(document.modelspace().query('LINE[layer=="STRUCT_BRIDGE"]'))),2)
        self.assertEqual(len(list(document.modelspace().query('LWPOLYLINE[layer=="STRUCT_CULVERT_OUTLINE"]'))),0)

    def test_panel_content_analysis_reports_source_backed_overflow_without_mutation(self):
        _,db=database([bridge()])
        panel=PanelDef(7,0.,50.,8.,20.,h_scale=500.,v_scale=5.,panel_suffix="P")
        before=panel.to_dict().copy()
        result=analyze_panel_content(db,[panel],page_size="11x17",revision=(0.,50.))
        item=result["panels"][0]
        self.assertFalse(item["existing_panel_valid"])
        self.assertEqual(item["display_id"],"7P")
        self.assertEqual(item["required_maximum_elevation"],22.)
        self.assertEqual(item["missing_upper_clearance"],2.)
        self.assertEqual((item["suggested_minimum_elevation"],item["suggested_maximum_elevation"]),(5.,25.))
        self.assertFalse(item["requires_scale_or_page_change"])
        self.assertTrue(any(entry["kind"]=="STRUCTURE" and "RS 201" in entry["label"]
                            for entry in item["responsible_content"]))
        self.assertEqual(panel.to_dict(),before)

    def test_panel_fit_flags_physical_scale_tradeoff_instead_of_changing_scale(self):
        _,db=database([bridge()])
        panel=PanelDef(1,0.,50.,10.,19.,h_scale=500.,v_scale=1.)
        item=analyze_panel_content(db,[panel],page_size="11x17",revision=(0.,50.))["panels"][0]
        self.assertTrue(item["requires_scale_or_page_change"])
        self.assertGreater(item["minimum_vertical_scale_for_suggestion"],panel.v_scale)
        self.assertEqual(panel.v_scale,1.)

    def test_export_preflight_reports_every_blocking_structure(self):
        first = culvert("201", "unsupported-1")
        first.type = "Inline Weir"
        first.openings = []
        second = culvert("250", "unsupported-2")
        second.type = "Gated"
        second.openings = []
        _, db = database([first, second])
        with self.assertRaises(ValueError) as caught:
            export_faces(db.working, 0., 50.)
        message = str(caught.exception)
        self.assertEqual(message.count("Structure export blocked:"), 1)
        self.assertIn("RS 201", message)
        self.assertIn("RS 250", message)

    def test_sheet_capacity_uses_actual_page_geometry_and_exact_boundary(self):
        exact = PanelDef(1, 0., 6500., 100., 145., h_scale=500., v_scale=5.)
        eleven = sheet_capacity(exact, "11x17")
        self.assertEqual((eleven["paper_width_inches"], eleven["paper_height_inches"]), (17., 11.))
        self.assertEqual((eleven["usable_plot_width_inches"], eleven["usable_plot_height_inches"]), (13., 9.))
        self.assertTrue(eleven["physically_valid"])
        ansi = sheet_capacity(exact, "ANSI_C")
        self.assertEqual((ansi["paper_width_inches"], ansi["paper_height_inches"]), (22., 17.))
        self.assertEqual((ansi["usable_plot_width_inches"], ansi["usable_plot_height_inches"]), (18., 15.))
        self.assertEqual(ansi["remaining_horizontal_clearance_inches"], 5.)
        self.assertEqual(ansi["remaining_vertical_clearance_inches"], 6.)

    def test_sheet_capacity_reports_horizontal_and_vertical_overflow(self):
        panel = PanelDef(1, 0., 7000., 100., 150., h_scale=500., v_scale=5.)
        capacity = sheet_capacity(panel, "11x17")
        self.assertFalse(capacity["physically_valid"])
        self.assertEqual(capacity["horizontal_overflow_inches"], 1.)
        self.assertEqual(capacity["vertical_overflow_inches"], 1.)

    def test_preliminary_notice_is_part_of_shared_drawing_presentation(self):
        from backend.app.core.engineering_drawing import DrawingBuildConfig, build_engineering_drawing
        _, db = database([culvert()])
        notice = "PRELIMINARY — ENGINEERING REVIEW REQUIRED"
        drawing = build_engineering_drawing(
            db, db.panels, revision=(0., 50.),
            config=DrawingBuildConfig(review_notice=notice))
        self.assertTrue(drawing.presentation.page_title.startswith(notice))
        self.assertIn(notice, drawing.warnings)

    def test_bridge_missing_or_invalid_source_chords_remains_blocked(self):
        for update in [
            {"US BR Lid Profile":[]},
            {"DS BR Weir Profile":[(0.,float("nan"))]},
            {"US BR Lid Profile":[(0.,30.)]},
        ]:
            with self.subTest(update=update):
                source=bridge();source.source_profiles.update(update)
                _,db=database([source]);m=models_from_rows(db.working)[0]
                self.assertEqual(m.status,"INVALID")
                self.assertFalse(m.faces)
                with self.assertRaisesRegex(ValueError,"BRIDGE"):
                    export_dxf(self.dxf,db)

    def test_hdf_barrel_records_must_match_plan_linked_geometry(self):
        source=culvert()
        source.openings[0]["barrel_records"]=[{"US Station":51.,"DS Station":50.}]
        _,db=database([source]);m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"INVALID")
        self.assertIn("barrel stations disagree",m.reason)

    def test_invalid_partitions_are_actionable_export_errors(self):
        for options in [dict(gap=-1.),dict(length=-1.),dict(gap=float("nan")),dict(length=float("inf")),dict(gap=25.)]:
            with self.subTest(options=options):
                _,db=database([culvert(**options)])
                m=models_from_rows(db.working)[0]
                self.assertEqual(m.status,"INVALID")
                self.assertFalse(m.faces)
                self.assertFalse(m.partition_valid)
                for writer,path in [(export_dxf,self.dxf),(render_profile_pdf_from_db,self.pdf)]:
                    with self.assertRaisesRegex(ValueError,"R / A / RS 201 / CULVERT"):
                        writer(str(path),db)
                self.assertEqual([r.cumdist for r in db.working if r.kind=="xs"],[50.,20.,0.])

    def test_nonadjacent_reversed_and_missing_connections_fail(self):
        for us,ds in [("300","100"),("200","300"),("missing","200"),("300","")]:
            _,db=database([culvert(us=us,ds=ds)])
            m=models_from_rows(db.working)[0]
            self.assertEqual(m.status,"INVALID")
            self.assertIn("adjacent",m.reason)
            self.assertFalse(m.faces)

    def test_cross_reach_connection_cannot_match_rs_alone(self):
        source=culvert();source.source_attributes["US Reach"]="B"
        _,db=database([source])
        self.assertEqual(models_from_rows(db.working)[0].status,"INVALID")

    def test_multiple_groups_are_preserved_and_blocked(self):
        source=culvert();source.openings.append({**source.openings[0],"source_id":"S1/group-2"})
        source.source_attributes["Culverts"]=2
        _,db=database([source]);m=models_from_rows(db.working)[0]
        self.assertEqual(m.type,"MULTIPLE_OPENING");self.assertEqual(len(m.openings),2)
        self.assertEqual(m.status,"INVALID");self.assertFalse(m.faces)
        with self.assertRaisesRegex(ValueError,"geometry text/HDF group"):
            export_dxf(self.dxf,db)

    def test_two_structures_in_one_interval_are_distinct_but_not_certified(self):
        _,db=database([culvert("201","S1"),culvert("250","S2",gap=16.,length=5.)])
        models=models_from_rows(db.working)
        self.assertEqual(len(models),2)
        self.assertEqual(len({m.identity for m in models}),2)
        self.assertTrue(all(m.status=="UNSUPPORTED" for m in models))
        with self.assertRaisesRegex(ValueError,"multiple-structure fixture"):
            export_dxf(self.dxf,db)

    def test_duplicate_structure_rs_does_not_collapse_records(self):
        _,db=database([culvert("201","S1"),culvert("201","S2")])
        models=models_from_rows(db.working)
        self.assertEqual(len(models),2)
        self.assertEqual(len({m.identity for m in models}),2)

    def test_duplicate_source_ids_are_invalid_and_retained(self):
        _,db=database([culvert("201","S1"),culvert("250","S1")])
        models=models_from_rows(db.working)
        self.assertEqual(len(models),2)
        self.assertTrue(all(m.status=="INVALID" for m in models))

    def test_duplicate_rs_on_different_reaches_stays_distinct(self):
        from backend.app.core.structure_model import normalize
        a=culvert();b=culvert(reach="B")
        _,db=database([])
        models=normalize([a,b],[r for r in db.working if r.kind=="xs"],["P"])
        self.assertNotEqual(models[0].identity,models[1].identity)
        self.assertEqual(models[1].status,"INVALID")
        self.assertEqual(models[1].upstream,("R","B","300"))

    def test_unsupported_types_have_no_invented_face_geometry(self):
        for kind in ["Bridge","Inline Weir","Weir","Gated","Unknown"]:
            source=culvert();source.type=kind;source.openings=[]
            _,db=database([source]);m=models_from_rows(db.working)[0]
            self.assertNotEqual(m.status,"VALIDATED");self.assertFalse(m.faces)
            self.assertIsNone(m.occupied_interval)
            self.assertTrue(all(r.cumdist is None for r in db.working if r.kind=="struct"))
            with self.assertRaisesRegex(ValueError,"Structure export blocked"):
                export_dxf(self.dxf,db)

    def test_gates_are_detected_from_source_counts_not_generic_mode(self):
        source=culvert();source.source_attributes["Gate Groups"]=1
        _,db=database([source]);self.assertEqual(models_from_rows(db.working)[0].type,"GATED")
        source.source_attributes["Gate Groups"]=0
        source.source_attributes["Mode"]="Weir/Gate/Culverts"
        _,db=database([source]);self.assertEqual(models_from_rows(db.working)[0].type,"CULVERT")

    def test_unsupported_outside_proven_interval_does_not_block(self):
        source=culvert();source.type="Bridge";source.openings=[]
        _,db=database([source])
        # Bridge's explicit adjacent bounding XS prove it is within 20..50.
        records=preflight(db.working,0.,15.)
        self.assertFalse(records[0]["affects_range"])
        export_dxf(self.dxf,db,revision_interval=(0.,15.))
        render_profile_pdf_from_db(str(self.pdf),db,revision_interval=(0.,15.))
        with self.assertRaisesRegex(ValueError,"BRIDGE"):
            export_dxf(self.dxf,db,revision_interval=(25.,40.))

    def test_unknown_extent_cannot_be_assumed_outside_revision(self):
        source=culvert();source.type="Bridge";source.us_rs="";source.ds_rs="";source.openings=[]
        _,db=database([source])
        self.assertIsNone(models_from_rows(db.working)[0].bounds)
        with self.assertRaisesRegex(ValueError,"BRIDGE"):
            export_dxf(self.dxf,db,revision_interval=(0.,10.))

    def test_revision_face_inclusion_consistent_in_serialized_dxf_and_pdf(self):
        _,db=database([culvert()])
        for limits,expected in [((0.,34.),[]),((46.,50.),[]),((0.,40.),[35.]),((40.,50.),[45.]),
                                ((36.,44.),[]),((30.,46.),[35.,45.]),((35.,45.),[35.,45.])]:
            with self.subTest(limits=limits):
                selected=export_faces(db.working,*limits)
                self.assertEqual(sorted(f.cumdist for f in selected),expected)
                captured=[];leader_sources=[];original=Axes.text;original_plot=Axes.plot
                def record(axis,x,y,text,*a,**kw):
                    if str(text).startswith("CULVERT"): captured.append(float(x))
                    return original(axis,x,y,text,*a,**kw)
                def record_plot(axis,x,y,*a,**kw):
                    if kw.get("color") == "#555555" and len(x) == 2:
                        leader_sources.append(float(x[0]))
                    return original_plot(axis,x,y,*a,**kw)
                with patch.object(Axes,"text",new=record), patch.object(Axes,"plot",new=record_plot):
                    render_profile_pdf_from_db(str(self.pdf),db,revision_interval=limits)
                export_dxf(self.dxf,db,revision_interval=limits)
                stations=[(list(e.get_points())[0][0]-1.75)*500 for e in ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="STRUCT_CULVERT"]')]
                # Face geometry remains one entity per authoritative face. A
                # structure/opening receives one label, centered between the
                # faces that are actually included in this revision.
                expected_labels = [] if not expected else [sum(expected) / len(expected)]
                self.assertEqual(len(captured), len(expected_labels))
                self.assertEqual(sorted(leader_sources), expected_labels)
                self.assertEqual(len(stations),len(expected))
                for actual,target in zip(sorted(stations),expected):self.assertAlmostEqual(actual,target,places=7)

    def test_missing_hydraulics_are_not_copied_xs_results(self):
        _,db=database([culvert()]);m=models_from_rows(db.working)[0]
        self.assertEqual({v.value for v in HydraulicKind},{"EXTRACTED","INTERPOLATED","COPIED_XS","DISPLAY_ONLY","MISSING"})
        self.assertTrue(all(r.profiles[db.profiles[0].profile_id] is None for r in db.working if r.kind=="struct"))
        self.assertEqual(m.hydraulic_values["UP"][db.profiles[0].profile_id]["kind"],"MISSING")
        self.assertEqual([r.profiles[db.profiles[0].profile_id] for r in db.working if r.kind=="xs"],[13.,12.,11.])
        self.assertIn("INTERPOLATED",m.faces[0]["provenance"]["high_chord"])

    def test_edited_interval_uses_same_placement_and_preserves_source(self):
        _,db=database([culvert()]);xs=[r for r in db.working if r.kind=="xs"]
        xs[0].length_chnl=40.
        self.assertEqual(preflight(db.working,0.,100.)[0]["status"],"INVALID")
        recalc_cumdist(db.working)
        m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"VALIDATED")
        self.assertEqual([f["station"] for f in m.faces],[55.,45.])
        self.assertEqual(m.downstream_residual,25.)
        self.assertEqual(xs[0].source_length_chnl,30.)

    def test_structure_preflight_and_preview_identify_support(self):
        source=culvert();source.type="Bridge";source.openings=[]
        plan,db=database([source]);_SESSIONS["structure-test"]={"plan":plan,"db":db,"reach_idx":0}
        report=structure_preflight("structure-test")
        self.assertFalse(report["can_export"])
        self.assertIn("R / A / RS 201 / BRIDGE",report["structures"][0]["message"])
        preview=get_profile("structure-test",profile=db.profiles[0].profile_id,profiles=None,reach_idx=0,label_every=5)
        self.assertIsNone(preview["structures"][0]["cumdist"])
        self.assertEqual(preview["structure_models"][0]["status"],"INVALID")
        self.assertIn("source-backed bridge chords",preview["structure_models"][0]["reason"])
        with self.assertRaises(HTTPException) as caught:
            export_profile(ExportOptions(session_id="structure-test",format="pdf"))
        self.assertEqual(caught.exception.status_code,400)
        self.assertIn("BRIDGE",caught.exception.detail)

    def test_working_patch_cannot_bypass_source_geometry_or_provenance(self):
        plan,db=database([culvert()]);_SESSIONS["structure-test"]={"plan":plan,"db":db}
        row=next(r for r in db.working if r.kind=="struct")
        with self.assertRaises(HTTPException):
            patch_working("structure-test",[WorkingPatch(id=row.id,high_chord=1234.)])

    def test_structure_name_reports_source_and_reviewer_provenance(self):
        plan, db = database([bridge()])
        _SESSIONS["structure-test"] = {"plan": plan, "db": db, "reach_idx": 0}
        preview = get_profile(
            "structure-test", profile=db.profiles[0].profile_id,
            profiles=None, reach_idx=0, label_every=5)
        structure = preview["structures"][0]
        self.assertEqual(structure["source_name"], "Reviewed bridge")
        self.assertEqual(structure["name"], "Reviewed bridge")
        self.assertEqual(structure["name_provenance"], "SOURCE")
        self.assertEqual([face["face"] for face in structure["source_faces"]], ["UP", "DN"])
        self.assertEqual([(face["low_chord"], face["high_chord"])
                          for face in structure["source_faces"]], [(15., 22.), (14., 21.)])
        row = next(item for item in db.working if item.kind == "struct")
        patch_working("structure-test", [WorkingPatch(
            id=row.id, xs_id=row.row_identity, label_text="Reviewer crossing name")])
        self.assertTrue(all(
            item.label_text == "Reviewer crossing name"
            for item in db.working if item.kind == "struct"))
        updated = get_profile(
            "structure-test", profile=db.profiles[0].profile_id,
            profiles=None, reach_idx=0, label_every=5)["structures"][0]
        self.assertEqual(updated["source_name"], "Reviewed bridge")
        self.assertEqual(updated["reviewer_name"], "Reviewer crossing name")
        self.assertEqual(updated["name"], "Reviewer crossing name")
        self.assertEqual(updated["name_provenance"], "USER_ENTERED")

    def test_unvalidated_shapes_and_unmatched_barrel_groups_are_not_certified(self):
        for update,reason in [({"Shape Name":"Arch"},"shape Arch"),({"Barrels":2},"geometry text/HDF group")]:
            source=culvert();source.openings[0].update(update)
            _,db=database([source]);m=models_from_rows(db.working)[0]
            self.assertEqual(m.status,"INVALID")
            self.assertIn(reason,m.reason)
            with self.assertRaisesRegex(ValueError,reason):
                export_dxf(self.dxf,db)

    def test_text_mismatch_does_not_silently_supply_deck(self):
        plan,text=plan_and_text([culvert()]);text[("R","A","201")][0]["length"]=999.
        with patch("backend.app.core.rasplot_db.linked_text",return_value=text): db=build_db_from_plan(plan)
        m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"INVALID");self.assertIn("disagree",m.reason)

    def test_geometry_text_preserves_real_multiple_barrel_and_opening_records(self):
        geometry = """River Reach=South_Soln      ,Reach 3C
Type RM Length L Ch R = 4 ,3951    ,,,
Bridge Culvert--1,0,-1,-1, 0
Deck Dist Width WeirC Skew NumUp NumDn MinLoCord MaxHiCord MaxSubmerge Is_Ogee
3,72,2.6,0, 6, 6, , , 0.98, 0, 0,0,,
       0  26.784  70.785 141.786 205.806   267.1
 5604.965604.9695605.156  5605.55606.156    5607
    5600    5600    5600    5600    5600    5600
       0  26.784  70.785 141.786 205.806   267.1
 5604.965604.9695605.156  5605.55606.156    5607
    5599    5599    5599    5599    5599    5599
Culvert=1,2,,72,0.024,0.9,1,2,3,5601.43,52.1,5600.8,22.2,1 - 24\" CMP , 0 ,3
    52.1    22.2
Multiple Barrel Culv=4,3,4,72,0.024,0.9,1,29,3,5600.98,5600.03, 4,4-3'x4' CMP , 0 ,3
   140.6   142.3   145.6   147.3   150.6   152.3   155.6   157.3
"""
        path=Path(self.tmp.name)/"industrial.g02";path.write_text(geometry)
        parsed=parse_geometry_text_structures(path)[0]
        self.assertEqual(len(parsed.culvert_groups),2)
        self.assertEqual([(group["shape_code"],group["barrels"],group["rise"],group["length"])
                          for group in parsed.culvert_groups],[(1.,1,2.,72.),(4.,4,3.,72.)])
        self.assertEqual(parsed.culvert_groups[1]["barrel_stations"][-1],(155.6,157.3))
        self.assertEqual(parsed.deck_dn[-1], (267.1, 5607.0))
        self.assertEqual(parsed.low_chord_up[0], (0.0, 5600.0))
        self.assertEqual(parsed.low_chord_dn[-1], (267.1, 5599.0))


ROOT=Path(__file__).resolve().parents[2]
NORTH=ROOT/"RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"
MUNCIE=ROOT.parent/"Muncie/Muncie.p04.hdf"
CHERRY=Path(os.environ.get("RAS_PLOT_CHERRY_HDF", "__missing_cherry_fixture__"))


class RealStructureTests(unittest.TestCase):
    @unittest.skipUnless(CHERRY.is_file(),"Authoritative Cherry Creek fixture unavailable")
    def test_cherry_creek_source_ids_bridges_and_three_barrel_culvert(self):
        plan=HdfReader(CHERRY).read_plan();db=build_db_from_plan(plan)
        self.assertEqual(plan.capabilities.schema_adapter,
                         "STEADY_BASE_OUTPUT_GEOMETRY_INFO_LABELS_V1")
        models=models_from_rows(db.working)
        self.assertEqual(plan.structure_inventory,{"state":"VERIFIED_PARSED"})
        self.assertEqual(len(models),61)
        self.assertTrue(all(model.status=="VALIDATED" for model in models))
        bridges=[model for model in models if model.type=="BRIDGE"]
        self.assertEqual(len(bridges),60)
        self.assertTrue(all(len(model.faces)==2 for model in bridges))
        self.assertTrue(all(model.capability_id=="BRIDGE_CHORD_ENVELOPE_V1" for model in bridges))
        culvert=next(model for model in models if model.rs=="35445")
        self.assertEqual(culvert.type,"CULVERT")
        self.assertEqual(len(culvert.openings),1)
        self.assertEqual(culvert.openings[0]["association"],
                         "explicit Structure ID -> Structures/Attributes row")
        self.assertEqual(len(culvert.openings[0]["barrel_records"]),3)
        self.assertEqual((culvert.openings[0]["Rise"],culvert.openings[0]["Span"],
                          culvert.openings[0]["Length"]),(4.,14.,10.))
        self.assertEqual(len(culvert.faces),2)
        self.assertEqual(culvert.capability_id,"CULVERT_GROUP_ENVELOPES_V1")

    @unittest.skipUnless(NORTH.exists() and MUNCIE.exists(),"Real fixtures unavailable")
    def test_real_structure_api_payloads_are_json_serializable(self):
        from fastapi.encoders import jsonable_encoder
        for path in (NORTH,MUNCIE):
            with self.subTest(path=path.name):
                plan=HdfReader(path).read_plan();db=build_db_from_plan(plan)
                _SESSIONS["structure-json"]={"plan":plan,"db":db,"reach_idx":0}
                try:
                    preview=get_profile("structure-json",profile=plan.profiles[0].profile_id,profiles=None,reach_idx=0,label_every=5)
                    for payload in (db.to_dict(),preview,structure_preflight("structure-json")):
                        encoded=json.dumps(jsonable_encoder(payload),allow_nan=False)
                        self.assertIn("structure",encoded)
                finally:
                    _SESSIONS.pop("structure-json",None)

    @unittest.skipUnless(NORTH.exists(),"North fixture unavailable")
    def test_north_source_provenance_and_faces(self):
        plan=HdfReader(NORTH).read_plan();db=build_db_from_plan(plan);m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"VALIDATED")
        self.assertEqual(m.capability_id,"CULVERT_GROUP_ENVELOPES_V1")
        self.assertEqual(len(m.openings),1)
        self.assertEqual(m.faces[0]["station"]-m.faces[1]["station"],110.)
        self.assertEqual(m.receiving_connection["DS RS"],"0.928")
        self.assertIn(".g04",m.faces[0]["provenance"]["text_path"])
        self.assertEqual(m.hydraulic_values["UP"][plan.profiles[0].profile_id]["kind"],"MISSING")

    @unittest.skipUnless(MUNCIE.exists(),"Muncie fixture unavailable")
    def test_muncie_lateral_source_retained_without_invented_extent(self):
        db=build_db_from_plan(HdfReader(MUNCIE).read_plan());models=models_from_rows(db.working)
        self.assertEqual(len(models),2)
        xs=[r for r in db.working if r.kind=="xs"]
        self.assertAlmostEqual(max(r.cumdist for r in xs),15458.599994659424,places=8)
        by_id={(r.river,r.reach,r.rs):r for r in xs}
        for m in models:
            self.assertEqual(m.capability_id,"LATERAL_ANCHOR_ONLY_V1")
            self.assertEqual(m.capability_level,"SOURCE_GEOMETRY_PREVIEW")
            self.assertEqual(m.status,"IMPLEMENTED_NEEDS_VALIDATION")
            self.assertEqual(m.anchor,by_id[m.upstream].cumdist)
            self.assertIsNone(m.occupied_interval)
            self.assertFalse(m.faces)
            self.assertEqual(m.receiving_connection["DS SA/2D"],"2D Interior Area")
            self.assertTrue(m.source["source_connections"])
            self.assertTrue(m.source["source_profiles"]["Centerline Profile"])
        lateral=[r for r in db.working if r.kind=="struct"]
        self.assertTrue(all(r.length_chnl==0 for r in lateral))
        before=[r.cumdist for r in db.working];recalc_cumdist(db.working)
        self.assertEqual(before,[r.cumdist for r in db.working])

    @unittest.skipUnless(NORTH.exists(),"North fixture unavailable")
    def test_hdf_multiple_groups_are_all_read(self):
        # Copy only to a temporary fixture; original model remains read-only.
        with TemporaryDirectory() as folder:
            import shutil
            path=Path(folder)/"groups.p01.hdf";shutil.copyfile(NORTH,path)
            with h5py.File(path,"a") as f:
                key="Geometry/Structures/Culvert Groups/Attributes"
                original=f[key][()];both=np.concatenate([original,original])
                both[1]["Name"]=b"Second group"
                del f[key];f[key]=both
            plan=HdfReader(path).read_plan();source=plan.reaches[0].structures[0]
            self.assertEqual(len(source.openings),2)
            self.assertIsNone(source.culvert_length)
            db=build_db_from_plan(plan);m=models_from_rows(db.working)[0]
            self.assertEqual(m.type,"MULTIPLE_OPENING")
            self.assertEqual(len(m.openings),2)
            self.assertNotEqual(m.openings[0]["source_id"],m.openings[1]["source_id"])


if __name__=="__main__": unittest.main()
