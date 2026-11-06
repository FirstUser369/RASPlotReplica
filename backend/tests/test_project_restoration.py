import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from backend.app.core.hdf_reader import CrossSection, Plan, ProfileResult, ReachGeometry, Structure
from backend.app.core.model_topology import ModelContext, build_topology
from backend.app.core.project_file import LEGACY_REVIEW_CONTRACT_VERSION
from backend.app.core.project_restore import (
    ProjectRestoreError, build_project_document, restore_candidate_session,
    semantic_source_identity,
)
from backend.app.core.rasplot_db import PanelDef, build_db_from_plan
from backend.app.core.state_contract import (
    mark_panels_review_required, panels_stale, review_panels, stationing_dirty,
)
from backend.app.routers import hecras_api
from fastapi.testclient import TestClient


def sample_plan(path: Path) -> Plan:
    xs = [
        CrossSection("River", "Reach", "200", "200", 100.0, 0, 10,
                     station_elevation=[(0, 10), (10, 12)], minchel=10, index=0),
        CrossSection("River", "Reach", "100", "100", 0.0, 0, 10,
                     station_elevation=[(0, 9), (10, 11)], minchel=9, index=1),
    ]
    profile = ProfileResult("Profile, 1%+", [20.0, 19.0], xs_ids=[x.identity for x in xs],
        profile_id="profile-one", key="profile-one", source_path="/Results",
        source_dataset="/Results/WSEL", values=[{"value": 20.0}, {"value": 19.0}])
    reach = ReachGeometry("River", "Reach", [], xs, [], source_path=str(path))
    plan = Plan("Test Plan", path, None, None, None, path, profiles=[profile], reaches=[reach],
                source_units="INTERNAL_US_CUSTOMARY", model_id="model-test", ras_version="6.4.1")
    plan.topology = build_topology(plan.model_id, plan.reaches, [], str(path))
    return plan


def sample_session(path: Path, sid: str):
    plan = sample_plan(path)
    db = build_db_from_plan(plan)
    return {"plan": plan, "engineering_plan": plan, "db": db, "reach_idx": 0,
            "path": str(path), "model_context": ModelContext(plan.model_id, 1, sid, str(path), "CANDIDATE")}


def structure_session(path: Path, sid: str):
    plan = sample_plan(path)
    structure = Structure(
        "River", "Reach", "150", "Bridge", "Source roadway", 5.0, 10.0,
        us_rs="200", ds_rs="100", source_id="bridge-1")
    structure.source_attributes = {
        "US Type": "XS", "US River": "River", "US Reach": "Reach", "US RS": "200",
        "DS Type": "XS", "DS River": "River", "DS Reach": "Reach", "DS RS": "100",
    }
    structure.source_profiles = {
        "US BR Weir Profile": [(0.0, 20.0), (10.0, 22.0)],
        "US BR Lid Profile": [(2.0, 15.0), (8.0, 16.0)],
        "DS BR Weir Profile": [(0.0, 19.0), (10.0, 21.0)],
        "DS BR Lid Profile": [(2.0, 14.0), (8.0, 15.0)],
    }
    plan.reaches[0].structures = [structure]
    db = build_db_from_plan(plan)
    return {"plan": plan, "engineering_plan": plan, "db": db, "reach_idx": 0,
            "path": str(path), "model_context": ModelContext(plan.model_id, 1, sid, str(path), "CANDIDATE")}


def multi_reach_plan(path: Path) -> Plan:
    def make_reach(name, stations, us, ds):
        records = [CrossSection("River", name, rs, rs, length, 0, 10,
                                station_elevation=[(0, 10), (10, 12)], minchel=10, index=index)
                   for index, (rs, length) in enumerate(stations)]
        return ReachGeometry("River", name, [], records, [], source_path=str(path),
            source_connectivity={"US Type": us[0], "US Name": us[1], "Junction to US XS": us[2],
                                 "DS Type": ds[0], "DS Name": ds[1], "DS XS to Junction": ds[2]})
    down = make_reach("Down", [("200", 100), ("100", 0)], ("JUNCTION", "J", 4), ("EXTERNAL", "", None))
    up = make_reach("Up", [("400", 80), ("300", 0)], ("EXTERNAL", "", None), ("JUNCTION", "J", 6))
    identities = [xs.identity for reach in (down, up) for xs in reach.cross_sections]
    values = [24.0, 23.0, 22.0, 21.0]
    profile = ProfileResult("P", values, xs_ids=identities, profile_id="p", key="p",
        source_path="/Results", source_dataset="/Results/WSEL",
        values=[{"value": value} for value in values])
    plan = Plan("Multi", path, None, None, None, path, profiles=[profile], reaches=[down, up],
                source_units="INTERNAL_US_CUSTOMARY", model_id="multi", ras_version="6.4.1")
    plan.topology = build_topology(plan.model_id, plan.reaches, [{"Name": "J"}], str(path))
    return plan


class ProjectRestorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hdf = self.root / "Model.p01.hdf"
        self.hdf.write_bytes(b"stable synthetic HDF identity")
        self.project = self.root / "Drawing.rasplot.json"
        hecras_api._SESSIONS.clear()

    def tearDown(self):
        hecras_api._SESSIONS.clear()
        self.temp.cleanup()

    def reviewed_document(self):
        session = sample_session(self.hdf, "source")
        db = session["db"]
        db.working[0].length_chnl = 125.0
        hecras_api._SESSIONS["source"] = session
        hecras_api.post_recalc_cumdist("source", hecras_api.CumdistRequest(start=12.0))
        db.working[0].label_text = "Reviewed XS"
        db.profiles[0].display_name = "Mapped profile"
        db.panels = [PanelDef(2, 12, 137, 5, 30, panel_x=1, panel_y=2,
                              h_scale=500, v_scale=5, panel_suffix="P")]
        review_panels(db)
        return build_project_document(session, self.project)

    def test_semantic_identity_is_deterministic_and_source_backed(self):
        plan = sample_plan(self.hdf)
        first = semantic_source_identity(plan)
        second = semantic_source_identity(deepcopy(plan))
        self.assertEqual(first, second)
        self.assertEqual(first.reaches[0].ordered_xs_ids,
                         plan.topology.reaches[next(iter(plan.topology.reaches))].cross_section_ids)
        self.assertEqual(first.profiles[0].source_dataset, "/Results/WSEL")

    def test_save_restore_round_trip_preserves_panels_suffix_and_overrides(self):
        document = self.reviewed_document()
        target = sample_session(self.hdf, "target")
        hecras_api._SESSIONS["target"] = target
        warnings = restore_candidate_session("target", document)
        db = target["db"]
        self.assertEqual(warnings, [])
        self.assertEqual(db.working[0].length_chnl, 125.0)
        self.assertEqual(db.working[0].label_text, "Reviewed XS")
        self.assertEqual(db.stationing_start, 12.0)
        self.assertFalse(stationing_dirty(db))
        self.assertEqual([(p.panel, p.panel_suffix, p.panel_x, p.panel_y) for p in db.panels],
                         [(2, "P", 1.0, 2.0)])
        self.assertFalse(panels_stale(db))
        self.assertEqual(db.profiles[0].display_name, "Mapped profile")

    def test_structure_reviewer_name_round_trip_keeps_source_geometry_authoritative(self):
        source = structure_session(self.hdf, "source")
        hecras_api._SESSIONS["source"] = source
        row = next(item for item in source["db"].working if item.kind == "struct")
        hecras_api.patch_working("source", [hecras_api.WorkingPatch(
            id=row.id, xs_id=row.row_identity, label_text="Reviewer crossing")])
        document = build_project_document(source, self.project)

        target = structure_session(self.hdf, "target")
        hecras_api._SESSIONS["target"] = target
        restore_candidate_session("target", document)
        restored = [item for item in target["db"].working if item.kind == "struct"]
        self.assertTrue(restored)
        self.assertTrue(all(item.label_text == "Reviewer crossing" for item in restored))
        self.assertTrue(all(item.structure_model.source["name"] == "Source roadway"
                            for item in restored))
        self.assertTrue(all(item.structure_model.status == "VALIDATED" for item in restored))

    def test_datum_choices_and_custom_label_preserve_user_provenance_on_reopen(self):
        for index, (entered, expected, provenance) in enumerate((
                ("NAVD88", "NAVD88", "USER_ENTERED"),
                ("NGVD29", "NGVD29", "USER_ENTERED"),
                ("  City of Denver Datum  ", "City of Denver Datum", "USER_ENTERED"),
                ("", "UNKNOWN", "UNKNOWN"))):
            with self.subTest(datum=entered or "UNKNOWN"):
                source_id = f"source-{index}"
                target_id = f"target-{index}"
                source = sample_session(self.hdf, source_id)
                hecras_api._SESSIONS[source_id] = source
                hecras_api.put_header(source_id, hecras_api.HeaderUpdate(
                    vertical_datum=entered, require_known_datum=bool(entered)))
                document = build_project_document(source, self.project)

                target = sample_session(self.hdf, target_id)
                hecras_api._SESSIONS[target_id] = target
                restore_candidate_session(target_id, document)
                header = target["db"].header
                self.assertEqual(header.vertical_datum, expected)
                self.assertEqual(header.datum_provenance, provenance)
                self.assertEqual(header.require_known_datum, bool(entered))

    def test_legacy_and_forged_review_claims_restore_review_required(self):
        document = self.reviewed_document()
        raw = document.model_dump(mode="json")
        evidence = raw["panel_set"]["review_evidence"]
        evidence.update(contract_version=LEGACY_REVIEW_CONTRACT_VERSION,
                        panel_inputs_sha256="a" * 64, engineering_version="legacy")
        evidence["dependency_sha256"] = None
        evidence["ordered_panel_definition_sha256"] = None
        legacy = type(document).model_validate(raw)
        target = sample_session(self.hdf, "target")
        hecras_api._SESSIONS["target"] = target
        warnings = restore_candidate_session("target", legacy)
        self.assertTrue(warnings)
        self.assertTrue(panels_stale(target["db"]))

        raw = document.model_dump(mode="json")
        raw["source"]["semantic_identity"] = None
        legacy_source = type(document).model_validate(raw)
        target = sample_session(self.hdf, "legacy-target")
        hecras_api._SESSIONS["legacy-target"] = target
        warnings = restore_candidate_session("legacy-target", legacy_source)
        self.assertTrue(any("semantic manifest" in warning for warning in warnings))
        self.assertTrue(panels_stale(target["db"]))

    def test_dirty_stationing_remains_dirty_and_review_required(self):
        document = self.reviewed_document()
        raw = document.model_dump(mode="json")
        raw["engineering_context"]["stationing_state"] = "DIRTY"
        raw["panel_set"]["review_state"] = "REVIEW_REQUIRED"
        raw["panel_set"]["review_evidence"] = None
        for panel in raw["panel_set"]["panels"]:
            panel["review_state"] = "REVIEW_REQUIRED"
        dirty = type(document).model_validate(raw)
        target = sample_session(self.hdf, "target")
        hecras_api._SESSIONS["target"] = target
        restore_candidate_session("target", dirty)
        self.assertTrue(stationing_dirty(target["db"]))
        self.assertTrue(panels_stale(target["db"]))

    def test_semantic_mismatch_rolls_back_before_state_application(self):
        document = self.reviewed_document()
        target = sample_session(self.hdf, "target")
        target["plan"].profiles[0].source_dataset = "/Different"
        before = deepcopy(target["db"])
        hecras_api._SESSIONS["target"] = target
        with self.assertRaisesRegex(ProjectRestoreError, "semantically incompatible"):
            restore_candidate_session("target", document)
        self.assertEqual(target["db"], before)

    def test_explicit_review_required_is_enforced(self):
        session = sample_session(self.hdf, "target")
        db = session["db"]
        self.assertTrue(db.panels)
        mark_panels_review_required(db)
        self.assertTrue(panels_stale(db))

        from backend.app.main import app
        session["model_context"].status = "ACTIVE"
        hecras_api._SESSIONS["target"] = session
        client = TestClient(app)
        for endpoint, body in (
            ("/api/report/export", {"session_id": "target", "format": "pdf"}),
            ("/api/report/export", {"session_id": "target", "format": "dxf"}),
            ("/api/report/cad-review-package", {"session_id": "target", "format": "cad-review-package",
                                                 "panel_numbers": [db.panels[0].panel]}),
        ):
            response = client.post(endpoint, json=body)
            self.assertIn(response.status_code, (400, 409), response.text)
            self.assertIn("review", response.text.lower())

    def test_multi_reach_context_and_continuous_stationing_round_trip(self):
        plan = multi_reach_plan(self.hdf)
        sid = "multi-source"
        hecras_api._SESSIONS[sid] = {"plan": plan, "reach_idx": None, "path": str(self.hdf),
            "model_context": ModelContext(plan.model_id, 1, sid, str(self.hdf), "ACTIVE")}
        ids = [next(r.id for r in plan.topology.reaches.values() if r.reach_name == name)
               for name in ("Down", "Up")]
        hecras_api.commit_flooding_source(sid, hecras_api.FloodingSourceRequest(
            display_name="Combined", reach_ids=ids, naming_provenance="USER_DEFINED"))
        db = hecras_api._SESSIONS[sid]["db"]
        edited = next(row for row in db.working if row.kind == "xs" and row.reach == "Down" and row.length_chnl > 0)
        edited.length_chnl += 20
        hecras_api.post_recalc_cumdist(sid, hecras_api.CumdistRequest(start=5))
        review_panels(db)
        expected = {row.source_identity: row.cumdist for row in db.working if row.kind == "xs"}
        document = build_project_document(hecras_api._SESSIONS[sid], self.project)
        saved_source = document.engineering_context.flooding_source
        self.assertEqual(saved_source.direction, "DOWNSTREAM_TO_UPSTREAM")
        self.assertEqual(saved_source.topology_provenance["ordering"], "USER_CONFIRMED")
        self.assertEqual(len(saved_source.ordered_connection_ids), 1)

        restored_plan = multi_reach_plan(self.hdf)
        target = {"plan": restored_plan, "reach_idx": None, "path": str(self.hdf),
            "model_context": ModelContext(restored_plan.model_id, 2, "multi-target", str(self.hdf), "CANDIDATE")}
        hecras_api._SESSIONS["multi-target"] = target
        restore_candidate_session("multi-target", document)
        actual = {row.source_identity: row.cumdist for row in target["db"].working if row.kind == "xs"}
        self.assertEqual(actual, expected)
        junction_entries = [entry for entry in target["db"].distance_ledger["ledger"] if entry["kind"] == "JUNCTION"]
        self.assertEqual(len(junction_entries), 1)
        self.assertEqual(junction_entries[0]["distance"], 10)

        forged_raw = document.model_dump(mode="json")
        forged_raw["engineering_context"]["flooding_source"]["topology_provenance"][
            "component_ids"] = ["component-forged"]
        forged = type(document).model_validate(forged_raw)
        rejected_plan = multi_reach_plan(self.hdf)
        rejected = {"plan": rejected_plan, "reach_idx": None, "path": str(self.hdf),
            "model_context": ModelContext(rejected_plan.model_id, 3, "forged-target",
                                          str(self.hdf), "CANDIDATE")}
        hecras_api._SESSIONS["forged-target"] = rejected
        with self.assertRaisesRegex(ProjectRestoreError, "topology provenance"):
            restore_candidate_session("forged-target", forged)

    def test_save_api_overwrite_protection_and_candidate_rollback(self):
        from backend.app.main import app
        from backend.app.routers import project_api
        session = sample_session(self.hdf, "active")
        session["model_context"].status = "ACTIVE"
        hecras_api._SESSIONS["active"] = session
        client = TestClient(app)
        first = client.post("/api/project/active/save", json={"path": str(self.project)})
        self.assertEqual(first.status_code, 200, first.text)
        protected = client.post("/api/project/active/save", json={"path": str(self.project)})
        self.assertEqual(protected.status_code, 409)
        overwrite = client.post("/api/project/active/save", json={"overwrite": True})
        self.assertEqual(overwrite.status_code, 200, overwrite.text)

        original = project_api.restore_candidate_session
        def fail_restore(sid, document):
            raise ProjectRestoreError("deliberate restore failure")
        project_api.restore_candidate_session = fail_restore
        try:
            before = set(hecras_api._SESSIONS)
            with patch("backend.app.routers.hecras_api.HdfReader") as reader:
                reader.return_value.read_plan.return_value = sample_plan(self.hdf)
                response = client.post("/api/project/open", json={"path": str(self.project), "model_generation": 9})
            self.assertEqual(response.status_code, 409)
            self.assertIn("deliberate restore failure", response.text)
            self.assertEqual(set(hecras_api._SESSIONS), before)
            self.assertEqual(hecras_api._SESSIONS["active"]["model_context"].status, "ACTIVE")
        finally:
            project_api.restore_candidate_session = original


class NorthProjectRoundTripTests(unittest.TestCase):
    def test_north_hdf_save_reopen_and_export_preflight(self):
        from backend.app.main import app
        root = Path(__file__).resolve().parents[2]
        source = root / "RAS Plot Database and Model Linework" / "Model Data" / "1033_North LOMR As-Built.p04.hdf"
        self.assertTrue(source.is_file(), "North integration HDF is required for persistence validation")
        hecras_api._SESSIONS.clear()
        client = TestClient(app)
        opened = client.post("/api/source", json={"path": str(source), "model_generation": 1})
        self.assertEqual(opened.status_code, 200, opened.text)
        sid = opened.json()["session_id"]
        self.assertEqual(client.post(f"/api/session/{sid}/commit", json={}).status_code, 200)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "North.rasplot.json"
            saved = client.post(f"/api/project/{sid}/save", json={"path": str(project)})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(client.delete(f"/api/session/{sid}").status_code, 200)
            reopened = client.post("/api/project/open", json={"path": str(project), "model_generation": 2})
            self.assertEqual(reopened.status_code, 200, reopened.text)
            restored = reopened.json()
            restored_sid = restored["session_id"]
            self.assertFalse(restored["db"]["panels_stale"])
            panel = restored["db"]["panels"][0]["panel"]
            for fmt in ("pdf", "dxf"):
                result = client.post("/api/report/export", json={
                    "session_id": restored_sid, "format": fmt,
                    "panel_numbers": [panel], "profile_names": [restored["profiles"][0]["profile_id"]],
                })
                self.assertEqual(result.status_code, 200, result.text)
            cad = client.post("/api/report/cad-review-package", json={
                "session_id": restored_sid, "format": "cad-review-package",
                "panel_numbers": [panel], "profile_names": [restored["profiles"][0]["profile_id"]],
            })
            self.assertEqual(cad.status_code, 200, cad.text)


if __name__ == "__main__":
    unittest.main()
