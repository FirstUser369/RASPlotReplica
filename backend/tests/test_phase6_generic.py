from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import zipfile

import h5py
import ezdxf
from PIL import Image
from fastapi import HTTPException

from backend.app.core.cad_review_export import export_cad_review_package
from backend.app.core.dxf_export import clip_station_elevation_polyline, export_dxf
from backend.app.core.hydraulics import segments
from backend.app.core.hdf_reader import CrossSection, HdfReader, ReachGeometry
from backend.app.core.local_state import owned_path
from backend.app.core.model_topology import (
    build_topology, continuous_distances, create_flooding_source, stable_id,
)
from backend.app.core.compatibility import assess_plan_compatibility
from backend.app.core.rasplot_db import RasplotDatabase, build_db_from_plan, build_working_from_plan, auto_panels
from backend.app.core.state_contract import panels_stale, stationing_dirty, validate_collection
from backend.app.core.structure_model import models_from_rows, preflight
from backend.app.routers import hecras_api as api
from backend.app.routers import report


ROOT = Path(__file__).resolve().parents[2]
NORTH = ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"
MUNCIE = ROOT.parent / "Muncie/Muncie.p04.hdf"
INDUSTRIAL_ZIP = Path.home() / "Downloads/Multiple Reach Example.zip"


def xs(river: str, reach: str, rs: str, length: float, index: int) -> CrossSection:
    return CrossSection(river, reach, rs, "", length, 0, 1, index=index)


def reach(model: str, name: str, records: list[CrossSection], us=("External", "", None), ds=("External", "", None)):
    return ReachGeometry(
        model, name, [], records, [], source_path="fixture.hdf",
        source_connectivity={
            "River Name": model, "Reach Name": name,
            "US Type": us[0], "US Name": us[1], "Junction to US XS": us[2],
            "DS Type": ds[0], "DS Name": ds[1], "DS XS to Junction": ds[2],
        },
    )


class UniversalInspectionTests(unittest.TestCase):
    @unittest.skipUnless(NORTH.exists(), "North fixture unavailable")
    def test_supported_hdf_inventory(self):
        capabilities = HdfReader(NORTH).inspect()
        self.assertEqual(capabilities.unit_system, "US Customary")
        self.assertEqual(capabilities.cross_sections_available, "SUPPORTED")

    def test_missing_optional_metadata_and_unknown_dataset_do_not_abort_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.p01.hdf"
            with h5py.File(path, "w") as handle:
                handle.create_group("Geometry")
                handle.create_dataset("Vendor/Optional Future Feature", data=[1])
            capabilities = HdfReader(path).inspect()
            self.assertEqual(capabilities.datum_status, "UNKNOWN")
            self.assertEqual(capabilities.cross_sections_available, "MISSING")

    def test_operation_scoped_compatibility_is_explicit_and_identity_aware(self):
        model = SimpleNamespace(
            source_units="US Customary", source_datum="UNKNOWN", profiles=[],
            capabilities=SimpleNamespace(schema_adapter="RESULT_CROSS_SECTION_ATTRIBUTES"),
            reaches=[ReachGeometry("River", "Reach", [], [xs("River", "Reach", "10", 0, 0)], [],
                                   source_id="reach-source", source_order_status="SOURCE_DOWNSTREAM_FIRST_NORMALIZED")],
        )
        findings = assess_plan_compatibility(model)
        ordering = next(item for item in findings if item["feature"] == "cross-section ordering")
        self.assertEqual(ordering["status"], "SUPPORTED_WITH_LIMITATIONS")
        self.assertEqual(ordering["entity"]["river"], "River")
        datum = next(item for item in findings if item["feature"] == "vertical datum")
        self.assertIn("AUTHORITATIVE_EXPORT", datum["affected_stages"])


@unittest.skipUnless(INDUSTRIAL_ZIP.exists(), "Industrial_Park fixture unavailable")
class IndustrialParkTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(INDUSTRIAL_ZIP) as archive:
            for name in archive.namelist():
                if name.rsplit("/", 1)[-1] in {
                    "Industrial_Park.p02.hdf", "Industrial_Park.g02.hdf",
                    "Industrial_Park.p02", "Industrial_Park.g02", "Industrial_Park.prj",
                }:
                    archive.extract(name, cls.temp.name)
        cls.path = Path(cls.temp.name) / "HEC-RAS Submittal/Industrial_Park.p02.hdf"
        cls.plan = HdfReader(cls.path).read_plan()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_older_version_and_unvalidated_structure_still_inventory(self):
        self.assertIn("5.0.7", self.plan.ras_version)
        self.assertEqual(self.plan.capabilities.schema_adapter,
                         "STEADY_BASE_OUTPUT_GEOMETRY_INFO_LABELS_V1")
        detected = {item["type"]: item["status"] for item in self.plan.capabilities.structures_detected}
        self.assertEqual(detected["Multiple Opening"], "UNVALIDATED")
        self.assertEqual(len(self.plan.reaches), 9)

    def test_inventory_fixture_counts(self):
        self.assertEqual(sum(len(r.cross_sections) for r in self.plan.reaches), 119)
        self.assertEqual(len(self.plan.topology.junctions), 5)

    def test_real_multiple_group_culvert_uses_all_source_chord_arrays(self):
        reach_idx = next(index for index, reach in enumerate(self.plan.reaches)
                         if reach.river == "North_Soln" and reach.reach == "Reach 5")
        model = models_from_rows(build_db_from_plan(self.plan, reach_idx).working)[0]
        self.assertEqual((model.rs, model.type), ("5874", "MULTIPLE_OPENING"))
        self.assertEqual(model.capability_id, "CULVERT_GROUP_ENVELOPES_V1")
        self.assertEqual(model.status, "VALIDATED", model.reason)
        self.assertEqual([(face["opening_name"], face["barrels"])
                          for face in model.faces[::2]],
                         [('1 - 24" CMP', 1), ('10 - 36" CMP', 10)])
        self.assertTrue(all(face["high_chord"] >= face["low_chord"]
                            for face in model.faces))

    def test_cross_sections_are_deterministically_upstream_first(self):
        for item in self.plan.reaches:
            stations = [float(value.rs) for value in item.cross_sections]
            self.assertEqual(stations, sorted(stations, reverse=True))

    def test_terminal_nan_length_is_not_an_interval(self):
        for item in self.plan.reaches:
            self.assertEqual(item.cross_sections[-1].length_channel, 0)
            self.assertEqual(item.cross_sections[-1].length_provenance["status"], "TERMINAL_NON_INTERVAL")

    def test_engineering_supplied_path_is_fixture_configuration_only(self):
        names = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = []
        by_id = {}
        for name in names:
            normalized = next(r for r in self.plan.topology.reaches.values()
                              if r.river_name == "South_Soln" and r.reach_name == name)
            ids.append(normalized.id)
            by_id[normalized.id] = next(r for r in self.plan.reaches
                                        if r.river == "South_Soln" and r.reach == name)
        source = create_flooding_source(self.plan.model_id, "fixture path", ids, self.plan.topology,
                                        "ENGINEERING_SUPPLIED")
        self.assertEqual(source.topology_validation_status, "SUPPORTED")
        self.assertEqual([round(s.transition_distance, 2) for s in source.ordered_segments[:-1]],
                         [320.12, 16.12, 187.63, 67.63, 52.39])
        result = continuous_distances(source, self.plan.topology, by_id)
        self.assertEqual(result.status, "SUPPORTED")
        self.assertEqual([round(e.distance, 2) for e in result.ledger if e.kind == "JUNCTION"],
                         [320.12, 16.12, 187.63, 67.63, 52.39])

    def test_generic_api_builds_validated_industrial_path(self):
        api._SESSIONS.clear()

    def test_real_industrial_culvert_groups_are_source_verified(self):
        api._SESSIONS.clear();self.addCleanup(api._SESSIONS.clear)
        opened=api.open_source(api.OpenRequest(path=str(self.path),model_generation=71))
        sid=opened["session_id"];inventory=api.model_inventory(sid)
        names=["Reach 4","Reach 3.2","Reach 3C","Reach 3.1","Reach 3A","Reach 3"]
        ids=[next(item["id"] for item in inventory["reaches"]
                  if item["river"]=="South_Soln" and item["reach"]==name) for name in names]
        api.commit_flooding_source(sid,api.FloodingSourceRequest(
            display_name="Southern Solution Wash",reach_ids=ids,
            naming_provenance="ENGINEERING_SUPPLIED"))
        db=api._SESSIONS[sid]["db"]
        models={(model.reach,model.rs):model for model in models_from_rows(db.working)}
        expected={
            ("Reach 3","9180"):[("Circular",6,4.,4.,50.,2.)],
            ("Reach 3","8590"):[("Circular",6,4.,4.,50.,2.)],
            ("Reach 3A","5804"):[("Circular",4,4.,4.,61.,2.)],
            ("Reach 3C","3951"):[("Circular",1,2.,2.,72.,3.),("Ellipse",4,3.,4.,72.,3.)],
        }
        for key,openings in expected.items():
            model=models[key]
            self.assertEqual(model.status,"VALIDATED",model.reason)
            self.assertEqual(model.capability_id,"CULVERT_GROUP_ENVELOPES_V1")
            self.assertEqual([(group["Shape Name"],int(group["Barrels"]),group["Rise"],
                               group["Span"],group["Length"],group["US Distance"])
                              for group in model.openings],openings)
            self.assertEqual(len(model.faces),2*len(openings))
            self.assertTrue(all(face["high_chord"]>=face["low_chord"] for face in model.faces))
        records={(record["reach"],record["rs"]):record for record in
                 preflight(db.working,0.,max(row.cumdist for row in db.working if row.kind=="xs"))}
        self.assertTrue(all(records[key]["status"]=="VALIDATED" for key in expected))
        revision=(0.,max(row.cumdist for row in db.working if row.kind=="xs"))
        api.put_revision_limits(sid,api.RevisionLimitsUpdate(
            revision_start_station=revision[0],revision_end_station=revision[1]))
        panels=auto_panels(db.working,h_scale=500.,v_scale=5.,station_range=revision)
        api.put_panels(sid,api.PanelsReplace(
            panels=[api.PanelIn(**panel.__dict__) for panel in panels],auto=False))
        with tempfile.TemporaryDirectory() as directory:
            dxf_path=Path(directory)/"industrial.dxf";pdf_path=Path(directory)/"industrial.pdf"
            export_dxf(dxf_path,db)
            report.render_profile_pdf_from_db(str(pdf_path),db)
            outlines=list(ezdxf.readfile(dxf_path).modelspace().query(
                'LWPOLYLINE[layer=="STRUCT_CULVERT_OUTLINE"]'))
            self.assertEqual(len(outlines),5)
            self.assertTrue(all(outline.closed for outline in outlines))
            self.assertGreater(pdf_path.stat().st_size,1000)

    def test_multi_reach_working_edits_recalculate_through_junctions(self):
        api._SESSIONS.clear()
        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=42))
        sid = opened["session_id"]
        inventory = api.model_inventory(sid)
        wanted = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in wanted]
        request = api.FloodingSourceRequest(display_name="Continuous source", reach_ids=ids)
        built = api.commit_flooding_source(sid, request)
        db = api._SESSIONS[sid]["db"]
        baseline = {row.source_identity: row.cumdist for row in db.working if row.kind == "xs"}
        reach_rows = {
            name: [row for row in db.working if row.kind == "xs" and row.reach == name and row.length_chnl > 0]
            for name in (wanted[0], wanted[len(wanted)//2], wanted[-1])
        }
        edits = []
        for increment, name in enumerate(reach_rows, 1):
            row = reach_rows[name][0]
            edits.append(api.WorkingPatch(id=row.id, xs_id=row.source_identity,
                                          length_chnl=row.length_chnl + increment))
        api.patch_working(sid, edits)
        self.assertTrue(stationing_dirty(db))
        result = api.post_recalc_cumdist(sid, api.CumdistRequest(start=0))
        self.assertFalse(stationing_dirty(db))
        self.assertTrue(panels_stale(db))
        self.assertEqual(result["revision_limits"]["review_required"], False)
        updated = {row.source_identity: row.cumdist for row in db.working if row.kind == "xs"}
        self.assertAlmostEqual(max(updated.values()) - max(baseline.values()), 6.0)
        junctions = [entry for entry in db.distance_ledger["ledger"] if entry["kind"] == "JUNCTION"]
        self.assertEqual(len(junctions), len(ids) - 1)
        self.assertEqual([round(entry["distance"], 2) for entry in junctions],
                         [320.12, 16.12, 187.63, 67.63, 52.39])
        self.assertTrue(all(row.source_length_chnl == row.canonical_length_chnl
                            for row in db.working if row.kind == "xs"))
        self.assertTrue(all(row.length_chnl == 0 for row in db.working if row.kind == "struct"))

    def test_industrial_reach3_10168_edit_propagates_exactly_twenty_feet(self):
        api._SESSIONS.clear()
        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=43))
        sid = opened["session_id"]
        inventory = api.model_inventory(sid)
        wanted = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in wanted]
        api.commit_flooding_source(sid, api.FloodingSourceRequest(
            display_name="Southern Solution Wash", reach_ids=ids))
        db = api._SESSIONS[sid]["db"]
        xs_rows = [row for row in db.working if row.kind == "xs"]
        before = {row.source_identity: row.cumdist for row in xs_rows}
        target = next(row for row in xs_rows
                      if row.reach == "Reach 3" and row.rs == "10168")
        self.assertAlmostEqual(target.length_chnl, 217.3000030517578)
        junction_before = [entry["distance"] for entry in db.distance_ledger["ledger"]
                           if entry["kind"] == "JUNCTION"]
        api.patch_working(sid, [api.WorkingPatch(
            id=target.id, xs_id=target.source_identity,
            length_chnl=target.length_chnl + 20.)])
        api.post_recalc_cumdist(sid, api.CumdistRequest(start=0.))
        deltas = {(row.reach, row.rs): row.cumdist-before[row.source_identity]
                  for row in xs_rows}
        changed = {identity: delta for identity, delta in deltas.items()
                   if abs(delta) > 1e-8}
        self.assertEqual(changed, {
            ("Reach 3", "10168"): 20.,
            ("Reach 3", "10458"): 20.,
        })
        self.assertEqual(sum(abs(delta) <= 1e-8 for delta in deltas.values()), 71)
        junction_after = [entry["distance"] for entry in db.distance_ledger["ledger"]
                          if entry["kind"] == "JUNCTION"]
        self.assertEqual(junction_after, junction_before)
        self.assertEqual(len(junction_after), 5)
        api._SESSIONS.clear()
        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=41))
        sid = opened["session_id"]
        inventory = api.model_inventory(sid)
        wanted = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in wanted]
        request = api.FloodingSourceRequest(
            display_name="Validated source", reach_ids=ids,
            naming_provenance="ENGINEERING_SUPPLIED",
        )
        preview = api.build_flooding_source(sid, request)
        self.assertEqual(preview["continuous_distance"]["status"], "SUPPORTED")
        built = api.commit_flooding_source(sid, request)
        self.assertEqual(built["flooding_source"]["topology_validation_status"], "SUPPORTED")
        self.assertEqual(sum(row["kind"] == "xs" for row in built["db"]["working"]),
                         sum(item["xs_count"] for item in inventory["reaches"] if item["id"] in ids))
        profile = api.get_profile(sid, profiles='["' + self.plan.profiles[0].profile_id + '"]', reach_idx=0, label_every=5)
        self.assertEqual(len(profile["cross_sections"]),
                         sum(item["xs_count"] for item in inventory["reaches"] if item["id"] in ids))
        api._SESSIONS.clear()

    def test_continuous_source_revision_and_panels_cross_junctions(self):
        api._SESSIONS.clear()
        self.addCleanup(api._SESSIONS.clear)
        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=77))
        sid = opened["session_id"]
        inventory = api.model_inventory(sid)
        names = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in names]
        api.commit_flooding_source(sid, api.FloodingSourceRequest(
            display_name="Fixture source", reach_ids=ids,
            naming_provenance="ENGINEERING_SUPPLIED"))
        db = api._SESSIONS[sid]["db"]
        extent = (min(row.cumdist for row in db.working if row.kind == "xs"),
                  max(row.cumdist for row in db.working if row.kind == "xs"))
        self.assertAlmostEqual(extent[1] - extent[0], 8847.09, places=2)

        full = auto_panels(db.working, h_scale=250, v_scale=100, station_range=extent)
        self.assertGreater(len(full), 1)
        validate_collection(full, extent)
        self.assertEqual([panel.panel for panel in full], list(range(1, len(full) + 1)))

        junctions = [entry["cumulative_distance"] for entry in db.distance_ledger["ledger"]
                     if entry["kind"] == "JUNCTION"]
        crossing = next(value for value in junctions if extent[0] < value < extent[1])
        partial = (crossing - 50, crossing + 50)
        api.put_revision_limits(sid, api.RevisionLimitsUpdate(
            revision_start_station=partial[0], revision_end_station=partial[1]))
        partial_panels = auto_panels(db.working, h_scale=50, v_scale=100,
                                    station_range=partial)
        validate_collection(partial_panels, partial)
        self.assertTrue(any(panel.start_station < crossing < panel.end_station
                            for panel in partial_panels))

        boundary_scale = (crossing - extent[0]) / 13.0
        boundary_panels = auto_panels(db.working, h_scale=boundary_scale, v_scale=100,
                                     station_range=extent)
        validate_collection(boundary_panels, extent)
        self.assertTrue(any(abs(panel.end_station - crossing) < 1e-6
                            for panel in boundary_panels))

        duplicate_labels = {}
        for row in db.working:
            if row.kind == "xs":
                duplicate_labels.setdefault(row.rs, set()).add(row.source_identity)
        self.assertTrue(all(len(identities) == len(set(identities))
                            for identities in duplicate_labels.values()))

    def test_real_multi_reach_cad_review_package_alignment(self):
        api._SESSIONS.clear()
        self.addCleanup(api._SESSIONS.clear)
        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=88))
        sid = opened["session_id"]
        inventory = api.model_inventory(sid)
        names = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in names]
        api.commit_flooding_source(sid, api.FloodingSourceRequest(
            display_name="Southern Solution Wash", reach_ids=ids,
            naming_provenance="ENGINEERING_SUPPLIED"))
        db = api._SESSIONS[sid]["db"]
        revision = (0.0, 2000.0)  # below the first unsupported structure's proven bounds
        api.put_revision_limits(sid, api.RevisionLimitsUpdate(
            revision_start_station=revision[0], revision_end_station=revision[1]))
        panels = auto_panels(db.working, h_scale=200.0, v_scale=5.0,
                            station_range=revision)
        self.assertEqual(len(panels), 1)
        api.put_panels(sid, api.PanelsReplace(
            panels=[api.PanelIn(**panel.__dict__) for panel in panels], auto=False))
        panel = db.panels[0]
        profile = db.profiles[0]
        self.assertFalse(any(row.profiles.get(profile.profile_id) is None
                             for row in db.working if row.kind == "xs"))

        junctions = [entry["cumulative_distance"] for entry in db.distance_ledger["ledger"]
                     if entry["kind"] == "JUNCTION"]
        self.assertTrue(any(revision[0] < value < revision[1] for value in junctions))
        xs_distances = [row.cumdist for row in db.working if row.kind == "xs"]
        self.assertEqual(xs_distances, sorted(xs_distances, reverse=True))

        result = report.export_cad_review(report.CadReviewPackageOptions(
            session_id=sid, panel_numbers=[panel.panel],
            profile_names=[profile.profile_id],
            expected_engineering_version=db.to_dict()["engineering_version"]))
        package = owned_path(sid, result["path"])
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(package) as archive:
            expected = {f"CAD_Review_Panel_{panel.panel}.{suffix}"
                        for suffix in ("dxf", "png", "pgw")}
            expected.add("CAD_Review_manifest.json")
            self.assertEqual(set(archive.namelist()), expected)
            archive.extractall(directory)
            root = Path(directory)
            dxf = ezdxf.readfile(root / f"CAD_Review_Panel_{panel.panel}.dxf")
            self.assertEqual(dxf.dxfversion, "AC1009")
            lines = list(dxf.modelspace().query(
                f'POLYLINE[layer=="{profile.layer}"]'))
            self.assertGreater(len(lines), 0)
            actual = [[(vertex.dxf.location.x, vertex.dxf.location.y)
                       for vertex in line.vertices] for line in lines]
            expected_runs = [clip_station_elevation_polyline(run, *revision)
                             for run in segments([(row.cumdist, row.profiles.get(profile.profile_id))
                                                  for row in db.working if row.kind == "xs"])]
            expected_runs = [run for run in expected_runs if len(run) >= 2]
            self.assertEqual(actual, expected_runs)
            endpoint_stations = [point[0] for run in actual for point in run]
            self.assertAlmostEqual(min(endpoint_stations), revision[0])
            self.assertAlmostEqual(max(endpoint_stations), revision[1])

            png = root / f"CAD_Review_Panel_{panel.panel}.png"
            pgw = [float(value) for value in
                   (root / f"CAD_Review_Panel_{panel.panel}.pgw").read_text().splitlines()]
            with Image.open(png) as image:
                width, height = image.size
            self.assertEqual((width, height), (result["width_px"], result["height_px"]))
            self.assertAlmostEqual(pgw[4] - pgw[0] / 2, revision[0])
            self.assertAlmostEqual(pgw[4] + (width - .5) * pgw[0], revision[1])
            self.assertAlmostEqual(pgw[5] - pgw[3] / 2, panel.end_elevation)
            self.assertAlmostEqual(pgw[5] + (height - .5) * pgw[3], panel.start_elevation)

    @unittest.skipUnless(NORTH.exists(), "North fixture unavailable")
    def test_north_industrial_north_cad_artifacts_are_session_isolated(self):
        api._SESSIONS.clear()
        self.addCleanup(api._SESSIONS.clear)

        def north_artifact(generation):
            opened = api.open_source(api.OpenRequest(path=str(NORTH), model_generation=generation))
            sid = opened["session_id"]
            api.commit_session(sid)
            db = api._SESSIONS[sid]["db"]
            result = report.export_cad_review(report.CadReviewPackageOptions(
                session_id=sid, panel_numbers=[db.panels[0].panel],
                profile_names=[db.profiles[0].profile_id],
                expected_engineering_version=db.to_dict()["engineering_version"]))
            return sid, result["path"]

        first_sid, first_path = north_artifact(101)

        opened = api.open_source(api.OpenRequest(path=str(self.path), model_generation=102))
        industrial_sid = opened["session_id"]
        inventory = api.model_inventory(industrial_sid)
        names = ["Reach 4", "Reach 3.2", "Reach 3C", "Reach 3.1", "Reach 3A", "Reach 3"]
        ids = [next(item["id"] for item in inventory["reaches"]
                    if item["river"] == "South_Soln" and item["reach"] == name)
               for name in names]
        api.commit_flooding_source(industrial_sid, api.FloodingSourceRequest(
            display_name="Southern Solution Wash", reach_ids=ids,
            naming_provenance="ENGINEERING_SUPPLIED"))
        industrial_db = api._SESSIONS[industrial_sid]["db"]
        revision = (0.0, 2000.0)
        api.put_revision_limits(industrial_sid, api.RevisionLimitsUpdate(
            revision_start_station=revision[0], revision_end_station=revision[1]))
        panel = auto_panels(industrial_db.working, h_scale=200.0, v_scale=5.0,
                            station_range=revision)[0]
        api.put_panels(industrial_sid, api.PanelsReplace(
            panels=[api.PanelIn(**panel.__dict__)], auto=False))
        industrial_result = report.export_cad_review(report.CadReviewPackageOptions(
            session_id=industrial_sid, panel_numbers=[panel.panel],
            profile_names=[industrial_db.profiles[0].profile_id],
            expected_engineering_version=industrial_db.to_dict()["engineering_version"]))

        final_sid, final_path = north_artifact(103)
        artifacts = [(first_sid, first_path),
                     (industrial_sid, industrial_result["path"]),
                     (final_sid, final_path)]
        self.assertEqual(len({sid for sid, _path in artifacts}), 3)
        self.assertEqual(len({path for _sid, path in artifacts}), 3)
        for owner, path in artifacts:
            self.assertTrue(owned_path(owner, path).is_file())
            for other, _other_path in artifacts:
                if other != owner:
                    with self.assertRaises(HTTPException):
                        owned_path(other, path)


class GenericTopologyAndDistanceTests(unittest.TestCase):
    def setUp(self):
        self.model = "model-generic"
        self.up = reach("River", "Up", [xs("River", "Up", "20", 10, 0), xs("River", "Up", "10", 0, 1)],
                        ds=("Junction", "J", 3.0))
        self.down = reach("River", "Down", [xs("River", "Down", "8", 5, 0), xs("River", "Down", "1", 0, 1)],
                          us=("Junction", "J", 2.0))
        self.graph = build_topology(self.model, [self.up, self.down], [{"Name": "J"}], "fixture.hdf")
        self.up_id = next(r.id for r in self.graph.reaches.values() if r.reach_name == "Up")
        self.down_id = next(r.id for r in self.graph.reaches.values() if r.reach_name == "Down")

    def test_single_reach(self):
        source = create_flooding_source(self.model, "one", [self.down_id], self.graph)
        result = continuous_distances(source, self.graph, {self.down_id: self.down})
        self.assertEqual(result.status, "SUPPORTED")
        self.assertFalse(any(e.kind == "JUNCTION" for e in result.ledger))

    def test_blank_name_and_invalid_provenance_are_blocked(self):
        source = create_flooding_source(self.model, " ", [self.down_id], self.graph, "GUESSED")
        self.assertEqual(source.topology_validation_status, "BLOCKED")
        self.assertTrue(any("name" in message for message in source.validation_messages))
        self.assertTrue(any("provenance" in message for message in source.validation_messages))

    def test_duplicate_station_labels_have_distinct_stable_working_identities(self):
        one = xs("River", "One", "100", 0, 0)
        two = xs("River", "Two", "100", 0, 1)
        synthetic = ReachGeometry("Source", "Flooding Source", [], [one, two], [])
        plan = SimpleNamespace(model_id=self.model, plan_path=Path("fixture.p01.hdf"),
                               reaches=[synthetic], profiles=[], unassigned_structures=[])
        rows = build_working_from_plan(plan, cumdist=[10, 0])
        identities = [row.source_identity for row in rows if row.kind == "xs"]
        self.assertEqual(len(identities), 2)
        self.assertEqual(len(set(identities)), 2)
        self.assertEqual(identities, [stable_id("xs", self.model, "River", "One", "100"),
                                      stable_id("xs", self.model, "River", "Two", "100")])

        sid = "duplicate-station-session"
        api._SESSIONS[sid] = {"db": RasplotDatabase(working=rows)}
        first, second = [row for row in rows if row.kind == "xs"]
        api.patch_working(sid, [api.WorkingPatch(id=first.id, xs_id=first.source_identity,
                                                 minchel=7)])
        self.assertEqual(first.minchel, 7)
        self.assertIsNone(second.minchel)
        with self.assertRaises(Exception):
            api.patch_working(sid, [api.WorkingPatch(id=first.id, xs_id=second.source_identity,
                                                     minchel=9)])
        self.assertEqual(first.minchel, 7)
        api._SESSIONS.clear()

    def test_sequential_reaches_and_exact_once_transition(self):
        source = create_flooding_source(self.model, "two", [self.down_id, self.up_id], self.graph)
        result = continuous_distances(source, self.graph, {self.down_id: self.down, self.up_id: self.up})
        transitions = [e for e in result.ledger if e.kind == "JUNCTION"]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].distance, 5)

    def test_edit_cascades_upstream_and_structure_dimensions_are_irrelevant(self):
        source = create_flooding_source(self.model, "two", [self.down_id, self.up_id], self.graph)
        reaches = {self.down_id: self.down, self.up_id: self.up}
        baseline = continuous_distances(source, self.graph, reaches)
        edited_id = stable_id("xs", self.model, "River", "Down", "8")
        edited = continuous_distances(source, self.graph, reaches, working_lengths={edited_id: 15})
        self.assertEqual(edited.distances[edited_id] - baseline.distances[edited_id], 10)
        up_ids = [key for key in edited.distances if key not in {
            stable_id("xs", self.model, "River", "Down", "1"), edited_id}]
        self.assertTrue(all(edited.distances[key] - baseline.distances[key] == 10 for key in up_ids))

    def test_invalid_order_is_blocked(self):
        source = create_flooding_source(self.model, "wrong", [self.up_id, self.down_id], self.graph)
        self.assertEqual(source.topology_validation_status, "BLOCKED")

    def test_missing_transition_distance_is_blocked(self):
        broken = reach("River", "Broken", [xs("River", "Broken", "20", 1, 0), xs("River", "Broken", "10", 0, 1)],
                       ds=("Junction", "J", None))
        graph = build_topology(self.model, [broken, self.down], [{"Name": "J"}], "fixture.hdf")
        broken_id = next(r.id for r in graph.reaches.values() if r.reach_name == "Broken")
        source = create_flooding_source(self.model, "blocked", [self.down_id, broken_id], graph)
        self.assertEqual(source.topology_validation_status, "BLOCKED")

    def test_ambiguous_junction_identity_is_blocked(self):
        graph = build_topology(self.model, [self.up, self.down], [{"Name": "J"}, {"Name": "J"}], "fixture.hdf")
        source = create_flooding_source(self.model, "ambiguous", [self.down_id, self.up_id], graph)
        self.assertEqual(source.topology_validation_status, "BLOCKED")
        self.assertTrue(any("not unique" in message for message in graph.warnings))

    def test_reorder_recomputes_deterministically_and_ledger_has_provenance(self):
        source = create_flooding_source(self.model, "valid", [self.down_id, self.up_id], self.graph)
        reaches = {self.down_id: self.down, self.up_id: self.up}
        first = continuous_distances(source, self.graph, reaches).to_dict()
        second = continuous_distances(source, self.graph, reaches).to_dict()
        self.assertEqual(first, second)
        self.assertTrue(all(entry["provenance"] for entry in first["ledger"]))
        reversed_source = create_flooding_source(self.model, "reordered", [self.up_id, self.down_id], self.graph)
        self.assertEqual(reversed_source.topology_validation_status, "BLOCKED")

    def test_divergence_and_convergence_expose_paths_without_choosing(self):
        trunk = reach("R", "Trunk", [xs("R", "Trunk", "9", 1, 0), xs("R", "Trunk", "8", 0, 1)], ds=("Junction", "Split", 1))
        a = reach("R", "A", [xs("R", "A", "7", 1, 0), xs("R", "A", "6", 0, 1)], us=("Junction", "Split", 1), ds=("Junction", "Return", 1))
        b = reach("R", "B", [xs("R", "B", "7", 1, 0), xs("R", "B", "6", 0, 1)], us=("Junction", "Split", 1), ds=("Junction", "Return", 1))
        tail = reach("R", "Tail", [xs("R", "Tail", "5", 1, 0), xs("R", "Tail", "4", 0, 1)], us=("Junction", "Return", 1))
        graph = build_topology("branches", [trunk, a, b, tail], [{"Name": "Split"}, {"Name": "Return"}], "fixture")
        self.assertEqual(len(graph.connections), 4)
        self.assertEqual(len(next(j for j in graph.junctions.values() if j.name == "Split").downstream_reach_ids), 2)
        self.assertEqual(len(next(j for j in graph.junctions.values() if j.name == "Return").upstream_reach_ids), 2)


@unittest.skipUnless(NORTH.exists() and MUNCIE.exists(), "North/Muncie fixtures unavailable")
class ModelContextLifecycleTests(unittest.TestCase):
    def tearDown(self):
        api._SESSIONS.clear()

    def open(self, path, generation):
        return api.open_source(api.OpenRequest(path=str(path), model_generation=generation))

    def test_north_muncie_north_are_distinct_clean_contexts(self):
        first = self.open(NORTH, 1); api.commit_session(first["session_id"])
        second = self.open(MUNCIE, 2); api.commit_session(second["session_id"])
        third = self.open(NORTH, 3); api.commit_session(third["session_id"])
        self.assertEqual([len(x["reaches"]) for x in (first, second, third)], [1, 1, 1])
        self.assertEqual([len(x["profiles"]) for x in (first, second, third)], [1, 34, 1])
        self.assertEqual(len({x["session_id"] for x in (first, second, third)}), 3)
        self.assertEqual(third["db"]["header"]["community"], "")

    def test_failed_candidate_preserves_active_context(self):
        active = self.open(NORTH, 1); api.commit_session(active["session_id"])
        with self.assertRaises(Exception):
            self.open(ROOT / "missing.p01.hdf", 2)
        self.assertIn(active["session_id"], api._SESSIONS)
        self.assertEqual(api._SESSIONS[active["session_id"]]["model_context"].status, "ACTIVE")

    def test_new_generation_cleans_abandoned_candidate(self):
        abandoned = self.open(NORTH, 1)
        current = self.open(MUNCIE, 2)
        self.assertNotIn(abandoned["session_id"], api._SESSIONS)
        self.assertIn(current["session_id"], api._SESSIONS)

    def test_failed_newer_generation_still_cleans_abandoned_candidate(self):
        abandoned = self.open(NORTH, 1)
        with self.assertRaises(Exception):
            self.open(ROOT / "missing.p01.hdf", 2)
        self.assertNotIn(abandoned["session_id"], api._SESSIONS)


if __name__ == "__main__":
    unittest.main()
