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

import ezdxf
import h5py
import numpy as np
from matplotlib.axes import Axes
from fastapi import HTTPException

from backend.app.core.hdf_reader import CrossSection, ReachGeometry, Structure, ProfileResult, HdfReader
from backend.app.core.rasplot_db import build_db_from_plan, build_working_from_plan, recalc_cumdist
from backend.app.core.structure_model import models_from_rows,preflight,export_faces,HydraulicKind
from backend.app.core.dxf_export import export_dxf
from backend.app.routers.report import render_profile_pdf_from_db,export_profile,ExportOptions
from backend.app.routers.hecras_api import _SESSIONS,get_profile,structure_preflight,patch_working,WorkingPatch


def culvert(rs="201",source_id="S1",gap=5.,length=10.,us="300",ds="200",reach="A"):
    source=Structure("R",reach,rs,"Culvert","Box",0.,2.,us_rs=us,ds_rs=ds,
                     culvert_length=length,culvert_us_distance=gap,invert_us=10.,invert_ds=9.,source_id=source_id)
    source.source_attributes={"Culverts":1,"US Type":"XS","US River":"R","US Reach":reach,"US RS":us,
                              "DS Type":"XS","DS River":"R","DS Reach":reach,"DS RS":ds}
    source.openings=[{"source_id":source_id+"/group-1","Shape Name":"Box","Barrels":1,"Rise":4.,
                      "Length":length,"US Distance":gap,"Invert US":10.,"Invert DS":9.}]
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
        self.assertEqual(outline.provenance,"VALIDATED_SINGLE_BOX_LONGITUDINAL_RULE")
        self.assertIn("Verified culvert opening outline",
                      [entry.label for entry in drawing.presentation.legend_entries])

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
        self.assertEqual(m.status,"UNSUPPORTED");self.assertFalse(m.faces)
        with self.assertRaisesRegex(ValueError,"Multiple openings"):
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
                captured=[];original=Axes.text
                def record(axis,x,y,text,*a,**kw):
                    if str(text).startswith("CULVERT-"): captured.append(float(x))
                    return original(axis,x,y,text,*a,**kw)
                with patch.object(Axes,"text",new=record):
                    render_profile_pdf_from_db(str(self.pdf),db,revision_interval=limits)
                export_dxf(self.dxf,db,revision_interval=limits)
                stations=[(list(e.get_points())[0][0]-1.75)*500 for e in ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="STRUCT_CULVERT"]')]
                self.assertEqual(sorted(captured),expected)
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
        self.assertEqual(preview["structure_models"][0]["status"],"IMPLEMENTED_NEEDS_VALIDATION")
        with self.assertRaises(HTTPException) as caught:
            export_profile(ExportOptions(session_id="structure-test",format="pdf"))
        self.assertEqual(caught.exception.status_code,400)
        self.assertIn("BRIDGE",caught.exception.detail)

    def test_working_patch_cannot_bypass_source_geometry_or_provenance(self):
        plan,db=database([culvert()]);_SESSIONS["structure-test"]={"plan":plan,"db":db}
        row=next(r for r in db.working if r.kind=="struct")
        with self.assertRaises(HTTPException):
            patch_working("structure-test",[WorkingPatch(id=row.id,high_chord=1234.)])

    def test_alternative_shapes_and_multiple_barrels_are_not_certified(self):
        for update in [{"Shape Name":"Circular"},{"Barrels":2}]:
            source=culvert();source.openings[0].update(update)
            _,db=database([source]);m=models_from_rows(db.working)[0]
            self.assertEqual(m.status,"UNSUPPORTED")
            with self.assertRaisesRegex(ValueError,"alternative shape"):
                export_dxf(self.dxf,db)

    def test_text_mismatch_does_not_silently_supply_deck(self):
        plan,text=plan_and_text([culvert()]);text[("R","A","201")][0]["length"]=999.
        with patch("backend.app.core.rasplot_db.linked_text",return_value=text): db=build_db_from_plan(plan)
        m=models_from_rows(db.working)[0]
        self.assertEqual(m.status,"INVALID");self.assertIn("disagree",m.reason)


ROOT=Path(__file__).resolve().parents[2]
NORTH=ROOT/"RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"
MUNCIE=ROOT.parent/"Muncie/Muncie.p04.hdf"


class RealStructureTests(unittest.TestCase):
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
