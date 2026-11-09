import tempfile
import time
import unittest
import zipfile
import json
from pathlib import Path

import ezdxf
from PIL import Image
from fastapi import HTTPException

from backend.app.core.cad_review_export import CAD_REVIEW_MANIFEST, CadReviewBounds, export_cad_review_package
from backend.app.core.export_jobs import ExportCancelled, get_export_job
from backend.app.core.engineering_drawing import DrawingBuildConfig, build_engineering_drawing
from backend.app.core.local_state import owned_path
from backend.app.core.rasplot_db import Landmark, PanelDef, ProfileMeta, RasplotDatabase, WorkingRow
from backend.app.routers.hecras_api import _SESSIONS
from backend.app.core.export_jobs import get_export_job
from backend.app.routers.report import CadReviewPackageOptions, export_cad_review, start_cad_review_export


def sample_db(name="Source A"):
    db = RasplotDatabase()
    db.header.stream_name = name
    db.panels = [PanelDef(4, 0.0, 100.0, 90.0, 110.0, h_scale=100.0, v_scale=5.0)]
    db.profiles = [ProfileMeta(name="1%", profile_id="profile-a", key="profile-a",
                               selected=True, color="#C00000", layer="WSEL_1PCT")]
    values = [(0.0, 96.0), (25.0, 98.0), (50.0, None), (75.0, 102.0), (100.0, 104.0)]
    db.working = [WorkingRow(id=i + 1, river="River", reach="Reach", rs=str(1000-i),
                     cumdist=x, minchel=ground, profiles={"profile-a": wsel},
                     source_identity=f"source|River|Reach|{i}", row_identity=f"xs-{i}")
                  for i, ((x, wsel), ground) in enumerate(zip(values, [92, 93, 94, 95, 96]))]
    db.working[0].label_letter = "A"
    return db


class CadReviewExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_r12_raw_coordinates_gaps_and_world_alignment(self):
        db = sample_db()
        target = Path(self.temp.name) / "package.zip"
        result = export_cad_review_package(target, db, db.profiles, db.panels[0], (0.0, 100.0))
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(set(archive.namelist()), {
                "CAD_Review_Panel_4.dxf", "CAD_Review_Panel_4.png", "CAD_Review_Panel_4.pgw",
                CAD_REVIEW_MANIFEST})
            archive.extractall(self.temp.name)
        doc = ezdxf.readfile(Path(self.temp.name) / "CAD_Review_Panel_4.dxf")
        self.assertEqual(doc.dxfversion, "AC1009")
        self.assertEqual(len(doc.modelspace().query("LWPOLYLINE")), 0)
        profiles = list(doc.modelspace().query('POLYLINE[layer=="WSEL_1PCT"]'))
        self.assertEqual(len(profiles), 2)
        points = [[(v.dxf.location.x, v.dxf.location.y) for v in line.vertices] for line in profiles]
        self.assertEqual(points, [[(0.0, 96.0), (25.0, 98.0)], [(75.0, 102.0), (100.0, 104.0)]])
        raw_dxf = (Path(self.temp.name) / "CAD_Review_Panel_4.dxf").read_text(errors="replace")
        self.assertIn("\nVERTEX\n", raw_dxf)
        self.assertGreater(len(doc.modelspace().query("POINT")), 0)
        self.assertGreater(len(doc.modelspace().query("TEXT")), 0)
        metadata = list(doc.modelspace().query('TEXT[layer=="PANEL_METADATA"]'))
        self.assertEqual(len(metadata), 1)
        self.assertIn("PANEL 4", metadata[0].dxf.text)
        self.assertIn("VERTICAL DATUM: UNKNOWN", metadata[0].dxf.text)
        with Image.open(Path(self.temp.name) / "CAD_Review_Panel_4.png") as image:
            self.assertEqual(image.size, (result["width_px"], result["height_px"]))
        world = [float(v) for v in (Path(self.temp.name) / "CAD_Review_Panel_4.pgw").read_text().splitlines()]
        width, height = result["width_px"], result["height_px"]
        self.assertAlmostEqual(world[4], world[0] / 2.0)
        self.assertAlmostEqual(world[5], 110.0 + world[3] / 2.0)
        self.assertAlmostEqual(world[4] + (width - 1) * world[0], 100.0 - world[0] / 2.0)
        self.assertAlmostEqual(world[5] + (height - 1) * world[3], 90.0 - world[3] / 2.0)

    def test_revision_boundary_endpoints_are_interpolated_in_raw_coordinates(self):
        db = sample_db()
        target = Path(self.temp.name) / "partial.zip"
        export_cad_review_package(target, db, db.profiles, db.panels[0], (10.0, 20.0))
        with zipfile.ZipFile(target) as archive:
            archive.extract("CAD_Review_Panel_4.dxf", self.temp.name)
        doc = ezdxf.readfile(Path(self.temp.name) / "CAD_Review_Panel_4.dxf")
        line = list(doc.modelspace().query('POLYLINE[layer=="WSEL_1PCT"]'))[0]
        points = [(v.dxf.location.x, v.dxf.location.y) for v in line.vertices]
        self.assertEqual([point[0] for point in points], [10.0, 20.0])
        self.assertAlmostEqual(points[0][1], 96.8)
        self.assertAlmostEqual(points[1][1], 97.6)

    def test_saved_suffix_is_r12_metadata_only_and_keeps_numeric_package_names(self):
        db = sample_db()
        db.panels[0].panel_suffix = "P"
        target = Path(self.temp.name) / "suffixed.zip"
        result = export_cad_review_package(
            target, db, db.profiles, db.panels[0], (0., 100.))
        self.assertEqual(result["files"], [
            "CAD_Review_Panel_4.dxf", "CAD_Review_Panel_4.png", "CAD_Review_Panel_4.pgw",
            CAD_REVIEW_MANIFEST])
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), result["files"])
            archive.extract("CAD_Review_Panel_4.dxf", self.temp.name)
        doc = ezdxf.readfile(Path(self.temp.name) / "CAD_Review_Panel_4.dxf")
        metadata = list(doc.modelspace().query('TEXT[layer=="PANEL_METADATA"]'))[0]
        self.assertIn("PANEL 4P", metadata.dxf.text)

    def test_cad_review_route_keeps_numeric_selection_and_suffix_metadata(self):
        db = sample_db()
        db.panels[0].panel_suffix = "P"
        _SESSIONS["cad-suffix-route"] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop("cad-suffix-route", None))
        result = export_cad_review(CadReviewPackageOptions(
            session_id="cad-suffix-route", panel_numbers=[4], profile_names=["profile-a"]))
        self.assertEqual(result["panel_number"], 4)
        self.assertEqual(result["files"], [
            "CAD_Review_Panel_4.dxf", "CAD_Review_Panel_4.png", "CAD_Review_Panel_4.pgw",
            CAD_REVIEW_MANIFEST])
        with zipfile.ZipFile(result["path"]) as archive:
            archive.extract("CAD_Review_Panel_4.dxf", self.temp.name)
        doc = ezdxf.readfile(Path(self.temp.name) / "CAD_Review_Panel_4.dxf")
        metadata = list(doc.modelspace().query('TEXT[layer=="PANEL_METADATA"]'))[0]
        self.assertIn("PANEL 4P", metadata.dxf.text)

    def test_endpoint_requires_one_panel_and_preserves_session_ownership(self):
        first, second = sample_db("First"), sample_db("Second")
        _SESSIONS["cad-first"] = {"db": first}
        _SESSIONS["cad-second"] = {"db": second}
        self.addCleanup(lambda: _SESSIONS.pop("cad-first", None))
        self.addCleanup(lambda: _SESSIONS.pop("cad-second", None))
        with self.assertRaisesRegex(HTTPException, "at least one"):
            export_cad_review(CadReviewPackageOptions(session_id="cad-first", panel_numbers=[]))
        one = export_cad_review(CadReviewPackageOptions(
            session_id="cad-first", panel_numbers=[4], profile_names=["profile-a"]))
        two = export_cad_review(CadReviewPackageOptions(
            session_id="cad-second", panel_numbers=[4], profile_names=["profile-a"]))
        self.assertNotEqual(one["path"], two["path"])
        self.assertTrue(owned_path("cad-first", one["path"]).is_file())
        with self.assertRaises(HTTPException):
            owned_path("cad-second", one["path"])

    def test_existing_preflight_blocks_unsupported_structure_inventory(self):
        db = sample_db()
        db.structure_inventory = {"state": "INCOMPLETE", "reason": "Unsupported alternative culvert"}
        _SESSIONS["cad-block"] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop("cad-block", None))
        with self.assertRaises(HTTPException) as raised:
            export_cad_review(CadReviewPackageOptions(
                session_id="cad-block", panel_numbers=[4], profile_names=["profile-a"]))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unsupported alternative culvert", raised.exception.detail)

    def test_world_file_uses_nonuniform_pixel_scales_and_centers(self):
        bounds = CadReviewBounds(1000.0, 6500.0, 5579.72, 5624.72)
        values = [float(v) for v in bounds.world_file(3900, 2700).splitlines()]
        self.assertEqual(values[1:3], [0.0, 0.0])
        self.assertAlmostEqual(values[0], 5500.0 / 3900)
        self.assertAlmostEqual(values[3], -45.0 / 2700)
        self.assertAlmostEqual(values[4], 1000.0 + values[0] / 2)
        self.assertAlmostEqual(values[5], 5624.72 + values[3] / 2)

    def test_shared_definition_drives_r12_grid_annotations_and_png_alignment(self):
        db = sample_db()
        db.header.vertical_datum = "NAVD88"
        db.header.datum_provenance = "SOURCE"
        db.flooding_source = {"display_name": "Validated Source"}
        db.panels = [PanelDef(27, 0., 100., 90., 110., h_scale=100., v_scale=5.)]
        for row in db.working[3:]:
            row.river, row.reach = "Tributary", "Lower Reach"
        db.landmarks = [Landmark(9, 50., 103., "Railroad", orientation="Vertical", adjusted=True)]
        db.profiles[0].backwater_elev = 101.
        db.profiles[0].backwater_text = "Reviewed reference"
        target = Path(self.temp.name) / "shared.zip"
        config = DrawingBuildConfig(panel_suffix="P")
        result = export_cad_review_package(
            target, db, db.profiles, db.panels[0], (0., 100.), drawing_config=config)
        drawing = build_engineering_drawing(
            db, db.panels, ["profile-a"], revision=(0., 100.), config=config)
        panel = drawing.panels[0]

        with zipfile.ZipFile(target) as archive:
            self.assertEqual(set(archive.namelist()), {
                "CAD_Review_Panel_27.dxf", "CAD_Review_Panel_27.png", "CAD_Review_Panel_27.pgw",
                CAD_REVIEW_MANIFEST})
            dxf_data = archive.read("CAD_Review_Panel_27.dxf")
            png_data = archive.read("CAD_Review_Panel_27.png")
            world_data = archive.read("CAD_Review_Panel_27.pgw")
        dxf_path = Path(self.temp.name) / "shared.dxf"
        png_path = Path(self.temp.name) / "shared.png"
        dxf_path.write_bytes(dxf_data)
        png_path.write_bytes(png_data)
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        self.assertEqual(doc.dxfversion, "AC1009")
        self.assertEqual(len(msp.query("LWPOLYLINE")), 0)
        self.assertTrue(set(entity.dxftype() for entity in msp).issubset({"POLYLINE", "POINT", "TEXT"}))

        expected_polylines = {
            polyline.identity: [(point.x, point.y) for point in polyline.points]
            for polyline in panel.polylines
        }
        for polyline in msp.query("POLYLINE"):
            vertices = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in polyline.vertices]
            matches = [value for value in expected_polylines.values() if value == vertices]
            if polyline.dxf.layer not in {"GRID_MAJOR", "GRID_MINOR", "BACKWATER_USER"}:
                self.assertTrue(matches, (polyline.dxf.layer, vertices))

        expected_grid = {"GRID_MAJOR": set(), "GRID_MINOR": set()}
        for grid_line in panel.grid_lines:
            if grid_line.axis == "STATION":
                points = [(grid_line.coordinate, 90.), (grid_line.coordinate, 110.)]
            else:
                points = [(0., grid_line.coordinate), (100., grid_line.coordinate)]
            expected_grid["GRID_MAJOR" if grid_line.major else "GRID_MINOR"].add(tuple(points))
        for layer, expected in expected_grid.items():
            actual = {
                tuple((vertex.dxf.location.x, vertex.dxf.location.y) for vertex in polyline.vertices)
                for polyline in msp.query(f'POLYLINE[layer=="{layer}"]')
            }
            self.assertEqual(actual, expected)

        point_anchors = {(point.dxf.location.x, point.dxf.location.y)
                         for point in msp.query('POINT[layer=="CROSS_SECTIONS"]')}
        self.assertIn((0., 96.), point_anchors)
        landmark = next(text for text in msp.query('TEXT[layer=="LANDMARK"]')
                        if text.dxf.text == "Railroad")
        self.assertEqual(landmark.dxf.rotation, 90.)
        self.assertEqual((landmark.dxf.insert.x, landmark.dxf.insert.y), (50., 103.))
        profile_label = next(text for text in msp.query('TEXT[layer=="PROFILE_LABELS"]'))
        self.assertEqual(profile_label.dxf.text, "1%")
        self.assertEqual((profile_label.dxf.insert.x, profile_label.dxf.insert.y), (100., 104.))
        metadata = next(text for text in msp.query('TEXT[layer=="PANEL_METADATA"]'))
        self.assertIn("PANEL 27P", metadata.dxf.text)
        self.assertIn("VERTICAL DATUM: NAVD88 (SOURCE)", metadata.dxf.text)
        self.assertEqual(result["warnings"], ())

        world = [float(value) for value in world_data.decode("ascii").splitlines()]
        width, height = result["width_px"], result["height_px"]
        for x, y in ((0., 96.), (25., 98.), (50., 103.), (100., 104.), (50., 100.)):
            column = (x - world[4]) / world[0]
            row = (y - world[5]) / world[3]
            nearest_column = max(0, min(width - 1, round(column)))
            nearest_row = max(0, min(height - 1, round(row)))
            self.assertLessEqual(abs(world[4] + nearest_column * world[0] - x), abs(world[0]) / 2 + 1e-9)
            self.assertLessEqual(abs(world[5] + nearest_row * world[3] - y), abs(world[3]) / 2 + 1e-9)

        with Image.open(png_path) as image:
            self.assertEqual(image.size, (width, height))
            pixels = image.convert("RGB")
            def nearby_matches(x, y, matches):
                column = round((x - world[4]) / world[0])
                row = round((y - world[5]) / world[3])
                return any(matches(pixels.getpixel((cx, cy)))
                           for cy in range(max(0, row - 2), min(height, row + 3))
                           for cx in range(max(0, column - 2), min(width, column + 3)))
            self.assertTrue(nearby_matches(25., 98., lambda rgb: rgb[0] > 80 and rgb[0] > rgb[1] * 1.5))
            self.assertTrue(nearby_matches(50., 94., lambda rgb: max(rgb) < 80))
            self.assertTrue(nearby_matches(50., 100., lambda rgb: max(rgb) < 180))
            self.assertTrue(nearby_matches(40., 94.5, lambda rgb: 190 < min(rgb) < 245))

    def test_revision_clips_shared_grid_and_preserves_aligned_panel_set(self):
        db = sample_db()
        panel = db.panels[0]
        target = Path(self.temp.name) / "clipped.zip"
        result = export_cad_review_package(target, db, db.profiles, panel, (10., 20.))
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), [
                "CAD_Review_Panel_4.dxf", "CAD_Review_Panel_4.png", "CAD_Review_Panel_4.pgw",
                CAD_REVIEW_MANIFEST])
            archive.extract("CAD_Review_Panel_4.dxf", self.temp.name)
        doc = ezdxf.readfile(Path(self.temp.name) / "CAD_Review_Panel_4.dxf")
        grid_polylines = list(doc.modelspace().query('POLYLINE[layer=="GRID_MAJOR"]'))
        grid_polylines += list(doc.modelspace().query('POLYLINE[layer=="GRID_MINOR"]'))
        for line in grid_polylines:
            for vertex in line.vertices:
                self.assertTrue(10. - 1e-9 <= vertex.dxf.location.x <= 20. + 1e-9)
                self.assertTrue(90. - 1e-9 <= vertex.dxf.location.y <= 110. + 1e-9)
        self.assertEqual((result["bounds"].x_min, result["bounds"].x_max), (10., 20.))
        self.assertEqual(result["files"], [
            "CAD_Review_Panel_4.dxf", "CAD_Review_Panel_4.png", "CAD_Review_Panel_4.pgw",
            CAD_REVIEW_MANIFEST])

    def test_two_and_ten_panel_packages_have_stable_sets_and_manifest(self):
        for count in (2, 10):
            db = sample_db()
            panels = [PanelDef(number, 0., 100., 90., 110., h_scale=100., v_scale=5.,
                               panel_suffix="P" if number == 2 else "")
                      for number in range(1, count + 1)]
            db.panels = panels
            target = Path(self.temp.name) / f"package-{count}.zip"
            result = export_cad_review_package(
                target, db, db.profiles, panels, (0., 100.))
            self.assertEqual(result["panel_count"], count)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                expected = {CAD_REVIEW_MANIFEST}
                for number in range(1, count + 1):
                    expected.update(f"CAD_Review_Panel_{number}.{suffix}"
                                    for suffix in ("dxf", "png", "pgw"))
                self.assertEqual(set(archive.namelist()), expected)
                manifest = json.loads(archive.read(CAD_REVIEW_MANIFEST))
                self.assertEqual(manifest["schema"], "rasplot-cad-review-package-v1")
                self.assertEqual(len(manifest["panels"]), count)
                self.assertEqual(manifest["coordinate_contract"]["crs"],
                                 {"status": "UNKNOWN_NOT_DECLARED", "name": None})
                self.assertEqual(manifest["datum"]["label"], "UNKNOWN")
                for item in manifest["panels"]:
                    png_name, pgw_name = item["files"]["png"], item["files"]["pgw"]
                    png_path = Path(self.temp.name) / f"{count}-{png_name}"
                    png_path.write_bytes(archive.read(png_name))
                    with Image.open(png_path) as image:
                        self.assertEqual(list(image.size), [item["raster"]["width_px"],
                                                           item["raster"]["height_px"]])
                    world = [float(value) for value in archive.read(pgw_name).decode().splitlines()]
                    self.assertEqual(world, item["world_file_affine"])

    def test_selected_subset_excludes_other_saved_panels(self):
        db = sample_db()
        db.panels = [PanelDef(number, 0., 100., 90., 110., h_scale=100., v_scale=5.)
                     for number in (4, 7, 9)]
        _SESSIONS["cad-subset"] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop("cad-subset", None))
        result = export_cad_review(CadReviewPackageOptions(
            session_id="cad-subset", panel_numbers=[4, 9], profile_names=["profile-a"]))
        self.assertEqual(result["panel_count"], 2)
        with zipfile.ZipFile(result["path"]) as archive:
            names = set(archive.namelist())
            self.assertIn("CAD_Review_Panel_4.dxf", names)
            self.assertIn("CAD_Review_Panel_9.dxf", names)
            self.assertFalse(any("Panel_7" in name for name in names))

    def test_cancellation_does_not_finalize_partial_package(self):
        db = sample_db()
        _SESSIONS["cad-cancel"] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop("cad-cancel", None))
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise ExportCancelled("cancelled in test")
        from backend.app.routers import report
        before = set(Path(report._cache_dir("cad-cancel")).glob("*"))
        with self.assertRaises(ExportCancelled):
            report._export_cad_review_snapshot(CadReviewPackageOptions(
                session_id="cad-cancel", panel_numbers=[4], profile_names=["profile-a"]),
                db, cancel_check=cancel)
        after = set(Path(report._cache_dir("cad-cancel")).glob("*"))
        self.assertEqual(after, before)

    def test_background_multipanel_job_finalizes_owned_zip(self):
        db = sample_db()
        db.panels = [PanelDef(number, 0., 100., 90., 110., h_scale=100., v_scale=5.)
                     for number in (4, 5)]
        _SESSIONS["cad-background"] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop("cad-background", None))
        started = start_cad_review_export(CadReviewPackageOptions(
            session_id="cad-background", panel_numbers=[4, 5],
            profile_names=["profile-a"]))
        for _ in range(500):
            job = get_export_job("cad-background", started["job_id"])
            if job.state in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(.01)
        self.assertEqual(job.state, "COMPLETED", job.error)
        self.assertEqual(job.result["panel_count"], 2)
        self.assertTrue(owned_path("cad-background", job.result["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
