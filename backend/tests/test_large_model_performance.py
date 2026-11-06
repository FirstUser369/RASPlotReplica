import math
import unittest

from backend.app.core.rasplot_db import auto_panels, build_db_from_plan, recalc_cumdist
from backend.app.core.state_contract import panel_dependency_digest
from backend.benchmarks.large_model_benchmark import synthetic_plan


class LargeModelEquivalenceTests(unittest.TestCase):
    def test_ten_thousand_xs_preserve_identity_distance_profiles_and_gaps(self):
        count = 10_000
        db = build_db_from_plan(synthetic_plan(count))
        xs = [row for row in db.working if row.kind == "xs"]
        self.assertEqual(len(xs), count)
        self.assertEqual(len({row.source_identity for row in xs}), count)
        self.assertEqual(xs[0].cumdist, 25.0 * (count - 1))
        self.assertEqual(xs[-1].cumdist, 0.0)
        self.assertIsNone(xs[997].profiles["p1"])
        self.assertEqual(xs[996].profiles["p1"], 102.996)
        self.assertEqual(xs[998].profiles["p1"], 102.998)

    def test_large_recalculation_is_exact_and_deterministic(self):
        db = build_db_from_plan(synthetic_plan(5_000))
        xs = [row for row in db.working if row.kind == "xs"]
        xs[2_500].length_chnl = 31.25
        recalc_cumdist(db.working)
        self.assertEqual(xs[-1].cumdist, 0.0)
        self.assertEqual(xs[2_501].cumdist, 25.0 * (5_000 - 2_501 - 1))
        self.assertEqual(xs[2_500].cumdist, xs[2_501].cumdist + 31.25)
        first = [row.cumdist for row in xs]
        recalc_cumdist(db.working)
        self.assertEqual([row.cumdist for row in xs], first)

    def test_indexed_panel_generation_preserves_bounds_scales_and_gaps(self):
        db = build_db_from_plan(synthetic_plan(5_000))
        panels = auto_panels(db.working, h_scale=2_500.0, v_scale=10.0,
                             station_range=(0.0, 124_975.0))
        self.assertEqual(len(panels), math.ceil(124_975.0 / 32_500.0))
        self.assertEqual(panels[0].start_station, 0.0)
        self.assertEqual(panels[-1].end_station, 130_000.0)
        self.assertTrue(all(panel.h_scale == 2_500.0 and panel.v_scale == 10.0
                            for panel in panels))
        self.assertTrue(all(a.end_station == b.start_station
                            for a, b in zip(panels, panels[1:])))

    def test_dependency_digest_remains_sensitive_on_large_model(self):
        db = build_db_from_plan(synthetic_plan(5_000))
        before = panel_dependency_digest(db)
        db.working[4_000].profiles["p3"] += 0.125
        self.assertNotEqual(before, panel_dependency_digest(db))


if __name__ == "__main__":
    unittest.main()
