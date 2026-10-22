"""Phase 1 identity and stationing tests; no unproven junction rules."""
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
from fastapi import HTTPException

from backend.app.core.hdf_reader import HdfReader, AlignmentError, _sorted_rs, ProfileResult
from backend.app.core.merge import compute_cumdist_from_lengths, merge_reaches
from backend.app.core.rasplot_db import build_working_from_plan, build_db_from_plan, recalc_cumdist
from backend.app.routers.hecras_api import (
    _SESSIONS, WorkingPatch, patch_working, get_profile, select_reach,
    ReachSelect, OpenRequest, open_source, post_recalc_cumdist, CumdistRequest,
)

GEOM = "Geometry/Cross Sections"
RESULT = "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles"
GD = [("River", "S24"), ("Reach", "S24"), ("RS", "S24"),
      ("Len Channel", "f8"), ("Left Bank", "f8"), ("Right Bank", "f8")]
RD = [("River", "S24"), ("Reach", "S24"), ("Station", "S24")]


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "synthetic.p01.hdf"
        self.addCleanup(lambda: _SESSIONS.pop("identity-test", None))

    def fixture(self, rows=None, result_rows=None, values=None, legacy=False):
        # Geometry is deliberately interleaved and not in station order.
        rows = rows if rows is not None else [
            (b"River", b"B", b"100", 999., 0., 1.),
            (b"River", b"A", b"200", 30., 0., 1.),
            (b"River", b"B", b"200", 40., 0., 1.),
            (b"River", b"A", b"100", 999., 0., 1.),
        ]
        result_rows = result_rows if result_rows is not None else [
            (b"River", b"A", b"100"), (b"River", b"B", b"200"),
            (b"River", b"A", b"200"), (b"River", b"B", b"100"),
        ]
        values = values if values is not None else [[102., 201., 101., 202.]]
        with h5py.File(self.path, "w") as f:
            g = f.create_group(GEOM)
            g["Attributes"] = np.array(rows, dtype=GD)
            g["Station Elevation Info"] = np.array([[i * 2, 2] for i in range(len(rows))])
            g["Station Elevation Values"] = np.array([(s, 10. * (i+1)) for i in range(len(rows)) for s in [0, 1]])
            r = f.create_group(RESULT)
            r["Profile Names"] = np.array([b"PF1"])
            c = r.create_group("Cross Sections")
            c["Water Surface"] = np.array(values)
            if legacy:
                f["Results/Steady/Output/Geometry Info/Cross Section Only"] = np.array([
                    b" ".join(row) for row in result_rows
                ])
            else:
                c["Cross Section Attributes"] = np.array(result_rows, dtype=RD)
        return HdfReader(self.path).read_plan()

    def test_sorted_rs_is_deterministic(self):
        a = np.array([(b"R", b"A", rs, 1., 0., 1.) for rs in [b"100", b"300", b"200"]], dtype=GD)
        self.assertEqual(list(_sorted_rs(a)["RS"]), [b"300", b"200", b"100"])

    def test_geometry_slices_follow_source_identity_not_sorted_position(self):
        plan = self.fixture()
        self.assertEqual([(x.rs, x.minchel, x.index) for x in plan.reaches[0].cross_sections],
                         [("200", 20., 1), ("100", 40., 3)])
        self.assertEqual([(x.rs, x.minchel) for x in plan.reaches[1].cross_sections], [("200", 30.), ("100", 10.)])

    def test_second_reach_and_duplicate_rs_get_their_own_results(self):
        plan = self.fixture()
        self.assertEqual([r.profiles["steady-row-0"] for r in build_working_from_plan(plan, 0)], [101., 102.])
        self.assertEqual([r.profiles["steady-row-0"] for r in build_working_from_plan(plan, 1)], [201., 202.])
        self.assertEqual(len(set(plan.profiles[0].xs_ids)), 4)

    def test_legacy_combined_result_identities_are_matched(self):
        plan = self.fixture(legacy=True)
        self.assertEqual([r.profiles["steady-row-0"] for r in build_working_from_plan(plan, 1)], [201., 202.])

    def test_recalculation_equals_initial_for_each_reach(self):
        plan = self.fixture()
        for index, expected in [(0, [30., 0.]), (1, [40., 0.])]:
            rows = build_working_from_plan(plan, index)
            self.assertEqual([r.cumdist for r in rows], expected)
            recalc_cumdist(rows)
            self.assertEqual([r.cumdist for r in rows], expected)
            recalc_cumdist(rows, start=17.)
            self.assertEqual([r.cumdist for r in rows], [v+17. for v in expected])

    def test_edit_recalculates_and_preserves_source(self):
        plan = self.fixture()
        db = build_db_from_plan(plan)
        _SESSIONS["identity-test"] = {"plan": plan, "db": db, "reach_idx": 0}
        patch_working("identity-test", [WorkingPatch(id=db.working[0].id, length_chnl=75.)])
        self.assertEqual(db.working[0].cumdist, 30.)  # explicit recalc, not implicit
        post_recalc_cumdist("identity-test", CumdistRequest(start=0))
        self.assertEqual(db.working[0].cumdist, 75.)
        self.assertEqual(db.working[0].source_length_chnl, 30.)
        self.assertEqual(db.working[0].canonical_length_chnl, 30.)
        self.assertEqual(plan.reaches[0].cross_sections[0].length_channel, 30.)

    def test_profile_api_never_applies_other_reach_working_rows(self):
        plan = self.fixture()
        _SESSIONS["identity-test"] = {"plan": plan, "db": build_db_from_plan(plan), "reach_idx": 0}
        payload = get_profile("identity-test", profile="steady-row-0", profiles=None, reach_idx=1, label_every=5)
        self.assertEqual([r["wsel"] for r in payload["cross_sections"]], [201., 202.])
        self.assertEqual([r["cumdist"] for r in payload["cross_sections"]], [40., 0.])
        select_reach("identity-test", ReachSelect(reach_idx=1))
        payload = get_profile("identity-test", profile="steady-row-0", profiles=None, reach_idx=1, label_every=5)
        self.assertEqual([r["wsel"] for r in payload["cross_sections"]], [201., 202.])

    def test_missing_result_identity_fails_actionably(self):
        self.fixture()
        with h5py.File(self.path, "a") as f:
            del f[RESULT + "/Cross Sections/Cross Section Attributes"]
        with self.assertRaisesRegex(AlignmentError, "identity dataset is missing"):
            HdfReader(self.path).read_plan()
        with self.assertRaises(HTTPException) as caught:
            open_source(OpenRequest(path=str(self.path)))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("identity", caught.exception.detail)

    def test_result_count_mismatch_fails(self):
        with self.assertRaisesRegex(AlignmentError, "count"):
            self.fixture(values=[[1., 2., 3.]])

    def test_result_identity_mismatch_fails(self):
        with self.assertRaisesRegex(AlignmentError, "identity mismatch"):
            self.fixture(result_rows=[(b"River", b"X", str(i).encode()) for i in range(4)])

    def test_duplicate_result_identities_fail(self):
        with self.assertRaisesRegex(AlignmentError, "duplicates"):
            self.fixture(result_rows=[(b"River", b"A", b"100")] * 4)

    def test_duplicate_station_in_same_reach_fails(self):
        with self.assertRaisesRegex(AlignmentError, "duplicate"):
            self.fixture(rows=[(b"River", b"A", b"100", 1., 0., 1.)] * 2)

    def test_numeric_station_aliases_are_rejected_as_ambiguous(self):
        with self.assertRaisesRegex(AlignmentError, "coincident"):
            self.fixture(rows=[(b"River", b"A", rs, 1., 0., 1.) for rs in [b"100", b"100.0"]])

    def test_unusual_station_does_not_silently_sort_to_zero(self):
        for rs in [b"100*", b"XS-A", b"NaN"]:
            with self.subTest(rs=rs), self.assertRaises(AlignmentError):
                self.fixture(rows=[(b"River", b"A", rs, 1., 0., 1.)])

    def test_negative_and_nonfinite_source_and_working_lengths_fail(self):
        for value in [-1., float("nan"), float("inf"), -float("inf")]:
            with self.subTest(value=value):
                with self.assertRaises(AlignmentError):
                    self.fixture(rows=[(b"River", b"A", b"100", value, 0., 1.)])
                with self.assertRaises(AlignmentError):
                    compute_cumdist_from_lengths([value, 0.])
        with self.assertRaises(AlignmentError):
            compute_cumdist_from_lengths([None, 0.])

    def test_zero_length_intentionally_preserves_coincident_cumdist(self):
        self.assertEqual(compute_cumdist_from_lengths([0., 20., 999.]), [20., 20., 0.])

    def test_bad_patch_is_atomic(self):
        plan = self.fixture()
        db = build_db_from_plan(plan)
        _SESSIONS["identity-test"] = {"plan": plan, "db": db}
        with self.assertRaises(HTTPException):
            patch_working("identity-test", [WorkingPatch(id=1, length_chnl=80.), WorkingPatch(id=2, length_chnl=-1.)])
        self.assertEqual(db.working[0].length_chnl, 30.)

    def test_nonfinite_start_is_rejected_without_mutation(self):
        rows = build_working_from_plan(self.fixture())
        with self.assertRaises(AlignmentError):
            recalc_cumdist(rows, float("nan"))
        self.assertEqual([r.cumdist for r in rows], [30., 0.])

    def test_unverified_merge_is_rejected_even_with_user_junction_distances(self):
        plan = self.fixture()
        for gaps in [None, [0.], [5.]]:
            with self.subTest(gaps=gaps), self.assertRaisesRegex(ValueError, "topology"):
                merge_reaches(plan.reaches, [0, 1], gaps)

    def test_merge_rejection_does_not_diverge_session_and_db(self):
        plan = self.fixture()
        db = build_db_from_plan(plan)
        _SESSIONS["identity-test"] = {"plan": plan, "db": db, "reach_idx": 0}
        with self.assertRaises(HTTPException):
            select_reach("identity-test", ReachSelect(order=[0, 1], junction_distances=[5.]))
        self.assertIs(_SESSIONS["identity-test"]["plan"], plan)
        self.assertIs(_SESSIONS["identity-test"]["db"], db)
        self.assertNotIn("merged", _SESSIONS["identity-test"])

    def test_profiles_without_identity_cannot_fall_back_to_position(self):
        plan = self.fixture()
        plan.profiles = [ProfileResult(name="PF1", wsel=[1., 2., 3., 4.])]
        with self.assertRaisesRegex(AlignmentError, "positional"):
            build_working_from_plan(plan)

    def test_unsteady_result_columns_match_identity(self):
        self.fixture()
        target = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"
        with h5py.File(self.path, "a") as f:
            r = f.create_group(target)
            f.copy(RESULT + "/Cross Sections", r, name="Cross Sections")
            del f["Results/Steady"]
        plan = HdfReader(self.path).read_plan()
        self.assertEqual(plan.flow_type, "unsteady")
        rows = build_working_from_plan(plan, 1)
        self.assertEqual([row.profiles["unsteady-saved-maximum"] for row in rows], [201., 202.])

    def test_geometry_companion_requires_plan_link(self):
        self.fixture()
        companion = self.path.parent / "synthetic.g02.hdf"
        with h5py.File(self.path, "a") as f, h5py.File(companion, "w") as g:
            f.copy("Geometry", g)
            del f["Geometry"]
        with self.assertRaisesRegex(AlignmentError, "companion geometry"):
            HdfReader(self.path).read_plan()
        # An unrelated, earlier-sorting geometry must never be chosen.
        with h5py.File(self.path.parent / "synthetic.g01.hdf", "w") as g:
            g.create_group("Geometry")
        with h5py.File(self.path, "a") as f:
            info = f.create_group("Plan Data/Plan Information")
            info.attrs["Geometry Filename"] = b"synthetic.g02"
        plan = HdfReader(self.path).read_plan()
        self.assertEqual(plan.geometry_path, companion)
        self.assertEqual(len(plan.reaches), 2)

    def test_invalid_working_order_and_mixed_reaches_are_rejected(self):
        plan = self.fixture()
        rows = build_working_from_plan(plan)
        with self.assertRaisesRegex(AlignmentError, "upstream-first"):
            recalc_cumdist(list(reversed(rows)))
        with self.assertRaisesRegex(AlignmentError, "topology"):
            recalc_cumdist(rows + build_working_from_plan(plan, 1))

    def test_ambiguous_legacy_combined_names_are_rejected(self):
        rows = [(b"A B", b"C", b"100", 0., 0., 1.), (b"A", b"B C", b"100", 0., 0., 1.)]
        with self.assertRaisesRegex(AlignmentError, "ambiguous combined"):
            self.fixture(rows=rows, result_rows=[r[:3] for r in rows], values=[[1., 2.]], legacy=True)


if __name__ == "__main__":
    unittest.main()
