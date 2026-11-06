"""Phase 4 meaning and missing-data regressions; synthetic cases are not model acceptance."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import h5py
import numpy as np
import ezdxf
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from matplotlib.axes import Axes
from backend.app.core.hydraulics import normalize_value,segments,ground_value,hydraulic_preflight
from backend.app.core.hdf_reader import HdfReader,ProfileResult,CrossSection,ReachGeometry,AlignmentError,plan_to_dict
from backend.app.core.rasplot_db import build_db_from_plan,fix_drawdowns,detect_drawdowns,WorkHeader
from backend.app.core.dxf_export import export_dxf
from backend.app.routers.report import render_profile_pdf_from_db
from backend.app.routers.hecras_api import _SESSIONS,get_profile,put_profiles_meta,ProfileMetaUpdate,patch_working,post_fix_drawdowns,WorkingPatch


def model(names=("P",),values=None):
    xs=[CrossSection("R","A",str(500-i*100),"",100.,0.,1.,minchel=10.) for i in range(5)]
    plan=SimpleNamespace(reaches=[ReachGeometry("R","A",[],xs,[])],profiles=[
        ProfileResult(name,list(values or [15.,14.,13.,12.,11.]),xs_ids=[x.identity for x in xs]) for name in names],
        plan_path=Path("absent.p01.hdf"),source_units="US Customary",source_datum="UNKNOWN")
    return plan,build_db_from_plan(plan)


class HydraulicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda:_SESSIONS.pop("hydraulic-test",None))

    def session(self,names=("P",),values=None):
        plan,db=model(names,values)
        _SESSIONS["hydraulic-test"]={"plan":plan,"db":db,"reach_idx":0}
        return plan,db

    def test_normal_finite_wsel(self):
        self.assertEqual(normalize_value(0.),{"value":0.,"state":"VALID","raw":0.})
        plan,db=model();p=plan.profiles[0];v=db.working[0].hydraulic_values[p.profile_id]
        self.assertEqual(v["xs_identity"],("R","A","500"));self.assertEqual(v["profile_id"],p.profile_id)

    def test_new_project_does_not_generate_cross_section_letters(self):
        _, db = model()
        self.assertTrue(all(row.label_letter is None for row in db.working))

    def test_nan_is_missing_not_zero(self):
        self.assertEqual(normalize_value(float("nan"))["state"],"NONFINITE")
        self.assertIsNone(normalize_value(float("nan"))["value"])

    def test_both_infinities_normalized(self):
        for value in (float("inf"),float("-inf")):
            self.assertIsNone(normalize_value(value)["value"])
            json.dumps(normalize_value(value),allow_nan=False)

    def test_explicit_dry_sentinel_distinct_from_missing(self):
        self.assertEqual(normalize_value(-9999,dry=(-9999,))["state"],"DRY")
        self.assertEqual(normalize_value(-9999,missing=(-9999,))["state"],"MISSING")

    def test_unverified_sentinel_remains_unknown_and_blocks(self):
        _,db=model(values=[15.,14.,-9999.,12.,11.])
        self.assertFalse(hydraulic_preflight(db,db.profiles)["can_export"])
        self.assertIsNone(db.working[2].profiles[db.profiles[0].profile_id])

    def test_missing_value_splits_segments(self):
        self.assertEqual(list(segments([(0,1),(1,2),(2,None),(3,4),(4,5)])),[[(0,1),(1,2)],[(3,4),(4,5)]])

    def test_duplicate_names_have_distinct_identities_and_values(self):
        plan,db=self.session(("Same","Same"))
        ids=[p.profile_id for p in plan.profiles]
        self.assertNotEqual(*ids);self.assertEqual(len(db.working[0].profiles),2)
        response=get_profile("hydraulic-test",profile=None,profiles=json.dumps(ids),reach_idx=0,label_every=5)
        self.assertEqual(response["profiles_selected"],ids)
        with self.assertRaises(HTTPException):get_profile("hydraulic-test",profile="Same",profiles=None,reach_idx=0,label_every=5)

    def test_comma_and_special_characters_are_not_selection_delimiters(self):
        plan,db=self.session(("Flood, future α + %","Other"))
        response=get_profile("hydraulic-test",profile=None,profiles=json.dumps([plan.profiles[0].profile_id]),reach_idx=0,label_every=5)
        self.assertEqual(response["profiles_selected"],[plan.profiles[0].profile_id])

    def test_exact_one_percent_is_unknown_without_mapping(self):self.assert_unknown("1%")
    def test_plus_is_not_one_percent(self):self.assert_unknown("1%+")
    def test_future_is_not_one_percent(self):self.assert_unknown("1% Future")
    def test_thousand_year_is_not_one_percent(self):self.assert_unknown("1000-year")
    def test_maximum_is_not_frequency(self):self.assert_unknown("Maximum WSEL")

    def assert_unknown(self,name):
        _,db=model((name,));self.assertEqual(db.profiles[0].frequency,"")
        self.assertEqual(db.profiles[0].mapping_provenance,"UNKNOWN")

    def test_deliberate_mapping_updates_all_style_metadata(self):
        plan,db=self.session();p=db.profiles[0]
        put_profiles_meta("hydraulic-test",[ProfileMetaUpdate(name=p.name,profile_id=p.profile_id,frequency="1%")])
        self.assertEqual(p.mapping_provenance,"USER_ENTERED");self.assertEqual(p.layer,"PROF_1PCT")
        self.assertIn("1%",p.legend_text)

    def test_mapping_preserves_identity_and_source_values(self):
        plan,db=self.session();p=db.profiles[0];identity=p.profile_id;raw=[dict(r.raw_profiles) for r in db.working]
        put_profiles_meta("hydraulic-test",[ProfileMetaUpdate(name=p.name,profile_id=identity,frequency="0.2%")])
        self.assertEqual(p.profile_id,identity);self.assertEqual(p.source_name,"P")
        self.assertEqual(raw,[r.raw_profiles for r in db.working])

    def test_fix_drawdowns_retains_raw_and_is_deterministic(self):
        plan,db=model(values=[11.,14.,13.,12.,11.]);raw=[dict(r.raw_profiles) for r in db.working]
        fix_drawdowns(db.working);self.assertEqual(db.working[0].profiles[db.profiles[0].profile_id],14.)
        self.assertEqual(db.working[0].adjustments[0]["original_value"],11.)
        fix_drawdowns(db.working);self.assertEqual(len(db.working[0].adjustments),1)
        self.assertEqual(raw,[r.raw_profiles for r in db.working])

    def test_drawdown_detection_is_nonmutating_and_matches_fix_rule(self):
        _, db = model(values=[11.,14.,13.,12.,11.])
        before = [dict(row.profiles) for row in db.working]
        findings = detect_drawdowns(db.working)
        self.assertTrue(findings)
        self.assertEqual(before, [row.profiles for row in db.working])
        affected = {(item["xs_identity"], item["profile_key"]) for item in findings}
        fix_drawdowns(db.working)
        changed = {(row.source_identity, key) for row, prior in zip(db.working, before)
                   for key, value in row.profiles.items() if value != prior.get(key)}
        self.assertEqual(affected, changed)

    def test_profile_response_separates_source_and_adjusted_wsel(self):
        plan, db = self.session(values=[11.,14.,13.,12.,11.])
        fix_drawdowns(db.working)
        profile_id = plan.profiles[0].profile_id
        response = get_profile("hydraulic-test", profile=None, profiles=json.dumps([profile_id]), reach_idx=0, label_every=5)
        first = response["cross_sections"][0]
        self.assertEqual(first["raw_wsel_by_profile"][profile_id], 11.)
        self.assertEqual(first["wsel_by_profile"][profile_id], 14.)
        self.assertEqual(first["adjustment_by_profile"][profile_id]["original_value"], 11.)

    def test_profile_response_marks_uncorrected_drawdown(self):
        plan, _ = self.session(values=[11.,14.,13.,12.,11.])
        profile_id = plan.profiles[0].profile_id
        response = get_profile("hydraulic-test", profile=None, profiles=json.dumps([profile_id]), reach_idx=0, label_every=5)
        findings = [row["drawdown_by_profile"][profile_id] for row in response["cross_sections"]
                    if row["drawdown_by_profile"][profile_id] is not None]
        self.assertTrue(findings)
        self.assertEqual(findings[0]["reason"], "UPSTREAM_BELOW_CARRIED_DOWNSTREAM_WSEL")

    def test_drawdowns_does_not_cross_missing_section(self):
        _,db=model(values=[11.,11.,None,15.,14.]);fix_drawdowns(db.working)
        self.assertEqual(db.working[1].profiles[db.profiles[0].profile_id],11.)

    def test_fix_drawdowns_endpoint_reports_noop_and_preserves_gap(self):
        _,db=self.session(values=[11.,11.,None,15.,14.])
        profile_id=db.profiles[0].profile_id
        raw=[dict(row.raw_profiles) for row in db.working]
        result=post_fix_drawdowns("hydraulic-test")
        self.assertEqual(result, {
            "ok":True, "profiles_evaluated":1, "cross_sections_evaluated":5,
            "wsel_adjustments_made":0, "affected_profiles":[], "no_op":True,
        })
        self.assertIsNone(db.working[2].profiles[profile_id])
        self.assertEqual(raw,[row.raw_profiles for row in db.working])

    def test_fix_drawdowns_endpoint_counts_actual_multi_profile_changes(self):
        _,db=self.session(("P","Q"),values=[11.,14.,13.,12.,11.])
        raw=[dict(row.raw_profiles) for row in db.working]
        result=post_fix_drawdowns("hydraulic-test")
        profile_ids=[profile.profile_id for profile in db.profiles]
        self.assertEqual(result["profiles_evaluated"],2)
        self.assertEqual(result["cross_sections_evaluated"],5)
        self.assertEqual(result["wsel_adjustments_made"],2)
        self.assertEqual(result["affected_profiles"],profile_ids)
        self.assertFalse(result["no_op"])
        self.assertEqual(raw,[row.raw_profiles for row in db.working])
        for profile_id in profile_ids:
            adjustment=next(item for item in db.working[0].adjustments
                            if item["profile_id"] == profile_id)
            self.assertEqual(adjustment["original_value"],11.)
            self.assertEqual(adjustment["adjusted_value"],14.)
            self.assertEqual(adjustment["reason"],"USER_FIX_DRAWDOWNS")
            self.assertEqual(adjustment["provenance"],"USER_ENTERED")

    def test_clearing_backwater_persists(self):
        _,db=self.session();p=db.profiles[0]
        put_profiles_meta("hydraulic-test",[ProfileMetaUpdate(name="P",profile_id=db.profiles[0].profile_id,backwater_elev=20.)])
        put_profiles_meta("hydraulic-test",[ProfileMetaUpdate(name="P",profile_id=db.profiles[0].profile_id,backwater_elev=None)])
        self.assertIsNone(p.backwater_elev);self.assertEqual(p.backwater_provenance,"USER_ENTERED_REFERENCE_ONLY")

    def test_unknown_datum_never_defaults_navd88(self):
        _,db=model();self.assertEqual(WorkHeader().vertical_datum,"UNKNOWN")
        self.assertEqual(db.header.source_datum,"UNKNOWN")
        self.assertIn("UNKNOWN",hydraulic_preflight(db,db.profiles)["warnings"][0])

    def test_unknown_units_are_rejected_before_processing(self):
        path=Path(self.tmp.name)/"unknown-units.hdf"
        with h5py.File(path,"w") as f:
            f.create_group("Geometry");f.attrs["Units System"]="Unsupported Planetary Units"
        with self.assertRaisesRegex(AlignmentError,"Unsupported source unit system"):
            HdfReader(path).read_plan()

    def test_unknown_units_block_export(self):
        _,db=model();db.source_units="UNKNOWN"
        self.assertFalse(hydraulic_preflight(db,db.profiles)["can_export"])

    def test_known_datum_requirement_is_enforced(self):
        _,db=model();db.header.require_known_datum=True
        self.assertFalse(hydraulic_preflight(db,db.profiles)["can_export"])
        db.header.vertical_datum="Verified local datum";db.header.datum_provenance="USER_ENTERED"
        self.assertTrue(hydraulic_preflight(db,db.profiles)["can_export"])
        self.assertEqual(db.header.source_datum,"UNKNOWN")

    def test_adjusted_profile_label_discloses_transformation(self):
        from backend.app.core.hydraulics import profile_label
        _,db=model(values=[11.,14.,13.,12.,11.]);fix_drawdowns(db.working)
        self.assertIn("user-adjusted",profile_label(db.profiles[0],db.working))
        self.assertEqual(db.working[0].hydraulic_values[db.profiles[0].profile_id]["working_provenance"],"USER_ENTERED")

    def test_actual_http_json_for_nonfinite_results(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app.routers.hecras_api import router
        _,db=self.session(values=[15.,float("nan"),float("inf"),float("-inf"),11.])
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.get("/api/profile/hydraulic-test",params={"profile":db.profiles[0].profile_id})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIsNone(response.json()["cross_sections"][1]["wsel"])

    def test_steady_duplicate_names_and_sentinels_keep_source_rows(self):
        path=Path(self.tmp.name)/"steady.hdf";plan,_=model();xs=plan.reaches[0].cross_sections
        group_path="Results/Steady/Output/Output Blocks/Base Output/Steady Profiles"
        with h5py.File(path,"w") as f:
            parent=f.create_group(group_path);parent["Profile Names"]=np.array([b"Flood, A",b"Flood, A"])
            cs=parent.create_group("Cross Sections");cs["Water Surface"]=np.array([[11.,12.,-9999.,14.,15.],[21.,22.,23.,24.,25.]])
            cs["Water Surface"].attrs["dry_value"]=-9999.
            cs["Cross Section Attributes"]=np.array([(x.river.encode(),x.reach.encode(),x.rs.encode()) for x in xs],dtype=[("River","S10"),("Reach","S10"),("Station","S10")])
            profiles=HdfReader(path)._read_steady_profiles(f,plan.reaches,group_path)
        self.assertNotEqual(profiles[0].profile_id,profiles[1].profile_id)
        self.assertEqual(profiles[0].values[2]["state"],"DRY")
        self.assertEqual(profiles[1].wsel[2],23.)
        self.assertTrue(profiles[0].source_dataset.endswith("Water Surface"))

    def test_missing_dataset_reports_source_path(self):
        path=Path(self.tmp.name)/"absent.hdf";plan,_=model()
        with h5py.File(path,"w") as f:
            f.create_group("Results/Cross Sections")
            with self.assertRaisesRegex(AlignmentError,"Water Surface dataset is absent"):
                HdfReader(path)._read_steady_profiles(f,plan.reaches,"Results/Cross Sections")

    def test_invalid_station_elevation_slice_never_silently_truncates(self):
        values=np.array([[0.,10.],[1.,11.]])
        for info in (np.array([[-1,2]]),np.array([[1,5]]),np.array([[0,0]])):
            self.assertEqual(HdfReader._slice_station_elevation(info,values,0),[])

    def test_preview_pdf_dxf_preserve_same_gap(self):
        plan,db=self.session(values=[15.,14.,None,12.,11.]);meta=db.profiles[0]
        response=get_profile("hydraulic-test",profile=db.profiles[0].profile_id,profiles=None,reach_idx=0,label_every=5)
        self.assertIsNone(response["cross_sections"][2]["wsel"])
        captured=[];original=Axes.plot
        def capture(ax,x,y,*args,**kwargs):
            if kwargs.get("label")=="P":captured.append(list(x))
            return original(ax,x,y,*args,**kwargs)
        pdf=Path(self.tmp.name)/"gap.pdf";dxf=Path(self.tmp.name)/"gap.dxf"
        with patch.object(Axes,"plot",capture):render_profile_pdf_from_db(pdf,db)
        self.assertEqual(captured,[[400.,300.],[100.,0.]])
        export_dxf(dxf,db)
        lines=list(ezdxf.readfile(dxf).modelspace().query(f'LWPOLYLINE[layer=="{meta.layer}"]'))
        self.assertEqual(len(lines),2)
        self.assertTrue(all(abs(list(line.get_points())[-1][0]-list(line.get_points())[0][0])<.201 for line in lines))

    def test_nonfinite_api_payload_is_valid_json(self):
        plan,db=self.session(values=[15.,float("nan"),float("inf"),float("-inf"),11.])
        response=get_profile("hydraulic-test",profile=db.profiles[0].profile_id,profiles=None,reach_idx=0,label_every=5)
        json.dumps(jsonable_encoder(response),allow_nan=False);json.dumps(jsonable_encoder(db.to_dict()),allow_nan=False)

    def test_nonfinite_working_edit_is_rejected_without_mutation(self):
        _,db=self.session();before=db.working[0].profiles.copy()
        with self.assertRaises(HTTPException):patch_working("hydraulic-test",[WorkingPatch(id=db.working[0].id,profiles={db.profiles[0].profile_id:float("inf")})])
        self.assertEqual(before,db.working[0].profiles)

    def test_ground_geometry_provenance_and_invalid_cases(self):
        self.assertEqual(ground_value([(0,10),(1,9)],0,1)[0],9)
        for points,lb,rb in [([],0,1),([(0,float("nan"))],0,1),([(0,10)],2,3),([(0,10)],2,1),([(0,10)],None,1)]:
            value,proof=ground_value(points,lb,rb);self.assertIsNone(value);self.assertIn(proof["state"],("MISSING","INVALID"))

    def unsteady(self,timestamps):
        path=Path(self.tmp.name)/"unsteady.hdf";plan,_=model();xs=plan.reaches[0].cross_sections
        parent="Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"
        with h5py.File(path,"w") as f:
            group=f.create_group(parent);group["Time Date Stamp"]=np.array(timestamps,dtype="S30")
            cs=group.create_group("Cross Sections")
            cs["Water Surface"]=np.array([[11.,12.,13.,14.,15.],[15.,14.,13.,12.,11.]])
            cs["Cross Section Attributes"]=np.array([(x.river.encode(),x.reach.encode(),x.rs.encode()) for x in xs],dtype=[("River","S10"),("Reach","S10"),("Station","S10")])
            return HdfReader(path)._read_unsteady_profiles(f,plan.reaches,parent)

    def test_unsteady_parent_timestamp_alignment(self):
        profiles=self.unsteady(["02JAN1900 00:00:00","02JAN1900 00:05:00"])
        self.assertEqual(profiles[1].name,"02JAN1900 00:00:00")
        self.assertEqual(profiles[1].time_provenance["kind"],"SOURCE")
        self.assertEqual(profiles[1].time_provenance["timezone"],"UNKNOWN")

    def test_bad_timestamp_count_and_order_use_explicit_fallback(self):
        for stamps in [["02JAN1900 00:00:00"],["02JAN1900 00:05:00","02JAN1900 00:00:00"]]:
            profiles=self.unsteady(stamps);self.assertIn("source time unavailable",profiles[1].name)
            self.assertEqual(profiles[1].time_provenance["kind"],"STEP_INDEX")

    def test_derived_maximum_is_all_saved_rows_not_simultaneous(self):
        profiles=self.unsteady(["02JAN1900 00:00:00","02JAN1900 00:05:00"])
        self.assertEqual(profiles[0].wsel,[15.,14.,13.,14.,15.])
        self.assertEqual(profiles[0].result_type,"DERIVED_MAXIMUM_ENVELOPE")
        self.assertEqual(profiles[0].time_provenance["rows"],[0,1])

    def test_derived_maximum_frequency_mapping_rejected(self):
        _,db=self.session();db.profiles[0].result_type="DERIVED_MAXIMUM_ENVELOPE"
        with self.assertRaises(HTTPException):put_profiles_meta("hydraulic-test",[ProfileMetaUpdate(name="P",profile_id=db.profiles[0].profile_id,frequency="1%")])


if __name__=="__main__":unittest.main()
