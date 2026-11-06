import math
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import ezdxf
import h5py
import numpy as np
from pypdf import PdfReader

from backend.app.core.engineering_units import (
    EngineeringUnitContract,
    FEET_PER_METER,
    LengthQuantity,
    LengthUnit,
    UnitContractError,
    UnitSystem,
    convert_length,
)
from backend.app.core.dxf_export import export_dxf
from backend.app.core.engineering_drawing import build_engineering_drawing
from backend.app.core.hdf_reader import (CrossSection, HdfReader, ProfileResult,
                                         Structure, Plan, ReachGeometry,
                                         apply_engineering_unit_contract)
from backend.app.core.hydraulics import prepare_profiles
from backend.app.core.model_topology import build_topology
from backend.app.core.structure_model import models_from_rows
from backend.app.core.rasplot_db import PanelDef, auto_panels, build_db_from_plan
from backend.app.core.rasplot_db import fix_drawdowns
from backend.app.core.model_topology import ModelContext
from backend.app.core.project_file import fingerprint_source, serialize_project_document, deserialize_project_document
from backend.app.core.project_restore import build_project_document, restore_candidate_session
from backend.app.core.state_contract import review_panels
from backend.app.routers.report import render_profile_pdf_from_db
from backend.app.routers import hecras_api


class EngineeringUnitContractTests(unittest.TestCase):
    def test_us_source_is_explicitly_feet(self):
        contract = EngineeringUnitContract.from_source_system("US Customary")
        self.assertEqual(contract.source_system, UnitSystem.US_CUSTOMARY)
        self.assertEqual(contract.source_length_unit, LengthUnit.FOOT)
        self.assertEqual(contract.source_to_working(123.25), 123.25)

    def test_si_source_converts_to_validated_working_feet(self):
        contract = EngineeringUnitContract.from_source_system("SI")
        self.assertAlmostEqual(contract.source_to_working(1.0), FEET_PER_METER, places=12)
        self.assertAlmostEqual(contract.working_to_source(FEET_PER_METER), 1.0, places=12)

    def test_round_trip_is_stable(self):
        for system in ("US Customary", "SI"):
            contract = EngineeringUnitContract.from_source_system(system)
            for value in (-12.75, 0.0, 987654.321):
                self.assertAlmostEqual(
                    contract.working_to_source(contract.source_to_working(value)), value, places=9)

    def test_paper_inches_are_separate_from_engineering_units(self):
        contract = EngineeringUnitContract.from_source_system("US Customary")
        self.assertEqual(contract.working_to_paper(6500.0, 500.0), 13.0)
        self.assertEqual(convert_length(1.0, LengthUnit.FOOT, LengthUnit.PAPER_INCH), 12.0)

    def test_point_conversion_applies_to_both_axes(self):
        contract = EngineeringUnitContract.from_source_system("SI")
        converted = contract.source_points_to_working(((1.0, 2.0), (3.0, 4.0)))
        self.assertEqual(len(converted), 2)
        self.assertAlmostEqual(converted[0][0], FEET_PER_METER)
        self.assertAlmostEqual(converted[0][1], 2 * FEET_PER_METER)

    def test_manifest_preserves_all_unit_spaces(self):
        manifest = EngineeringUnitContract.from_source_system("SI").to_manifest()
        self.assertEqual(manifest, {
            "contract_version": "rasplot-length-units-v1",
            "source_system": "SI",
            "source_length_unit": "m",
            "working_length_unit": "ft",
            "paper_length_unit": "paper_in",
        })

    def test_unknown_or_unrecognized_units_fail_closed(self):
        with self.assertRaisesRegex(UnitContractError, "UNKNOWN"):
            EngineeringUnitContract.from_source_system("UNKNOWN")
        with self.assertRaisesRegex(UnitContractError, "Unsupported"):
            EngineeringUnitContract.from_source_system("Metric-ish")

    def test_nonfinite_and_invalid_scale_are_rejected(self):
        contract = EngineeringUnitContract.from_source_system("US Customary")
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(UnitContractError):
                LengthQuantity(value, LengthUnit.FOOT)
        with self.assertRaises(UnitContractError):
            contract.working_to_paper(1.0, 0.0)

    def test_structure_dimensions_convert_once_and_preserve_source(self):
        opening = {"Rise": 2.0, "Span": 3.0, "Length": 30.0,
                   "Invert US": 90.0, "Invert DS": 89.0,
                   "barrel_records": [{"US Station": 4.0, "DS Station": 5.0}]}
        structure = Structure("River", "Reach", "150", "Culvert", "", 2.0, 8.0,
                              low_chord=90.0, high_chord=95.0,
                              profile_data=[(0.0, 90.0), (10.0, 95.0)],
                              openings=[opening])
        plan = Plan("SI", Path("synthetic.p01.hdf"), None, None, None, None,
                    source_units="SI", reaches=[ReachGeometry("River", "Reach", [], [], [structure])])
        apply_engineering_unit_contract(plan, EngineeringUnitContract.from_source_system("SI"))
        self.assertAlmostEqual(structure.upstream_distance, 2.0 * FEET_PER_METER)
        self.assertAlmostEqual(structure.openings[0]["Rise"], 2.0 * FEET_PER_METER)
        self.assertAlmostEqual(structure.openings[0]["barrel_records"][0]["US Station"], 4.0 * FEET_PER_METER)
        self.assertEqual(structure.source_openings[0]["Rise"], 2.0)
        with self.assertRaisesRegex(Exception, "double-convert"):
            apply_engineering_unit_contract(plan, EngineeringUnitContract.from_source_system("SI"))

    def test_si_bridge_chords_and_longitudinal_faces_use_converted_geometry(self):
        sections = [
            CrossSection("River", "Reach", "200", "", 100.0, 0.0, 10.0,
                         station_elevation=[(0.0, 90.0), (10.0, 91.0)], minchel=90.0),
            CrossSection("River", "Reach", "100", "", 0.0, 0.0, 10.0,
                         station_elevation=[(0.0, 89.0), (10.0, 90.0)], minchel=89.0),
        ]
        bridge = Structure("River", "Reach", "150", "Bridge", "Metric bridge",
                           10.0, 20.0, us_rs="200", ds_rs="100", source_id="bridge-si")
        bridge.source_attributes = {
            "US Type": "XS", "US River": "River", "US Reach": "Reach", "US RS": "200",
            "DS Type": "XS", "DS River": "River", "DS Reach": "Reach", "DS RS": "100",
        }
        bridge.source_profiles = {
            "US BR Weir Profile": [(0.0, 98.0), (10.0, 99.0)],
            "US BR Lid Profile": [(2.0, 94.0), (8.0, 95.0)],
            "DS BR Weir Profile": [(0.0, 97.0), (10.0, 98.0)],
            "DS BR Lid Profile": [(2.0, 93.0), (8.0, 94.0)],
        }
        reach = ReachGeometry("River", "Reach", [], sections, [bridge])
        profile = ProfileResult("Design", [96.0, 95.0], xs_ids=[item.identity for item in sections],
                                profile_id="metric-profile", key="metric-profile",
                                source_path="synthetic", source_dataset="WSEL",
                                values=[{"value": 96.0, "state": "VALID", "raw": 96.0},
                                        {"value": 95.0, "state": "VALID", "raw": 95.0}])
        prepare_profiles([profile])
        plan = Plan("SI bridge", Path("synthetic-si.p01.hdf"), None, None, None, None,
                    profiles=[profile], reaches=[reach], source_units="SI", model_id="si-bridge")
        apply_engineering_unit_contract(plan, EngineeringUnitContract.from_source_system("SI"))
        plan.topology = build_topology(plan.model_id, plan.reaches, [], "synthetic-si.p01.hdf")
        database = build_db_from_plan(plan)
        model = models_from_rows(database.working)[0]
        self.assertEqual(model.status, "VALIDATED")
        self.assertAlmostEqual(model.faces[0]["station"], 90.0 * FEET_PER_METER)
        self.assertAlmostEqual(model.faces[1]["station"], 70.0 * FEET_PER_METER)
        self.assertAlmostEqual(model.faces[0]["low_chord"], 94.0 * FEET_PER_METER)
        self.assertAlmostEqual(model.faces[0]["high_chord"], 99.0 * FEET_PER_METER)
        self.assertEqual(bridge.source_profiles_raw["US BR Lid Profile"][0], [2.0, 94.0])

        # The drawing boundary consumes canonical feet, so a physically
        # equivalent US plan must produce identical PDF/DXF structure geometry.
        us_plan = deepcopy(plan)
        us_plan.name = "US bridge equivalent"
        us_plan.model_id = "us-bridge-equivalent"
        us_plan.source_units = "US Customary"
        us_plan.unit_contract = EngineeringUnitContract.from_source_system("US Customary").to_manifest()
        si_db, us_db = database, build_db_from_plan(us_plan)
        panel = PanelDef(1, 0.0, 400.0, 280.0, 340.0, h_scale=50.0, v_scale=10.0)
        drawings = []
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            for label, current in (("si", si_db), ("us", us_db)):
                current.panels = [panel]
                review_panels(current)
                drawing = build_engineering_drawing(current, [panel], [current.profiles[0].profile_id])
                drawings.append([[(point.x, point.y) for point in line.points]
                                 for line in drawing.panels[0].polylines
                                 if line.kind == "STRUCTURE_FACE"])
                dxf = Path(directory) / f"{label}.dxf"
                pdf = Path(directory) / f"{label}.pdf"
                export_dxf(dxf, current, panels=[panel])
                render_profile_pdf_from_db(str(pdf), current, panels=[panel])
                document = ezdxf.readfile(dxf)
                lines = [((entity.dxf.start.x, entity.dxf.start.y),
                          (entity.dxf.end.x, entity.dxf.end.y))
                         for entity in document.modelspace().query('LINE[layer=="STRUCT_BRIDGE"]')]
                page = PdfReader(str(pdf)).pages[0]
                outputs.append((lines, float(page.mediabox.width), float(page.mediabox.height)))
            self.assertEqual(drawings[0], drawings[1])
            self.assertEqual(outputs[0], outputs[1])


class SiEndToEndTests(unittest.TestCase):
    GEOM_DTYPE = [("River", "S24"), ("Reach", "S24"), ("RS", "S24"),
                  ("Len Channel", "f8"), ("Left Bank", "f8"), ("Right Bank", "f8")]
    RESULT_DTYPE = [("River", "S24"), ("Reach", "S24"), ("Station", "S24")]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fixture(self, name: str, system: str, factor: float) -> Path:
        path = Path(self.temp.name) / name
        rows = [(b"River", b"Reach", b"300", 100.0 * factor, 0.0, 10.0 * factor),
                (b"River", b"Reach", b"200", 100.0 * factor, 0.0, 10.0 * factor),
                (b"River", b"Reach", b"100", 0.0, 0.0, 10.0 * factor)]
        elevations = [(0.0, 92.0), (10.0, 93.0),
                      (0.0, 91.0), (10.0, 92.0),
                      (0.0, 90.0), (10.0, 91.0)]
        with h5py.File(path, "w") as handle:
            handle.attrs["Units System"] = system
            geometry = handle.create_group("Geometry/Cross Sections")
            geometry["Attributes"] = np.array(rows, dtype=self.GEOM_DTYPE)
            geometry["Station Elevation Info"] = np.array([[0, 2], [2, 2], [4, 2]])
            geometry["Station Elevation Values"] = np.array(
                [(station * factor, elevation * factor) for station, elevation in elevations])
            results = handle.create_group(
                "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles")
            results["Profile Names"] = np.array([b"Design"])
            cross_sections = results.create_group("Cross Sections")
            cross_sections["Water Surface"] = np.array([[95.0 * factor, 94.0 * factor, 96.0 * factor]])
            cross_sections["Cross Section Attributes"] = np.array(
                [(b"River", b"Reach", b"300"), (b"River", b"Reach", b"200"),
                 (b"River", b"Reach", b"100")],
                dtype=self.RESULT_DTYPE)
        return path

    def test_mixed_plan_and_geometry_units_fail_closed(self):
        plan_path = Path(self.temp.name) / "mixed.p01.hdf"
        geometry_path = Path(self.temp.name) / "mixed.g01.hdf"
        with h5py.File(plan_path, "w") as handle:
            handle.attrs["Units System"] = "SI"
            info = handle.create_group("Plan Data/Plan Information")
            info.attrs["Geometry Filename"] = "mixed.g01"
        with h5py.File(geometry_path, "w") as handle:
            handle.attrs["Units System"] = "US Customary"
            handle.create_group("Geometry")
        with self.assertRaisesRegex(Exception, "conflict"):
            HdfReader(plan_path).read_plan()

    def test_si_companion_geometry_requires_unit_metadata(self):
        plan_path = Path(self.temp.name) / "missing-unit.p01.hdf"
        geometry_path = Path(self.temp.name) / "missing-unit.g01.hdf"
        with h5py.File(plan_path, "w") as handle:
            handle.attrs["Units System"] = "SI"
            info = handle.create_group("Plan Data/Plan Information")
            info.attrs["Geometry Filename"] = "missing-unit.g01"
        with h5py.File(geometry_path, "w") as handle:
            handle.create_group("Geometry")
        with self.assertRaisesRegex(Exception, "no Units System metadata"):
            HdfReader(plan_path).read_plan()

    def test_si_and_physically_equivalent_us_models_match_pdf_and_dxf_geometry(self):
        si_path = self.fixture("si.p01.hdf", "SI Units", 1.0)
        us_path = self.fixture("us.p01.hdf", "US Customary", FEET_PER_METER)
        si_plan, us_plan = HdfReader(si_path).read_plan(), HdfReader(us_path).read_plan()
        self.assertEqual(si_plan.source_units, "SI")
        self.assertEqual(si_plan.unit_conversion_status, "COMPLETE")
        self.assertEqual(si_plan.reaches[0].cross_sections[0].source_engineering["length_channel"], 100.0)
        si_db, us_db = build_db_from_plan(si_plan), build_db_from_plan(us_plan)
        self.assertEqual(si_db.working_units, "INTERNAL_US_CUSTOMARY")
        self.assertEqual(si_db.unit_contract["source_length_unit"], "m")
        for left, right in zip(si_db.working, us_db.working):
            self.assertAlmostEqual(left.length_chnl, right.length_chnl, places=9)
            self.assertAlmostEqual(left.cumdist, right.cumdist, places=9)
            self.assertAlmostEqual(left.minchel, right.minchel, places=9)
            self.assertAlmostEqual(left.profiles["steady-row-0"], right.profiles["steady-row-0"], places=9)
        si_auto = auto_panels(si_db.working, h_scale=55.0, v_scale=10.0)
        us_auto = auto_panels(us_db.working, h_scale=55.0, v_scale=10.0)
        self.assertEqual(len(si_auto), len(us_auto))
        for si_panel, us_panel in zip(si_auto, us_auto):
            for field in ("start_station", "end_station", "start_elevation",
                          "end_elevation", "h_scale", "v_scale"):
                self.assertAlmostEqual(getattr(si_panel, field), getattr(us_panel, field), places=8)
        panel = PanelDef(1, 0.0, 700.0, 280.0, 330.0, h_scale=55.0, v_scale=10.0)
        for database in (si_db, us_db):
            database.panels = [panel]
            review_panels(database)
        si_drawing = build_engineering_drawing(si_db, [panel], ["steady-row-0"])
        us_drawing = build_engineering_drawing(us_db, [panel], ["steady-row-0"])
        si_geometry = [(line.kind, [(point.x, point.y) for point in line.points])
                       for line in si_drawing.panels[0].polylines]
        us_geometry = [(line.kind, [(point.x, point.y) for point in line.points])
                       for line in us_drawing.panels[0].polylines]
        self.assertEqual(len(si_geometry), len(us_geometry))
        for (si_kind, si_points), (us_kind, us_points) in zip(si_geometry, us_geometry):
            self.assertEqual(si_kind, us_kind)
            for si_point, us_point in zip(si_points, us_points):
                self.assertAlmostEqual(si_point[0], us_point[0], places=8)
                self.assertAlmostEqual(si_point[1], us_point[1], places=8)
        pdf_path = Path(self.temp.name) / "si.pdf"
        dxf_path = Path(self.temp.name) / "si.dxf"
        render_profile_pdf_from_db(str(pdf_path), si_db, panels=[panel])
        export_dxf(dxf_path, si_db, panels=[panel])
        page = PdfReader(str(pdf_path)).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), 17.0 * 72.0, places=3)
        self.assertAlmostEqual(float(page.mediabox.height), 11.0 * 72.0, places=3)
        document = ezdxf.readfile(dxf_path)
        self.assertEqual(document.header["$INSUNITS"], 1)
        profile_entities = [entity for entity in document.modelspace().query("LWPOLYLINE")
                            if entity.dxf.layer.startswith("PROF_")]
        self.assertTrue(profile_entities)
        xs = [point[0] for point in profile_entities[0].get_points("xy")]
        self.assertAlmostEqual(max(xs) - min(xs), (200.0 * FEET_PER_METER) / 55.0, places=7)

    def test_si_edit_drawdown_revision_panel_and_save_reopen_workflow(self):
        path = self.fixture("workflow-si.p01.hdf", "SI", 1.0)
        plan = HdfReader(path).read_plan()
        db = build_db_from_plan(plan)
        fix_drawdowns(db.working)
        self.assertEqual(len(db.working[0].adjustments), 1)
        self.assertEqual(len(db.working[1].adjustments), 1)
        self.assertAlmostEqual(db.working[0].raw_profiles["steady-row-0"], 95.0 * FEET_PER_METER)
        extent = (0.0, 200.0 * FEET_PER_METER)
        db.header.revision_start_station, db.header.revision_end_station = extent
        db.panels = [PanelDef(7, extent[0], extent[1], 280.0, 330.0,
                              h_scale=100.0, v_scale=10.0, panel_suffix="SI")]
        review_panels(db)
        source = {
            "plan": plan, "engineering_plan": plan, "db": db, "reach_idx": 0,
            "path": str(path), "source_fingerprint": fingerprint_source(path),
            "model_context": ModelContext(plan.model_id, 1, "si-source", str(path), "ACTIVE"),
        }
        project_path = Path(self.temp.name) / "si-workflow.rasplot.json"
        document = build_project_document(source, project_path)
        self.assertEqual(document.engineering_units.source_system, "SI")
        encoded = serialize_project_document(document)
        document = deserialize_project_document(encoded)
        restored_plan = HdfReader(path).read_plan()
        candidate = {
            "plan": restored_plan, "engineering_plan": restored_plan,
            "db": build_db_from_plan(restored_plan), "reach_idx": 0,
            "path": str(path), "source_fingerprint": fingerprint_source(path),
            "model_context": ModelContext(restored_plan.model_id, 2, "si-restored", str(path), "CANDIDATE"),
        }
        hecras_api._SESSIONS["si-restored"] = candidate
        self.addCleanup(lambda: hecras_api._SESSIONS.pop("si-restored", None))
        warnings = restore_candidate_session("si-restored", document)
        restored = hecras_api._SESSIONS["si-restored"]["db"]
        self.assertEqual(warnings, [])
        self.assertEqual(restored.source_units, "SI")
        self.assertEqual(restored.unit_contract, db.unit_contract)
        self.assertEqual(restored.panel_review_state, "REVIEWED")
        self.assertEqual(restored.panels[0].display_id, "7SI")
        self.assertEqual(restored.header.revision_start_station, extent[0])
        self.assertEqual(restored.header.revision_end_station, extent[1])
        self.assertAlmostEqual(restored.working[0].profiles["steady-row-0"], 96.0 * FEET_PER_METER)


if __name__ == "__main__":
    unittest.main()

