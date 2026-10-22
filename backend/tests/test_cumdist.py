from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ezdxf
from matplotlib.axes import Axes
from backend.app.core.hdf_reader import CrossSection, HdfReader, ReachGeometry, Structure
from backend.app.core.merge import compute_cumdist_from_lengths
from backend.app.core.rasplot_db import build_working_from_plan, recalc_cumdist
from backend.app.routers.hecras_api import (
    OpenRequest, get_db, get_panels, get_profile, open_source,
)
from backend.app.routers.report import ExportOptions, export_profile


ROOT = Path(__file__).resolve().parents[2]
MUNCIE_HDF = ROOT.parent / "Muncie" / "Muncie.p04.hdf"
NORTH_HDF = ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"


def xs(rs: str, length: float, index: int) -> CrossSection:
    return CrossSection(
        river="White", reach="Muncie", rs=rs, name="",
        length_channel=length, left_bank=0.0, right_bank=1.0,
        minchel=900.0 + index, index=index,
    )


def lateral(rs: str, us_rs: str, index: int) -> Structure:
    return Structure(
        river="White", reach="Muncie", rs=rs, type="Lateral",
        name=f"Lateral {rs}", upstream_distance=0.0, weir_width=20.0,
        index=index, us_rs=us_rs,
        profile_data=[(0.0, 950.0), (100.0, 950.0)],
        hw_connectivity=[(us_rs, -10.0)],
    )


def plan_for(cross_sections, structures=()):
    reach = ReachGeometry(
        river="White", reach="Muncie", centerline=[],
        cross_sections=list(cross_sections), structures=list(structures),
    )
    return SimpleNamespace(
        reaches=[reach], profiles=[], plan_path=Path("missing.p01.hdf"),
    )


class CanonicalCumdistTests(unittest.TestCase):
    def test_upstream_xs_owns_interval(self):
        self.assertEqual(compute_cumdist_from_lengths([12.5, 999.0]), [12.5, 0.0])

    def test_fresh_load_matches_recalculation_for_every_xs(self):
        plan = plan_for([xs("300", 30.0, 0), xs("200", 20.0, 1), xs("100", 999.0, 2)])
        rows = build_working_from_plan(plan)
        fresh = [row.cumdist for row in rows if row.kind == "xs"]
        recalc_cumdist(rows)
        recalculated = [row.cumdist for row in rows if row.kind == "xs"]
        self.assertEqual(fresh, [50.0, 20.0, 0.0])
        self.assertEqual(fresh, recalculated)

    def test_lateral_widths_do_not_add_distance(self):
        cross_sections = [
            xs("300", 30.0, 0), xs("200", 20.0, 1), xs("100", 999.0, 2),
        ]
        plan = plan_for(
            cross_sections,
            [lateral("290", "300", 0), lateral("190", "200", 1)],
        )
        rows = build_working_from_plan(plan)
        lateral_rows = [row for row in rows if row.struc == "LATERAL"]
        self.assertEqual([row.length_chnl for row in lateral_rows], [0.0, 0.0])
        self.assertEqual([row.weir_width for row in lateral_rows], [20.0, 20.0])
        recalc_cumdist(rows)
        self.assertEqual([row.cumdist for row in rows if row.kind == "xs"], [50.0, 20.0, 0.0])

    def test_explicit_lateral_connection_wins_over_nearest_numeric_rs(self):
        cross_sections = [
            xs("7490.833", 331.93, 0), xs("7158.903", 290.56, 1), xs("6868.344", 1.0, 2),
        ]
        rows = build_working_from_plan(
            plan_for(cross_sections, [lateral("7300", "7490.833", 0)])
        )
        structure = next(row for row in rows if row.kind == "struct")
        anchor = next(row for row in rows if row.rs == "7490.833")
        self.assertEqual(structure.source_xs_index, 0)
        self.assertEqual(structure.connected_us_rs, "7490.833")
        self.assertEqual(structure.cumdist, anchor.cumdist)
        recalc_cumdist(rows)
        self.assertEqual(structure.cumdist, anchor.cumdist)

    def test_invalid_culvert_partition_does_not_change_xs_chain(self):
        cross_sections = [xs("300", 30.0, 0), xs("200", 20.0, 1), xs("100", 999.0, 2)]
        structure = Structure(
            river="White", reach="Muncie", rs="250", type="Culvert", name="",
            upstream_distance=0.0, weir_width=10.0, us_rs="300", ds_rs="200",
            culvert_length=40.0, culvert_us_distance=5.0,
            invert_us=10.0, invert_ds=9.0,
        )
        rows = build_working_from_plan(plan_for(cross_sections, [structure]))
        fresh = [row.cumdist for row in rows if row.kind == "xs"]
        structure_rows = [row for row in rows if row.kind == "struct"]
        self.assertEqual(fresh, [50.0, 20.0, 0.0])
        self.assertTrue(all(row.structure_partition_valid is False for row in structure_rows))
        recalc_cumdist(rows)
        self.assertEqual(fresh, [row.cumdist for row in rows if row.kind == "xs"])


@unittest.skipUnless(MUNCIE_HDF.exists(), "Muncie fixture is not available")
class MuncieIntegrationTests(unittest.TestCase):
    def test_muncie_endpoint_metadata_and_profile_values(self):
        plan = HdfReader(MUNCIE_HDF).read_plan()
        reach = plan.reaches[0]
        rows = build_working_from_plan(plan)
        xs_rows = [row for row in rows if row.kind == "xs"]
        self.assertEqual(len(xs_rows), 61)
        self.assertTrue(math.isclose(xs_rows[0].cumdist, 15458.599994659424, abs_tol=1e-8))
        fresh = [row.cumdist for row in xs_rows]
        wsel = [row.profiles["unsteady-saved-maximum"] for row in xs_rows]
        ground = [row.minchel for row in xs_rows]
        order = [row.rs for row in xs_rows]
        recalc_cumdist(rows)
        self.assertEqual(fresh, [row.cumdist for row in xs_rows])
        self.assertEqual(wsel, [row.profiles["unsteady-saved-maximum"] for row in xs_rows])
        self.assertEqual(ground, [row.minchel for row in xs_rows])
        self.assertEqual(order, [row.rs for row in xs_rows])
        lateral_rows = {row.rs: row for row in rows if row.struc == "LATERAL"}
        self.assertEqual(lateral_rows["13214"].connected_us_rs, "13214.80")
        self.assertEqual(lateral_rows["7300"].connected_us_rs, "7490.833")
        self.assertEqual(lateral_rows["7300"].source_xs_index, 36)
        self.assertEqual(lateral_rows["13214"].length_chnl, 0.0)
        self.assertEqual(lateral_rows["7300"].length_chnl, 0.0)
        self.assertTrue(lateral_rows["7300"].hw_connectivity)
        self.assertTrue(lateral_rows["7300"].structure_profile_data)
        self.assertEqual(lateral_rows["7300"].lateral_hw_distance, 175.0)

    def test_profile_and_export_endpoints_use_canonical_db_axis(self):
        opened = open_source(OpenRequest(path=str(MUNCIE_HDF)))
        sid = opened["session_id"]
        profile = get_profile(
            sid, profile=None, profiles="unsteady-saved-maximum", reach_idx=0, label_every=5,
        )
        distances = [row["cumdist"] for row in profile["cross_sections"]]
        self.assertTrue(math.isclose(max(distances), 15458.599994659424, abs_tol=1e-8))
        db = get_db(sid)
        db_xs = [row["cumdist"] for row in db["working"] if row["kind"] == "xs"]
        self.assertEqual(distances, db_xs)
        panels = get_panels(sid)["panels"]
        self.assertLessEqual(panels[0]["start_station"], min(distances))
        self.assertGreaterEqual(panels[-1]["end_station"], max(distances))
        lateral_rows = {row["rs"]: row for row in profile["structures"]}
        by_rs = {row["rs"]: row for row in profile["cross_sections"]}
        self.assertEqual(lateral_rows["7300"]["cumdist"], by_rs["7490.833"]["cumdist"])
        self.assertEqual(lateral_rows["7300"]["weir_width"], 20.0)
        self.assertEqual(lateral_rows["7300"]["connected_us_rs"], "7490.833")
        # Phase 3 deliberately blocks unvalidated lateral extent instead of
        # drawing the old authoritative-looking anchor/crest schematic.
        from fastapi import HTTPException
        for output_format in ("pdf", "dxf"):
            with self.assertRaises(HTTPException) as caught:
                export_profile(ExportOptions(session_id=sid, format=output_format,
                                             profile_names=["unsteady-saved-maximum"]))
            self.assertEqual(caught.exception.status_code,400)
            self.assertIn("LATERAL",caught.exception.detail)
            self.assertIn("White / Muncie",caught.exception.detail)



@unittest.skipUnless(NORTH_HDF.exists(), "1033 North fixture is not available")
class NorthCulvertIntegrationTests(unittest.TestCase):
    def test_canonical_interval_partition_and_recalculation(self):
        plan = HdfReader(NORTH_HDF).read_plan()
        rows = build_working_from_plan(plan)
        xs_rows = [row for row in rows if row.kind == "xs"]
        by_rs = {row.rs: row for row in xs_rows}
        faces = {row.struc: row for row in rows if row.kind == "struct"}
        self.assertTrue(math.isclose(by_rs["0.943"].length_chnl, 132.47000122070312, abs_tol=1e-8))
        self.assertTrue(math.isclose(by_rs["0.943"].canonical_length_chnl, 132.47000122070312, abs_tol=1e-8))
        self.assertTrue(faces["CULVERT-UP"].structure_partition_valid)
        self.assertTrue(math.isclose(
            faces["CULVERT-UP"].culvert_us_gap
            + faces["CULVERT-UP"].culvert_barrel_length
            + faces["CULVERT-UP"].culvert_ds_residual,
            by_rs["0.943"].canonical_length_chnl,
            abs_tol=0.01,
        ))
        self.assertTrue(math.isclose(faces["CULVERT-UP"].cumdist, 2629.289993286133, abs_tol=1e-8))
        self.assertTrue(math.isclose(faces["CULVERT-DN"].cumdist, 2519.2899920654295, abs_tol=1e-8))
        self.assertNotEqual(faces["CULVERT-UP"].cumdist, faces["CULVERT-DN"].cumdist)
        fresh_xs = [row.cumdist for row in xs_rows]
        fresh_faces = {key: row.cumdist for key, row in faces.items()}
        self.assertTrue(math.isclose(max(fresh_xs), 3229.0599632263184, abs_tol=1e-8))
        recalc_cumdist(rows)
        self.assertEqual(fresh_xs, [row.cumdist for row in xs_rows])
        self.assertEqual(fresh_faces, {key: row.cumdist for key, row in faces.items()})

    def test_profile_pdf_and_dxf_use_distinct_face_coordinates(self):
        opened = open_source(OpenRequest(path=str(NORTH_HDF)))
        sid = opened["session_id"]
        profile = get_profile(sid, profile=None, profiles="steady-row-0", reach_idx=0, label_every=5)
        faces = {row["type"]: row for row in profile["structures"]}
        self.assertTrue(math.isclose(faces["CULVERT-UP"]["cumdist"], 2629.289993286133, abs_tol=1e-8))
        self.assertTrue(math.isclose(faces["CULVERT-DN"]["cumdist"], 2519.2899920654295, abs_tol=1e-8))

        plotted_structure_x = []
        original_text = Axes.text
        def capture_text(axis, x, y, text, *args, **kwargs):
            if str(text).startswith("CULVERT-"):
                plotted_structure_x.append(float(x))
            return original_text(axis, x, y, text, *args, **kwargs)
        with patch.object(Axes, "text", new=capture_text):
            pdf = export_profile(ExportOptions(
                session_id=sid, format="pdf", profile_names=["steady-row-0"],
            ))
        self.assertGreater(pdf["size_bytes"], 0)
        self.assertEqual(sorted(plotted_structure_x), sorted([
            faces["CULVERT-UP"]["cumdist"], faces["CULVERT-DN"]["cumdist"],
        ]))

        dxf = export_profile(ExportOptions(
            session_id=sid, format="dxf", profile_names=["steady-row-0"],
        ))
        doc = ezdxf.readfile(dxf["path"])
        centers = []
        for entity in doc.modelspace().query('LWPOLYLINE[layer=="STRUCT_CULVERT"]'):
            x_values = [point[0] for point in entity.get_points("xy")]
            centers.append((min(x_values) + max(x_values)) * 0.5)
        # Normal DXF X = left margin + station / horizontal scale for panel 1.
        stations = sorted((center - 1.75) * 500.0 for center in centers)
        self.assertEqual(len(stations), 2)
        self.assertTrue(math.isclose(stations[0], faces["CULVERT-DN"]["cumdist"], abs_tol=1e-6))
        self.assertTrue(math.isclose(stations[1], faces["CULVERT-UP"]["cumdist"], abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()
