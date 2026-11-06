from copy import deepcopy
import tempfile
import unittest

import ezdxf
from pypdf import PdfReader

from backend.app.core.dxf_export import export_dxf
from backend.app.core.engineering_drawing import build_engineering_drawing
from pathlib import Path

from backend.app.core.hdf_reader import CrossSection, Plan, ProfileResult, ReachGeometry
from backend.app.core.model_topology import (
    ModelContext, build_topology, continuous_distances, create_flooding_source, stable_id,
)
from backend.app.core.state_contract import panels_stale, review_panels
from backend.app.routers import hecras_api
from backend.app.routers.report import render_profile_pdf_from_db


def xs(river, reach, label, length, index):
    return CrossSection(river, reach, label, label, length, 0.0, 1.0, index=index)


def reach(name, *, river="River", length=10.0, us=None, ds=None):
    connectivity = {
        "US Type": us[0] if us else "EXTERNAL", "US Name": us[1] if us else "",
        "Junction to US XS": us[2] if us else None,
        "DS Type": ds[0] if ds else "EXTERNAL", "DS Name": ds[1] if ds else "",
        "DS XS to Junction": ds[2] if ds else None,
    }
    return ReachGeometry(
        river, name, [], [xs(river, name, "UP", length, 0), xs(river, name, "DN", 0.0, 1)], [],
        source_path="synthetic.hdf", source_connectivity=connectivity,
        source_order_status="SOURCE_NONNUMERIC_UPSTREAM_FIRST_VERIFIED")


class AdvancedTopologyTests(unittest.TestCase):
    def tearDown(self):
        hecras_api._SESSIONS.clear()

    def test_disconnected_components_are_deterministic_and_cannot_be_combined(self):
        left, right = reach("Left"), reach("Right")
        first = build_topology("network", [right, left], [], "synthetic.hdf")
        second = build_topology("network", [left, right], [], "synthetic.hdf")
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.components), 2)
        ids = {item.reach_name: item.id for item in first.reaches.values()}
        source = create_flooding_source("network", "invalid", [ids["Left"], ids["Right"]], first)
        self.assertEqual(source.topology_validation_status, "BLOCKED")
        self.assertTrue(any("disconnected" in message for message in source.validation_messages))

    def test_split_rejoin_requires_one_explicit_branch_and_preserves_choice(self):
        trunk = reach("Trunk", ds=("JUNCTION", "Split", 1.0))
        branch_a = reach("A", us=("JUNCTION", "Split", 2.0), ds=("JUNCTION", "Join", 3.0))
        branch_b = reach("B", us=("JUNCTION", "Split", 4.0), ds=("JUNCTION", "Join", 5.0))
        tail = reach("Tail", us=("JUNCTION", "Join", 6.0))
        graph = build_topology("branched", [branch_b, tail, trunk, branch_a],
                               [{"Name": "Split"}, {"Name": "Join"}], "synthetic.hdf")
        ids = {item.reach_name: item.id for item in graph.reaches.values()}
        reaches = {ids[item.reach]: item for item in (trunk, branch_a, branch_b, tail)}
        routes = []
        for branch in ("A", "B"):
            source = create_flooding_source(
                "branched", branch, [ids["Tail"], ids[branch], ids["Trunk"]], graph)
            result = continuous_distances(source, graph, reaches)
            self.assertEqual(source.topology_validation_status, "SUPPORTED")
            self.assertEqual(result.status, "SUPPORTED")
            self.assertEqual(len([item for item in result.ledger if item.kind == "JUNCTION"]), 2)
            self.assertEqual(len(source.provenance["explicit_branch_junction_ids"]), 2)
            routes.append(set(result.distances))
        self.assertNotEqual(routes[0], routes[1])
        both = create_flooding_source(
            "branched", "both", [ids["Tail"], ids["A"], ids["B"], ids["Trunk"]], graph)
        self.assertEqual(both.topology_validation_status, "BLOCKED")

    def test_directed_cycle_is_reported_and_blocked(self):
        a = reach("A", us=("JUNCTION", "J2", 1.0), ds=("JUNCTION", "J1", 1.0))
        b = reach("B", us=("JUNCTION", "J1", 1.0), ds=("JUNCTION", "J2", 1.0))
        graph = build_topology("cycle", [a, b], [{"Name": "J1"}, {"Name": "J2"}], "synthetic.hdf")
        self.assertEqual(len(graph.cycle_reach_ids), 2)
        self.assertTrue(any("cycle" in warning for warning in graph.warnings))
        ids = {item.reach_name: item.id for item in graph.reaches.values()}
        source = create_flooding_source("cycle", "cycle", [ids["B"], ids["A"]], graph)
        self.assertEqual(source.topology_validation_status, "BLOCKED")

    def test_route_identity_order_mismatch_blocks_distance_calculation(self):
        item = reach("Only")
        graph = build_topology("order", [item], [], "synthetic.hdf")
        rid = next(iter(graph.reaches))
        source = create_flooding_source("order", "only", [rid], graph)
        changed = deepcopy(item)
        changed.cross_sections.reverse()
        result = continuous_distances(source, graph, {rid: changed})
        self.assertEqual(result.status, "BLOCKED")
        self.assertTrue(any("identity/order" in message for message in result.messages))

    def test_si_and_us_canonical_networks_have_identical_distance_ledgers(self):
        # Phase 2 converts SI source values to canonical feet before topology.
        # Equivalent canonical networks must therefore be byte-for-byte equal.
        us_down = reach("Down", length=328.0839895013123, us=("JUNCTION", "J", 16.4041994750656))
        us_up = reach("Up", length=164.04199475065616, ds=("JUNCTION", "J", 9.84251968503937))
        si_down, si_up = deepcopy(us_down), deepcopy(us_up)
        for model, pair in (("us", (us_down, us_up)), ("si", (si_down, si_up))):
            graph = build_topology(model, list(pair), [{"Name": "J"}], "synthetic.hdf")
            ids = {item.reach_name: item.id for item in graph.reaches.values()}
            source = create_flooding_source(model, "route", [ids["Down"], ids["Up"]], graph)
            result = continuous_distances(
                source, graph, {ids["Down"]: pair[0], ids["Up"]: pair[1]})
            normalized = [(entry.kind, entry.distance, entry.cumulative_distance)
                          for entry in result.ledger]
            if model == "us":
                expected = normalized
            else:
                self.assertEqual(normalized, expected)

    def test_explicit_branch_route_change_rebuilds_revision_and_requires_panel_review(self):
        trunk = reach("Trunk", ds=("JUNCTION", "Split", 1.0))
        branch_a = reach("A", us=("JUNCTION", "Split", 2.0), ds=("JUNCTION", "Join", 3.0))
        branch_b = reach("B", us=("JUNCTION", "Split", 4.0), ds=("JUNCTION", "Join", 5.0))
        tail = reach("Tail", us=("JUNCTION", "Join", 6.0))
        reaches = [trunk, branch_a, branch_b, tail]
        identities = [item.identity for reach_item in reaches for item in reach_item.cross_sections]
        profile = ProfileResult(
            "P", [100.0 + index for index in range(len(identities))], xs_ids=identities,
            profile_id="p", key="p", source_path="synthetic", source_dataset="WSEL",
            values=[{"value": 100.0 + index} for index in range(len(identities))])
        plan = Plan("Branches", Path("synthetic.p01.hdf"), None, None, None, None,
                    profiles=[profile], reaches=reaches, model_id="branch-session",
                    source_units="US Customary")
        plan.topology = build_topology(
            plan.model_id, reaches, [{"Name": "Split"}, {"Name": "Join"}], "synthetic.hdf")
        sid = "branch-session"
        hecras_api._SESSIONS[sid] = {
            "plan": plan, "path": "synthetic.p01.hdf", "reach_idx": None,
            "model_context": ModelContext(plan.model_id, 1, sid, "synthetic.p01.hdf", "ACTIVE")}
        ids = {item.reach_name: item.id for item in plan.topology.reaches.values()}
        hecras_api.commit_flooding_source(sid, hecras_api.FloodingSourceRequest(
            display_name="A route", reach_ids=[ids["Tail"], ids["A"], ids["Trunk"]]))
        first_db = hecras_api._SESSIONS[sid]["db"]
        review_panels(first_db)
        self.assertFalse(panels_stale(first_db))
        first_extent = (min(row.cumdist for row in first_db.working if row.kind == "xs"),
                        max(row.cumdist for row in first_db.working if row.kind == "xs"))

        hecras_api.commit_flooding_source(sid, hecras_api.FloodingSourceRequest(
            display_name="B route", reach_ids=[ids["Tail"], ids["B"], ids["Trunk"]]))
        second_db = hecras_api._SESSIONS[sid]["db"]
        second_extent = (min(row.cumdist for row in second_db.working if row.kind == "xs"),
                         max(row.cumdist for row in second_db.working if row.kind == "xs"))
        self.assertIsNot(second_db, first_db)
        self.assertTrue(panels_stale(second_db))
        self.assertNotEqual(first_extent, second_extent)
        self.assertEqual(second_db.flooding_source["display_name"], "B route")
        review_panels(second_db)
        drawing = build_engineering_drawing(
            second_db, second_db.panels, [second_db.profiles[0].profile_id])
        self.assertTrue(drawing.panels[0].polylines)
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "branch.pdf"
            dxf_path = Path(directory) / "branch.dxf"
            render_profile_pdf_from_db(str(pdf_path), second_db)
            export_dxf(dxf_path, second_db)
            self.assertGreater(len(PdfReader(str(pdf_path)).pages), 0)
            document = ezdxf.readfile(dxf_path)
            self.assertTrue([entity for entity in document.modelspace().query("LWPOLYLINE")
                             if entity.dxf.layer.startswith("PROF_")])


if __name__ == "__main__":
    unittest.main()
