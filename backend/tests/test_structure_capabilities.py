import unittest

from backend.app.core.structure_capabilities import (
    STRUCTURE_CAPABILITIES,
    StructureCapability,
    StructureCapabilityRegistry,
)
from backend.tests.test_structures import bridge, culvert, database
from backend.app.core.structure_model import interpolate_deck


class StructureCapabilityRegistryTests(unittest.TestCase):
    def model(self, source):
        return __import__("backend.app.core.structure_model", fromlist=["models_from_rows"]) \
            .models_from_rows(database([source])[1].working)[0]

    def test_complete_bridge_and_culvert_signatures_are_export_eligible(self):
        for source, capability in [
            (bridge(), "BRIDGE_CHORD_ENVELOPE_V1"),
            (culvert(), "CULVERT_GROUP_ENVELOPES_V1"),
        ]:
            with self.subTest(capability=capability):
                model = self.model(source)
                self.assertEqual(model.capability_id, capability)
                self.assertEqual(model.capability_level, "AUTHORITATIVE_DRAWING")
                self.assertEqual(model.status, "VALIDATED")
                self.assertEqual(model.missing_source_fields, [])

    def test_bridge_multiple_disjoint_lid_openings_fail_closed(self):
        source = bridge()
        source.source_profiles["US BR Lid Profile"] = [
            (0.0, 15.0), (2.0, 16.0), (3.0, 0.0), (5.0, 14.0), (8.0, 15.0)]
        model = self.model(source)
        self.assertEqual(model.capability_id, "BRIDGE_CHORD_ENVELOPE_V1")
        self.assertEqual(model.status, "UNSUPPORTED")
        self.assertIn("Multiple disjoint bridge lid openings", model.reason)
        self.assertFalse(model.faces)

    def test_mixed_bridge_culvert_and_gated_culvert_never_use_plain_rules(self):
        mixed = bridge()
        mixed.openings = [dict(culvert().openings[0])]
        model = self.model(mixed)
        self.assertEqual(model.status, "UNSUPPORTED")
        self.assertIn("Mixed bridge/culvert", model.reason)

        gated = culvert()
        gated.source_attributes["Gate Groups"] = 1
        gated.openings.append({**gated.openings[0], "source_id": "S1/group-2"})
        model = self.model(gated)
        self.assertEqual(model.type, "GATED")
        self.assertEqual(model.capability_id, "GATED_INVENTORY_ONLY_V1")
        self.assertEqual(model.capability_level, "INVENTORY_ONLY")
        self.assertNotEqual(model.status, "VALIDATED")

    def test_registry_distinguishes_preview_from_authoritative_drawing(self):
        lateral = STRUCTURE_CAPABILITIES.declaration("LATERAL")
        inline = STRUCTURE_CAPABILITIES.declaration("INLINE")
        self.assertEqual(lateral.support_level, "SOURCE_GEOMETRY_PREVIEW")
        self.assertEqual(inline.support_level, "SOURCE_GEOMETRY_PREVIEW")
        self.assertFalse(lateral.export_eligible)
        self.assertFalse(inline.export_eligible)

    def test_incomplete_culvert_lists_exact_missing_source_fields(self):
        source = culvert()
        del source.openings[0]["Invert DS"]
        source.openings[0].pop("association")
        model = self.model(source)
        self.assertEqual(model.status, "INVALID")
        self.assertIn("group 1 Invert DS", model.missing_source_fields)
        self.assertIn("group 1 explicit structure association", model.missing_source_fields)
        self.assertIn("authoritative DXF", model.affected_workflows)

    def test_registry_rejects_duplicate_family_declarations(self):
        original = STRUCTURE_CAPABILITIES.declaration("BRIDGE")
        duplicate = StructureCapability(**{**original.__dict__, "capability_id": "DUPLICATE"})
        with self.assertRaisesRegex(ValueError, "Ambiguous structure capability"):
            StructureCapabilityRegistry((original, duplicate)).declaration("BRIDGE")

    def test_preflight_exposes_capability_and_workflow_diagnostics(self):
        source = culvert()
        source.openings[0]["Shape Name"] = "Unverified Arch"
        _plan, db = database([source])
        from backend.app.core.structure_model import preflight
        record = preflight(db.working, 0.0, 50.0)[0]
        self.assertEqual(record["capability_id"], "CULVERT_GROUP_ENVELOPES_V1")
        self.assertEqual(record["capability_level"], "AUTHORITATIVE_DRAWING")
        self.assertIn("shape Unverified Arch", record["reason"])
        self.assertEqual(record["status"], "INVALID")

    def test_vertical_deck_segment_is_allowed_only_away_from_ambiguous_station(self):
        deck = [(0.0, 10.0), (5.0, 11.0), (5.0, 13.0), (10.0, 14.0)]
        self.assertAlmostEqual(interpolate_deck(deck, 2.5), 10.5)
        self.assertAlmostEqual(interpolate_deck(deck, 7.5), 13.5)
        with self.assertRaisesRegex(ValueError, "multiple elevations"):
            interpolate_deck(deck, 5.0)
        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            interpolate_deck([(0.0, 10.0), (5.0, 11.0), (4.0, 12.0)], 2.0)


if __name__ == "__main__":
    unittest.main()

