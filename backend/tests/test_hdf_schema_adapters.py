import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
import ezdxf

from backend.app.core.hdf_reader import AlignmentError, HdfReader
from backend.app.core.dxf_export import export_dxf
from backend.app.core.rasplot_db import PanelDef, build_db_from_plan
from backend.app.core.state_contract import review_panels
from backend.app.routers.report import render_profile_pdf_from_db
from backend.app.core.hdf_schema_adapters import (
    DatasetSignature,
    HdfSchemaAdapter,
    HdfSchemaAdapterRegistry,
    REGISTRY,
    SchemaAdapterError,
    POST_ROOT,
    STEADY_ROOT,
    UNSTEADY_ROOT,
)


GEOMETRY_DTYPE = [
    ("River", "S20"), ("Reach", "S20"), ("RS", "S20"),
    ("Len Channel", "f8"), ("Left Bank", "f8"), ("Right Bank", "f8"),
]
RESULT_DTYPE = [("River", "S20"), ("Reach", "S20"), ("Station", "S20")]
MUNCIE = Path(__file__).resolve().parents[2].parent / "Muncie/Muncie.p04.hdf"


class SchemaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def fixture(self, name="fixture.p01.hdf", *, result="steady", identity="explicit",
                version="arbitrary-not-a-selector"):
        path = Path(self.temp.name) / name
        with h5py.File(path, "w") as handle:
            handle.attrs["Units System"] = "US Customary"
            handle.attrs["File Version"] = version
            geometry = handle.create_group("Geometry/Cross Sections")
            geometry["Attributes"] = np.array([
                (b"River", b"A", b"100", 20.0, 0.0, 1.0),
                (b"River", b"B", b"100", 0.0, 0.0, 1.0),
            ], dtype=GEOMETRY_DTYPE)
            geometry["Station Elevation Info"] = np.array([[0, 2], [2, 2]])
            geometry["Station Elevation Values"] = np.array(
                [(0.0, 10.0), (1.0, 11.0), (0.0, 20.0), (1.0, 21.0)])
            root = UNSTEADY_ROOT if result == "unsteady" else STEADY_ROOT
            output = handle.create_group(root)
            if result == "steady":
                output["Profile Names"] = np.array([b"Design"])
            cross_sections = output.create_group("Cross Sections")
            cross_sections["Water Surface"] = np.array([[12.0, 22.0]])
            identities = np.array([(b"River", b"A", b"100"),
                                   (b"River", b"B", b"100")], dtype=RESULT_DTYPE)
            if identity == "explicit":
                cross_sections["Cross Section Attributes"] = identities
            elif identity == "legacy":
                handle["Results/Steady/Output/Geometry Info/Cross Section Only"] = np.array(
                    [b"River A 100", b"River B 100"])
        return path

    def test_explicit_steady_adapter_uses_signature_not_version(self):
        plan = HdfReader(self.fixture(version="5.0.7-or-99.0-does-not-matter")).read_plan()
        self.assertEqual(plan.capabilities.schema_adapter, "STEADY_BASE_OUTPUT_EXPLICIT_XS_V1")
        self.assertEqual(plan.schema_adapter["identity_strategy"],
                         "EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES")
        self.assertEqual(plan.profiles[0].xs_ids,
                         [("River", "A", "100"), ("River", "B", "100")])

    def test_legacy_adapter_requires_exact_result_side_labels(self):
        plan = HdfReader(self.fixture(identity="legacy")).read_plan()
        self.assertEqual(plan.capabilities.schema_adapter,
                         "STEADY_BASE_OUTPUT_GEOMETRY_INFO_LABELS_V1")
        self.assertEqual(plan.schema_adapter["identity_strategy"],
                         "EXACT_COMBINED_GEOMETRY_INFO_LABEL")

    def test_unsteady_adapter_preserves_result_identity_and_precedence(self):
        path = self.fixture(result="unsteady")
        # A second lower-precedence steady block must not make selection ambiguous.
        with h5py.File(path, "a") as handle:
            steady = handle.create_group(STEADY_ROOT)
            steady["Profile Names"] = np.array([b"Other"])
            cross_sections = steady.create_group("Cross Sections")
            cross_sections["Water Surface"] = np.array([[1.0, 2.0]])
            cross_sections["Cross Section Attributes"] = np.array(
                [(b"River", b"A", b"100"), (b"River", b"B", b"100")],
                dtype=RESULT_DTYPE)
        plan = HdfReader(path).read_plan()
        self.assertEqual(plan.capabilities.schema_adapter,
                         "UNSTEADY_BASE_OUTPUT_EXPLICIT_XS_V1")
        self.assertEqual(plan.profiles[0].result_type, "DERIVED_MAXIMUM_ENVELOPE")

    def test_registry_rejects_ambiguous_verified_signatures(self):
        signature = HdfSchemaAdapter(
            "ONE", "TEST", STEADY_ROOT, f"{STEADY_ROOT}/Cross Sections",
            "EXPLICIT_RESULT_CROSS_SECTION_ATTRIBUTES",
            (DatasetSignature(f"{STEADY_ROOT}/Cross Sections/Water Surface"),),
            (DatasetSignature("Geometry/Cross Sections/Attributes"),),
        )
        duplicate = HdfSchemaAdapter(**{**signature.__dict__, "name": "TWO"})
        path = self.fixture()
        with h5py.File(path, "r") as handle:
            with self.assertRaisesRegex(SchemaAdapterError, "Ambiguous HDF schema"):
                HdfSchemaAdapterRegistry((signature, duplicate)).select(handle, handle)

    def test_unsupported_schema_reports_observed_paths_and_missing_evidence(self):
        path = self.fixture()
        with h5py.File(path, "a") as handle:
            del handle[STEADY_ROOT + "/Cross Sections/Cross Section Attributes"]
        with self.assertRaisesRegex(AlignmentError, "no verified adapter signature matched") as caught:
            HdfReader(path).read_plan()
        self.assertIn("Water Surface", str(caught.exception))
        self.assertIn("Cross Section Attributes", str(caught.exception))

    def test_repeated_station_labels_across_reaches_never_collide(self):
        plan = HdfReader(self.fixture()).read_plan()
        identities = plan.profiles[0].xs_ids
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual({identity[1] for identity in identities}, {"A", "B"})

    def test_coded_but_unvalidated_post_process_layout_remains_blocked(self):
        path = self.fixture()
        with h5py.File(path, "a") as handle:
            del handle["Results/Steady"]
            output = handle.create_group(POST_ROOT)
            cross_sections = output.create_group("Cross Sections")
            cross_sections["Water Surface"] = np.array([[12.0, 22.0]])
            cross_sections["Cross Section Attributes"] = np.array(
                [(b"River", b"A", b"100"), (b"River", b"B", b"100")],
                dtype=RESULT_DTYPE)
        with self.assertRaisesRegex(AlignmentError, "authorized authentic fixture"):
            HdfReader(path).read_plan()

    @unittest.skipUnless(MUNCIE.exists(), "Authentic Muncie post-process fixture unavailable")
    def test_authentic_post_process_geometry_identity_adapter(self):
        path = Path(self.temp.name) / "muncie-post-only.p04.hdf"
        with h5py.File(MUNCIE, "r") as source, h5py.File(path, "w") as target:
            target.attrs["Units System"] = source.attrs["Units System"]
            source.copy("Geometry", target)
            results = target.create_group("Results")
            source.copy("Results/Post Process", results, name="Post Process")
            identity_record = source[
                "Results/Post Process/Steady/Output/Geometry Info/Cross Section Attributes"][0]
            expected_identity = tuple(
                value.decode().strip() if isinstance(value, bytes) else str(value).strip()
                for value in (identity_record["River"], identity_record["Reach"],
                              identity_record["Station"]))
            expected_maximum = float(source[
                "Results/Post Process/Steady/Output/Output Blocks/Base Output/Post Process/"
                "Post Process Profiles/Cross Sections/Water Surface"][0, 0])
        plan = HdfReader(path).read_plan()
        self.assertEqual(plan.capabilities.schema_adapter,
                         "POST_PROCESS_STEADY_GEOMETRY_INFO_XS_V1")
        self.assertEqual(plan.profiles[0].result_type, "SOURCE_AUTHORITATIVE_MAXIMUM")
        self.assertEqual(plan.profiles[0].time_provenance["source_label"], "Max WS")
        value_by_identity = plan.profiles[0].values_by_identity()
        self.assertEqual(value_by_identity[expected_identity], expected_maximum)
        self.assertEqual(len(value_by_identity), 61)

    @unittest.skipUnless(MUNCIE.exists(), "Authentic Muncie post-process fixture unavailable")
    def test_muncie_base_unsteady_output_retains_precedence(self):
        plan = HdfReader(MUNCIE).read_plan()
        self.assertEqual(plan.capabilities.schema_adapter,
                         "UNSTEADY_BASE_OUTPUT_EXPLICIT_XS_V1")
        self.assertNotEqual(plan.profiles[0].result_type,
                            "SOURCE_AUTHORITATIVE_MAXIMUM")

    @unittest.skipUnless(MUNCIE.exists(), "Authentic Muncie post-process fixture unavailable")
    def test_post_process_adapter_requires_explicit_wsel_units(self):
        path = Path(self.temp.name) / "muncie-post-missing-units.p04.hdf"
        with h5py.File(MUNCIE, "r") as source, h5py.File(path, "w") as target:
            target.attrs["Units System"] = source.attrs["Units System"]
            source.copy("Geometry", target)
            results = target.create_group("Results")
            source.copy("Results/Post Process", results, name="Post Process")
        with h5py.File(path, "a") as target:
            dataset = target[
                "Results/Post Process/Steady/Output/Output Blocks/Base Output/Post Process/"
                "Post Process Profiles/Cross Sections/Water Surface"]
            del dataset.attrs["Variable_Unit"]
        with self.assertRaisesRegex(AlignmentError, "WSEL unit metadata"):
            HdfReader(path).read_plan()

    @unittest.skipUnless(MUNCIE.exists(), "Authentic Muncie post-process fixture unavailable")
    def test_post_process_hydraulics_feed_shared_pdf_dxf_drawing_contract(self):
        path = Path(self.temp.name) / "muncie-post-hydraulics-only.p04.hdf"
        with h5py.File(MUNCIE, "r") as source, h5py.File(path, "w") as target:
            target.attrs["Units System"] = source.attrs["Units System"]
            source.copy("Geometry", target)
            results = target.create_group("Results")
            source.copy("Results/Post Process", results, name="Post Process")
        # This derived regression isolates the newly enabled hydraulic schema;
        # it does not claim the authentic Muncie laterals are exportable.
        with h5py.File(path, "a") as target:
            del target["Geometry/Structures"]
            lateral_path = ("Results/Post Process/Steady/Output/Output Blocks/Base Output/"
                            "Post Process/Post Process Profiles/Lateral Structures")
            if lateral_path in target:
                del target[lateral_path]
        plan = HdfReader(path).read_plan()
        db = build_db_from_plan(plan)
        xs = [row for row in db.working if row.kind == "xs"]
        span = max(row.cumdist for row in xs)
        elevations = [value for row in xs for value in
                      (row.minchel, row.profiles[plan.profiles[0].profile_id])
                      if value is not None]
        db.panels = [PanelDef(1, 0.0, span, min(elevations) - 1.0,
                              max(elevations) + 1.0,
                              h_scale=max(1.0, span / 13.0),
                              v_scale=max(1.0, (max(elevations) - min(elevations) + 2.0) / 9.0))]
        review_panels(db)
        pdf = Path(self.temp.name) / "post-process.pdf"
        dxf = Path(self.temp.name) / "post-process.dxf"
        render_profile_pdf_from_db(str(pdf), db)
        export_dxf(dxf, db)
        self.assertGreater(pdf.stat().st_size, 1_000)
        document = ezdxf.readfile(dxf)
        profile_entities = [entity for entity in document.modelspace()
                            if entity.dxftype() == "LWPOLYLINE"
                            and str(entity.dxf.layer).startswith("PROF_")]
        self.assertTrue(profile_entities)
        self.assertEqual(document.header["$INSUNITS"], 1)
        self.assertTrue(any(row.profiles[plan.profiles[0].profile_id] is not None for row in xs))


if __name__ == "__main__":
    unittest.main()

