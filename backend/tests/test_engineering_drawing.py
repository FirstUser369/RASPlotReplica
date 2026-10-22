import unittest
from dataclasses import FrozenInstanceError

from backend.app.core.engineering_drawing import (
    DrawingBuildConfig, EngineeringBounds, EngineeringPoint,
    build_engineering_drawing, engineering_to_dxf_inches,
    engineering_to_pdf_points, raster_affine,
)
from backend.app.core.rasplot_db import Landmark, PanelDef, ProfileMeta, RasplotDatabase, WorkingRow


def database():
    db = RasplotDatabase()
    db.header.stream_name = "Test Stream"
    db.header.vertical_datum = "UNKNOWN"
    db.stationing_start = 0.0
    db.profiles = [ProfileMeta(
        name="Source Profile", profile_id="profile-stable", key="profile-stable",
        selected=True, layer="PROFILE_TEST", color="#C00000")]
    values = [(0.0, 10.0), (50.0, 12.0), (100.0, None),
              (150.0, 16.0), (200.0, 18.0)]
    db.working = [WorkingRow(
        id=index + 1, river="River", reach="Reach", rs=str(1000-index),
        cumdist=station, minchel=5.0 + index, profiles={"profile-stable": value},
        source_identity=f"source-{index}", row_identity=f"row-{index}")
        for index, (station, value) in enumerate(values)]
    return db


class EngineeringDrawingDefinitionTests(unittest.TestCase):
    def test_builder_is_immutable_and_preserves_missing_profile_gaps(self):
        db = database()
        panel = PanelDef(27, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(
            db, [panel], ["profile-stable"], revision=(0.0, 200.0),
            config=DrawingBuildConfig(panel_suffix="P"))
        self.assertEqual(drawing.panels[0].display_id, "27P")
        profiles = [line for line in drawing.panels[0].polylines if line.kind == "PROFILE"]
        self.assertEqual(len(profiles), 2)
        self.assertEqual([(point.x, point.y) for point in profiles[0].points],
                         [(0.0, 10.0), (50.0, 12.0)])
        self.assertEqual([(point.x, point.y) for point in profiles[1].points],
                         [(150.0, 16.0), (200.0, 18.0)])
        with self.assertRaises(FrozenInstanceError):
            drawing.panels[0].panel_id = 1

    def test_every_cross_section_has_a_location_and_reviewed_labels_stay_explicit(self):
        db = database()
        db.working[0].label_letter = "A"
        panel = PanelDef(27, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        annotations = [item for item in drawing.panels[0].annotations
                       if item.kind in {"CROSS_SECTION", "CROSS_SECTION_LOCATION"}]
        self.assertEqual([item.text for item in annotations], ["A", "", "", "", ""])
        self.assertEqual([item.kind for item in annotations],
                         ["CROSS_SECTION"] + ["CROSS_SECTION_LOCATION"] * 4)
        anchors = [item.anchor.x for item in drawing.panels[0].annotations
                   if item.kind in {"CROSS_SECTION", "CROSS_SECTION_LOCATION"}]
        self.assertEqual(anchors, [0.0, 50.0, 100.0, 150.0, 200.0])

    def test_presentation_uses_source_metadata_and_discloses_missing_data(self):
        db = database()
        db.header.stream_name = "Validated River"
        db.header.vertical_datum = "UNKNOWN"
        db.flooding_source = {"display_name": "Validated Flooding Source"}
        panel = PanelDef(27, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(
            db, [panel], revision=(0.0, 200.0),
            config=DrawingBuildConfig(panel_suffix="P"))
        presentation = drawing.presentation
        self.assertEqual(presentation.page_title, "FLOOD PROFILES")
        self.assertEqual(drawing.identity.flooding_source_name, "Validated Flooding Source")
        self.assertEqual(presentation.vertical_datum_disclosure, "VERTICAL DATUM: UNKNOWN")
        self.assertEqual(drawing.panels[0].display_id, "27P")
        self.assertIn("Stream bed", [entry.label for entry in presentation.legend_entries])
        self.assertTrue(any("profile gaps are shown" in note
                            for note in presentation.data_unavailable_footnotes))
        self.assertTrue(any("Vertical datum UNKNOWN" in warning for warning in drawing.warnings))
        with self.assertRaises(FrozenInstanceError):
            presentation.page_title = "OTHER"

    def test_builder_uses_saved_panel_suffix_with_explicit_config_override(self):
        db = database()
        saved = PanelDef(27, 0.0, 200.0, 0.0, 20.0,
                         h_scale=50.0, v_scale=5.0, panel_suffix=" p ")
        drawing = build_engineering_drawing(db, [saved], revision=(0.0, 200.0))
        self.assertEqual(drawing.panels[0].panel_id, 27)
        self.assertEqual(drawing.panels[0].panel_suffix, "P")
        self.assertEqual(drawing.panels[0].display_id, "27P")

        override = build_engineering_drawing(
            db, [saved], revision=(0.0, 200.0),
            config=DrawingBuildConfig(panel_suffix="A"))
        self.assertEqual(override.panels[0].display_id, "27A")

        legacy = PanelDef(27, 0.0, 200.0, 0.0, 20.0,
                          h_scale=50.0, v_scale=5.0)
        unsuffixed = build_engineering_drawing(db, [legacy], revision=(0.0, 200.0))
        self.assertEqual(unsuffixed.panels[0].display_id, "27")

    def test_vertical_and_horizontal_annotations_keep_their_engineering_anchors(self):
        db = database()
        db.landmarks = [
            Landmark(1, 75.0, 14.0, "Vertical note", orientation="Vertical"),
            Landmark(2, 125.0, 15.0, "Horizontal note", orientation="Horizontal"),
        ]
        panel = PanelDef(5, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        annotations = {annotation.text: annotation for annotation in drawing.panels[0].annotations}
        self.assertEqual(annotations["Vertical note"].anchor, EngineeringPoint(75.0, 14.0))
        self.assertEqual(annotations["Vertical note"].rotation_degrees, 90.0)
        self.assertEqual(annotations["Horizontal note"].anchor, EngineeringPoint(125.0, 15.0))
        self.assertEqual(annotations["Horizontal note"].rotation_degrees, 0.0)

    def test_annotation_collision_warnings_are_deterministic(self):
        db = database()
        db.landmarks = [
            Landmark(1, 100.0, 15.0, "First"),
            Landmark(2, 100.0, 15.0, "Second"),
        ]
        panel = PanelDef(6, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        first = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        second = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        self.assertEqual(first.presentation.annotation_warnings,
                         second.presentation.annotation_warnings)
        self.assertTrue(first.presentation.annotation_warnings)

    def test_pdf_and_engineering_dxf_transforms_preserve_fixed_scale(self):
        db = database()
        panel = PanelDef(7, 0.0, 200.0, 0.0, 20.0,
                         panel_x=30.0, panel_y=12.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        definition = drawing.panels[0]
        start = EngineeringPoint(0.0, 0.0)
        end = EngineeringPoint(200.0, 20.0)
        pdf_start, pdf_end = engineering_to_pdf_points(start, definition), engineering_to_pdf_points(end, definition)
        self.assertEqual((pdf_start.x, pdf_start.y), (1.75 * 72, 1.0 * 72))
        self.assertEqual((pdf_end.x - pdf_start.x, pdf_end.y - pdf_start.y), (4.0 * 72, 4.0 * 72))
        dxf_start, dxf_end = engineering_to_dxf_inches(start, definition), engineering_to_dxf_inches(end, definition)
        self.assertEqual((dxf_start.x, dxf_start.y), (31.75, 13.0))
        self.assertEqual((dxf_end.x - dxf_start.x, dxf_end.y - dxf_start.y), (4.0, 4.0))

    def test_raster_transform_maps_pixel_centers_and_nonuniform_axes(self):
        bounds = EngineeringBounds(1000.0, 6500.0, 5579.72, 5624.72)
        affine = raster_affine(bounds, 3900, 2700)
        upper_left = affine.pixel_center(0, 0)
        lower_right = affine.pixel_center(3899, 2699)
        self.assertAlmostEqual(upper_left.x, bounds.x_min + affine.a / 2)
        self.assertAlmostEqual(upper_left.y, bounds.y_max + affine.e / 2)
        self.assertAlmostEqual(lower_right.x, bounds.x_max - affine.a / 2)
        self.assertAlmostEqual(lower_right.y, bounds.y_min - affine.e / 2)
        self.assertNotEqual(abs(affine.a), abs(affine.e))

    def test_grid_is_aligned_to_shared_station_origin_across_panels(self):
        db = database()
        panels = [
            PanelDef(27, 0.0, 100.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0),
            PanelDef(31, 100.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0),
        ]
        drawing = build_engineering_drawing(
            db, panels, revision=(0.0, 200.0), config=DrawingBuildConfig(
                station_minor=25.0, station_major=100.0,
                elevation_minor=1.0, elevation_major=5.0,
                panel_suffixes=((27, "P"), (31, "A"))))
        self.assertEqual([panel.display_id for panel in drawing.panels], ["27P", "31A"])
        first = {(line.coordinate, line.major) for line in drawing.panels[0].grid_lines
                 if line.axis == "STATION"}
        second = {(line.coordinate, line.major) for line in drawing.panels[1].grid_lines
                  if line.axis == "STATION"}
        self.assertIn((100.0, True), first)
        self.assertIn((100.0, True), second)
        self.assertIn((25.0, False), first)
        self.assertIn((125.0, False), second)
        self.assertEqual(drawing.panels[0].grid.station_origin, 0.0)
        self.assertEqual(drawing.panels[1].grid.station_origin, 0.0)
        self.assertEqual(drawing.panels[0].grid.elevation_minor, 1.0)

    def test_explicit_grid_origin_is_respected_without_stretching_short_panel(self):
        db = database()
        panel = PanelDef(9, 25.0, 125.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        drawing = build_engineering_drawing(
            db, [panel], revision=(25.0, 125.0), config=DrawingBuildConfig(
                station_origin=10.0, station_minor=20.0, station_major=100.0,
                elevation_minor=1.0, elevation_major=5.0))
        definition = drawing.panels[0]
        station_lines = [line.coordinate for line in definition.grid_lines if line.axis == "STATION"]
        self.assertEqual(station_lines, [30.0, 50.0, 70.0, 90.0, 110.0])
        start = engineering_to_pdf_points(EngineeringPoint(25.0, 0.0), definition)
        end = engineering_to_pdf_points(EngineeringPoint(125.0, 0.0), definition)
        self.assertAlmostEqual((end.x - start.x) / 72.0, 2.0)

    def test_invalid_grid_ratio_is_rejected(self):
        db = database()
        panel = PanelDef(1, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        with self.assertRaisesRegex(ValueError, "integer multiples"):
            build_engineering_drawing(
                db, [panel], revision=(0.0, 200.0),
                config=DrawingBuildConfig(station_minor=30.0, station_major=100.0))

    def test_builder_does_not_mutate_database_or_saved_panel_identity(self):
        db = database()
        panel = PanelDef(42, 0.0, 200.0, 0.0, 20.0, h_scale=50.0, v_scale=5.0)
        before = db.to_dict()
        drawing = build_engineering_drawing(db, [panel], revision=(0.0, 200.0))
        self.assertEqual(db.to_dict(), before)
        self.assertEqual(drawing.panels[0].panel_id, 42)
        self.assertEqual(drawing.panels[0].display_id, "42")


if __name__ == "__main__":
    unittest.main()
