import tempfile
import unittest
import math
from pathlib import Path

import ezdxf
from pypdf import PdfReader
from pypdf.generic import ContentStream

from backend.app.core.rasplot_db import Landmark, RasplotDatabase, WorkingRow, PanelDef, ProfileMeta
from backend.app.core.dxf_export import export_dxf
from backend.app.core.engineering_drawing import (
    DrawingBuildConfig, EngineeringPoint, build_engineering_drawing,
    engineering_to_pdf_points,
)
from backend.app.routers.report import render_profile_pdf_from_db


def sample(panel=None):
    panel = panel or PanelDef(3, 0., 6500., 100., 145.)
    db = RasplotDatabase()
    db.panels = [panel]
    db.profiles = [ProfileMeta(name="Test",profile_id="Test",key="Test", selected=True, color="#C00000", layer="TEST")]
    db.working = [WorkingRow(id=i+1, river="R", reach="A", rs=str(i),
        cumdist=x, minchel=y, profiles={"Test": y})
        for i, (x,y) in enumerate([(panel.start_station,panel.start_elevation),
                                  (panel.end_station,panel.end_elevation)])]
    return db


def pdf_geometry(path, index=0):
    reader = PdfReader(path)
    page = reader.pages[index]
    ops = ContentStream(page.get_contents(), reader).operations
    rectangles, red_points = [], []
    red = False
    for args, op in ops:
        if op == b"RG":
            red = float(args[0]) > .7 and float(args[1]) == 0 and float(args[2]) == 0
        if op == b"re":
            rectangles.append(tuple(float(v) for v in args))
        if red and op in (b"m", b"l"):
            red_points.append(tuple(float(v) for v in args))
    return page, rectangles, red_points


def pdf_grid_segments(path):
    reader = PdfReader(path)
    operations = ContentStream(reader.pages[0].get_contents(), reader).operations
    width = None
    start = None
    segments = {"major": set(), "minor": set()}
    for args, operation in operations:
        if operation == b"w":
            width = float(args[0])
        elif operation == b"m":
            start = (float(args[0]), float(args[1]))
        elif operation == b"l" and start is not None:
            if math.isclose(width or -1, 0.45, abs_tol=0.001):
                target = "major"
            elif math.isclose(width or -1, 0.18, abs_tol=0.001):
                target = "minor"
            else:
                target = None
            if target:
                end = (float(args[0]), float(args[1]))
                if abs(end[0] - start[0]) > 100 or abs(end[1] - start[1]) > 100:
                    segments[target].add(tuple(sorted((
                        (round(start[0], 2), round(start[1], 2)),
                        (round(end[0], 2), round(end[1], 2)),
                    ))))
            start = (float(args[0]), float(args[1]))
    return segments


def dxf_grid_segments(document, layer):
    return {
        tuple(sorted((
            (round(float(line.dxf.start.x) * 72, 2), round(float(line.dxf.start.y) * 72, 2)),
            (round(float(line.dxf.end.x) * 72, 2), round(float(line.dxf.end.y) * 72, 2)),
        )))
        for line in document.modelspace().query(f'LINE[layer=="{layer}"]')
    }


class ExportScaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = Path(self.tmp.name)/"profile.pdf"
        self.dxf = Path(self.tmp.name)/"profile.dxf"

    def test_standard_pdf_serialized_axes_and_profile_are_13_by_9_inches(self):
        render_profile_pdf_from_db(str(self.pdf), sample())
        page, rects, points = pdf_geometry(self.pdf)
        expected = (126., 72., 936., 648.)
        self.assertTrue(any(all(abs(a-b)<.001 for a,b in zip(r,expected)) for r in rects), rects)
        self.assertTrue(any(abs(x-126.)<.001 and abs(y-72.)<.001 for x,y in points),points)
        self.assertTrue(any(abs(x-1062.)<.001 and abs(y-720.)<.001 for x,y in points),points)
        text = page.extract_text()
        self.assertIn("PANEL 3", text)
        self.assertIn("FLOOD PROFILES", text)
        self.assertIn("Stream bed", text)
        self.assertIn("VERTICAL DATUM: UNKNOWN", text)

    def test_dxf_inches_and_plot_border(self):
        export_dxf(self.dxf, sample())
        doc=ezdxf.readfile(self.dxf)
        self.assertEqual(doc.header["$INSUNITS"],1)
        self.assertEqual(doc.header["$MEASUREMENT"],0)
        line=list(doc.modelspace().query('LWPOLYLINE[layer=="TEST"]'))[0]
        pts=list(line.get_points())
        self.assertAlmostEqual(pts[-1][0]-pts[0][0],13.,places=8)
        self.assertAlmostEqual(pts[-1][1]-pts[0][1],9.,places=8)
        borders=list(doc.modelspace().query('LWPOLYLINE[layer=="PANEL_BORDER"]'))
        sizes=[(max(p[0] for p in l)-min(p[0] for p in l), max(p[1] for p in l)-min(p[1] for p in l)) for l in borders]
        self.assertIn((13.,9.),sizes)

    def test_invalid_version_is_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError,"version"):
            export_dxf(self.dxf,sample(),dxf_version="invalid")

    def assert_pdf_dimensions(self, panel, index=0):
        page, rects, points = pdf_geometry(self.pdf,index)
        w=(panel.end_station-panel.start_station)/panel.h_scale*72
        h=(panel.end_elevation-panel.start_elevation)/panel.v_scale*72
        expected=(126.,72.,w,h)
        self.assertTrue(any(all(abs(a-b)<.001 for a,b in zip(r,expected)) for r in rects),rects)
        return points

    def test_other_horizontal_vertical_scales_and_custom_spans(self):
        for span,elev,hs,vs in [(3250,45,250,5),(6500,90,500,10),(1200,12,200,3)]:
            with self.subTest(span=span,elev=elev):
                panel=PanelDef(9,100.,100.+span,200.,200.+elev,h_scale=hs,v_scale=vs)
                db=sample(panel)
                render_profile_pdf_from_db(str(self.pdf),db)
                self.assert_pdf_dimensions(panel)
                export_dxf(self.dxf,db)
                line=list(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="TEST"]'))[0]
                pts=list(line.get_points())
                self.assertAlmostEqual(pts[-1][0]-pts[0][0],span/hs,places=8)
                self.assertAlmostEqual(pts[-1][1]-pts[0][1],elev/vs,places=8)

    def test_multiple_pdf_pages_and_dxf_independent_origins(self):
        from dataclasses import replace
        first=PanelDef(2,0.,6500.,100.,145.,panel_x=21.,panel_y=-4.)
        second=replace(first,panel=7,panel_x=0.,panel_y=6.)
        db=sample(first); db.panels=[first,second]
        render_profile_pdf_from_db(str(self.pdf),db)
        self.assertEqual(len(PdfReader(self.pdf).pages),2)
        export_dxf(self.dxf,db)
        lines=list(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="TEST"]'))
        self.assertEqual(len(lines),2)
        for i,(panel,line) in enumerate(zip(db.panels,lines)):
            points=self.assert_pdf_dimensions(panel,i)
            xy=list(line.get_points())[0]
            self.assertAlmostEqual(xy[0],panel.panel_x+1.75,places=8)
            self.assertAlmostEqual(xy[1],panel.panel_y+1.,places=8)
            # Translation from absolute CAD sheet origin to PDF page-local points.
            expected=((xy[0]-panel.panel_x)*72,(xy[1]-panel.panel_y)*72)
            self.assertTrue(any(abs(x-expected[0])<.001 and abs(y-expected[1])<.001 for x,y in points))
            self.assertIn(f"PANEL {panel.panel}",PdfReader(self.pdf).pages[i].extract_text())

    def test_automatic_dxf_origins_use_sheet_width_and_gap(self):
        from dataclasses import replace
        db=sample();db.panels.append(replace(db.panels[0],panel=7))
        export_dxf(self.dxf,db)
        lines=list(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="TEST"]'))
        self.assertAlmostEqual(lines[1][0][0]-lines[0][0][0],17.5,places=8)
        borders=list(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="PANEL_BORDER"]'))
        sheet=list(borders[0].get_points())
        self.assertAlmostEqual(max(p[0] for p in sheet)-min(p[0] for p in sheet),17.,places=8)
        self.assertAlmostEqual(max(p[1] for p in sheet)-min(p[1] for p in sheet),11.,places=8)

    def test_subset_numbering_and_saved_ids_are_preserved(self):
        from dataclasses import replace,asdict
        db=sample();db.header.start_panel_no=10
        db.panels.append(replace(db.panels[0],panel=8,panel_x=20.,panel_y=5.))
        saved=[asdict(p) for p in db.panels]
        render_profile_pdf_from_db(str(self.pdf),db,panels=[db.panels[1]])
        page,_,_=pdf_geometry(self.pdf)
        self.assertIn("PANEL 8",page.extract_text())
        self.assert_pdf_dimensions(db.panels[1])
        export_dxf(self.dxf,db,panels=[db.panels[1]])
        doc=ezdxf.readfile(self.dxf)
        self.assertIn("PANEL 8",[e.dxf.text for e in doc.modelspace().query("TEXT")])
        line=list(doc.modelspace().query('LWPOLYLINE[layer=="TEST"]'))[0]
        self.assertAlmostEqual(line[0][0],21.75,places=8)
        self.assertEqual([asdict(p) for p in db.panels],saved)
        self.assertEqual(db.header.start_panel_no,10)

    def test_saved_panel_ids_and_suffix_survive_selected_multipage_pdf(self):
        from dataclasses import replace
        first = PanelDef(27, 0., 6500., 100., 145., h_scale=500., v_scale=5.)
        second = replace(first, panel=37)
        db = sample(first)
        render_profile_pdf_from_db(
            str(self.pdf), db, panels=[first, second],
            drawing_config=DrawingBuildConfig(panel_suffix="P"))
        pages = PdfReader(self.pdf).pages
        self.assertEqual(len(pages), 2)
        self.assertIn("PANEL 27P", pages[0].extract_text())
        self.assertIn("PANEL 37P", pages[1].extract_text())

    def test_dxf_uses_saved_panel_id_and_configurable_suffix(self):
        panel = PanelDef(27, 0., 6500., 100., 145., h_scale=500., v_scale=5., panel_suffix="p")
        export_dxf(self.dxf, sample(panel), panels=[panel])
        text = [entity.dxf.text for entity in ezdxf.readfile(self.dxf).modelspace().query("TEXT")]
        self.assertIn("PANEL 27P", text)
        self.assertNotIn("PANEL 1 OF 1", text)

    def test_export_route_preserves_saved_suffixes_and_canonical_subset_order(self):
        from copy import deepcopy
        from dataclasses import asdict
        from backend.app.routers.hecras_api import _SESSIONS
        from backend.app.routers.report import ExportOptions, export_profile

        first = PanelDef(27, 0., 100., 0., 20., h_scale=50., v_scale=5., panel_suffix="p")
        middle = PanelDef(29, 100., 150., 0., 20., h_scale=25., v_scale=5.)
        last = PanelDef(37, 150., 200., 0., 20., h_scale=25., v_scale=5., panel_suffix="q")
        db = sample(first)
        db.panels = [first, middle, last]
        db.working = [
            WorkingRow(id=index + 1, river="Synthetic", reach="Reach", rs=str(index),
                       cumdist=station, minchel=elevation - 2,
                       profiles={"Test": elevation})
            for index, (station, elevation) in enumerate((
                (0., 10.), (50., 12.), (100., 14.), (125., 15.),
                (150., 16.), (175., 17.), (200., 18.),
            ))
        ]
        saved_before = [asdict(panel) for panel in db.panels]
        sid = "suffix-export-route"
        _SESSIONS[sid] = {"db": db}
        self.addCleanup(lambda: _SESSIONS.pop(sid, None))

        paths = {}
        for format_name in ("pdf", "dxf"):
            result = export_profile(ExportOptions(
                session_id=sid, format=format_name, panel_numbers=[37, 27],
                profile_names=["Test"], use_current_view=False))
            self.assertEqual(result["panel_count"], 2)
            paths[format_name] = Path(result["path"])

        pages = PdfReader(paths["pdf"]).pages
        self.assertEqual(len(pages), 2)
        self.assertIn("PANEL 27P", pages[0].extract_text())
        self.assertIn("PANEL 37Q", pages[1].extract_text())

        document = ezdxf.readfile(paths["dxf"])
        identifiers = [entity.dxf.text for entity in document.modelspace().query('TEXT[layer=="PANEL_ID"]')]
        self.assertEqual(identifiers, ["PANEL 27P", "PANEL 37Q"])
        self.assertEqual(document.header["$INSUNITS"], 1)
        self.assertEqual(len(document.modelspace().query('LWPOLYLINE[layer=="TEST"]')), 2)

        consecutive = export_profile(ExportOptions(
            session_id=sid, format="pdf", panel_numbers=[29, 27],
            profile_names=["Test"], use_current_view=False))
        consecutive_pdf = PdfReader(consecutive["path"])
        self.assertEqual(len(consecutive_pdf.pages), 2)
        self.assertIn("PANEL 27P", consecutive_pdf.pages[0].extract_text())
        self.assertIn("PANEL 29", consecutive_pdf.pages[1].extract_text())
        self.assertEqual([asdict(panel) for panel in db.panels], saved_before)

    def test_selected_saved_subset_allows_omitted_ranges_but_full_layout_does_not(self):
        db = sample(PanelDef(27, 0., 75., 0., 20., h_scale=50., v_scale=5.))
        second = PanelDef(37, 100., 200., 0., 20., h_scale=50., v_scale=5.)
        db.panels = [db.panels[0], second]

        export_dxf(self.dxf, db, panels=[db.panels[0]], revision_interval=(0., 200.))
        self.assertTrue(self.dxf.exists())
        self.assertEqual(len(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="TEST"]')), 1)

        invalid = Path(self.tmp.name) / "incomplete-layout.dxf"
        with self.assertRaisesRegex(ValueError, "uncovered station intervals"):
            export_dxf(invalid, db, panels=db.panels, revision_interval=(0., 200.))
        self.assertFalse(invalid.exists())

    def test_stale_saved_subset_is_rejected_even_when_panel_values_are_copied(self):
        from copy import deepcopy
        from backend.app.core.state_contract import review_panels
        db = sample(PanelDef(27, 0., 100., 0., 20., h_scale=50., v_scale=5.))
        db.panels = [db.panels[0], PanelDef(29, 100., 200., 0., 20., h_scale=50., v_scale=5.)]
        review_panels(db)
        db.working[0].profiles["Test"] = 11.
        copied_panel = deepcopy(db.panels[0])
        with self.assertRaisesRegex(ValueError, "Saved panels need review"):
            export_dxf(self.dxf, db, panels=[copied_panel], revision_interval=(0., 200.))
        self.assertFalse(self.dxf.exists())

    def test_dxf_keeps_established_default_profile_layer_name(self):
        db = sample()
        db.profiles[0].layer = ""
        export_dxf(self.dxf, db)
        document = ezdxf.readfile(self.dxf)
        self.assertEqual(len(document.modelspace().query('LWPOLYLINE[layer=="PROF_Test"]')), 1)
        self.assertEqual(len(document.modelspace().query('LWPOLYLINE[layer=="PROFILE_Test"]')), 0)

    def test_dxf_vertical_and_horizontal_annotations_keep_source_anchors(self):
        panel = PanelDef(5, 0., 200., 0., 20., h_scale=50., v_scale=5.)
        db = sample(panel)
        db.landmarks = [
            Landmark(1, 75., 14., "Vertical note", orientation="Vertical"),
            Landmark(2, 125., 15., "Horizontal note", orientation="Horizontal"),
        ]
        export_dxf(self.dxf, db, panels=[panel])
        annotations = {
            entity.dxf.text: entity
            for entity in ezdxf.readfile(self.dxf).modelspace().query('TEXT[layer=="LANDMARK"]')
        }
        vertical = annotations["Vertical note"]
        horizontal = annotations["Horizontal note"]
        self.assertEqual(vertical.dxf.rotation, 90.)
        self.assertEqual(horizontal.dxf.rotation, 0.)
        self.assertAlmostEqual(vertical.dxf.insert.x, 1.75 + 75. / 50., places=8)
        self.assertAlmostEqual(vertical.dxf.insert.y, 1.0 + 14. / 5., places=8)
        self.assertAlmostEqual(horizontal.dxf.insert.x, 1.75 + 125. / 50., places=8)
        self.assertAlmostEqual(horizontal.dxf.insert.y, 1.0 + 15. / 5., places=8)

    def test_dxf_grid_coordinates_match_pdf_and_shared_definition(self):
        panel = PanelDef(27, 0., 6500., 100., 145., h_scale=500., v_scale=5.)
        db = sample(panel)
        render_profile_pdf_from_db(str(self.pdf), db)
        export_dxf(self.dxf, db, panels=[panel])
        document = ezdxf.readfile(self.dxf)
        drawing = build_engineering_drawing(db, [panel], revision=(0., 6500.))
        pdf_segments = pdf_grid_segments(self.pdf)

        for major, key, layer in ((True, "major", "GRID_MAJOR"),
                                  (False, "minor", "GRID_MINOR")):
            expected = set()
            for grid_line in drawing.panels[0].grid_lines:
                if grid_line.major != major:
                    continue
                if grid_line.axis == "STATION":
                    points = (
                        engineering_to_pdf_points(EngineeringPoint(grid_line.coordinate, panel.start_elevation), drawing.panels[0]),
                        engineering_to_pdf_points(EngineeringPoint(grid_line.coordinate, panel.end_elevation), drawing.panels[0]),
                    )
                else:
                    points = (
                        engineering_to_pdf_points(EngineeringPoint(panel.start_station, grid_line.coordinate), drawing.panels[0]),
                        engineering_to_pdf_points(EngineeringPoint(panel.end_station, grid_line.coordinate), drawing.panels[0]),
                    )
                expected.add(tuple(sorted((
                    (round(points[0].x, 2), round(points[0].y, 2)),
                    (round(points[1].x, 2), round(points[1].y, 2)),
                ))))
            self.assertEqual(dxf_grid_segments(document, layer), expected)
            self.assertEqual(pdf_segments[key], expected)
        self.assertTrue(all(line.dxf.lineweight == 25 for line in document.modelspace().query('LINE[layer=="GRID_MAJOR"]')))
        self.assertTrue(all(line.dxf.lineweight == 9 for line in document.modelspace().query('LINE[layer=="GRID_MINOR"]')))

    def test_multireach_pdf_dxf_share_profiles_annotations_and_presentation(self):
        panel = PanelDef(27, 0., 200., 0., 20., h_scale=50., v_scale=5.)
        db = sample(panel)
        db.header.stream_name = "Connected River"
        db.header.stream_distance_label = "upstream of junction"
        db.header.community = "Example Community"
        db.flooding_source = {"display_name": "Tributary A"}
        db.working = [
            WorkingRow(id=index + 1, river=river, reach=reach, rs=str(index),
                       cumdist=station, minchel=elevation - 3 if elevation is not None else 5.,
                       profiles={"Test": elevation}, label_letter=label)
            for index, (river, reach, station, elevation, label) in enumerate((
                ("Main", "Upper", 0., 10., "A"),
                ("Main", "Upper", 50., 12., "B"),
                ("Tributary", "Lower", 100., None, "C"),
                ("Tributary", "Lower", 150., 16., "D"),
                ("Tributary", "Lower", 200., 18., "E"),
            ))
        ]
        db.panels = [panel]
        config = DrawingBuildConfig(panel_suffix="P", agency="Validated Agency")
        render_profile_pdf_from_db(str(self.pdf), db, panels=[panel],
                                   revision_interval=(0., 200.), drawing_config=config)
        export_dxf(self.dxf, db, panels=[panel], revision_interval=(0., 200.),
                   drawing_config=config)
        pdf_text = PdfReader(self.pdf).pages[0].extract_text()
        document = ezdxf.readfile(self.dxf)
        entities = list(document.modelspace()
                        .query('LWPOLYLINE[layer=="TEST"]'))
        dxf_text = [entity.dxf.text for entity in document.modelspace().query("TEXT")]

        self.assertEqual(len(entities), 2)
        self.assertEqual(document.header["$INSUNITS"], 1)
        for expected in ("FLOOD PROFILES", "TRIBUTARY A", "CONNECTED RIVER",
                         "EXAMPLE COMMUNITY", "VALIDATED AGENCY", "PANEL 27P",
                         "VERTICAL DATUM: UNKNOWN", "Stream bed", "profile gaps are shown",
                         "Distance (Feet) above upstream of junction", "Elevation (UNKNOWN)"):
            self.assertIn(expected, pdf_text)
        for expected in ("FLOOD PROFILES", "TRIBUTARY A", "CONNECTED RIVER",
                         "EXAMPLE COMMUNITY", "VALIDATED AGENCY", "PANEL 27P",
                         "VERTICAL DATUM: UNKNOWN", "STREAM BED", "TEST",
                         "Distance (Feet) above upstream of junction", "Elevation (UNKNOWN)"):
            self.assertIn(expected, dxf_text)
        self.assertEqual(len(list(document.modelspace().query('LINE[layer=="CROSS_SECTIONS"]'))), 10)
        labels = list(document.modelspace().query('TEXT[layer=="XS_LABEL"]'))
        self.assertEqual(len(labels), 5)
        label_a = next(entity for entity in labels if entity.dxf.text == "A")
        self.assertAlmostEqual(label_a.dxf.insert.x, 1.75, places=8)
        self.assertAlmostEqual(label_a.dxf.insert.y, 1.0 + 10. / 5., places=8)

    def test_unlettered_cross_sections_export_locations_without_invented_labels(self):
        panel = PanelDef(8, 0., 200., 0., 20., h_scale=50., v_scale=5.)
        db = sample(panel)
        for row in db.working:
            row.label_letter = ""
        render_profile_pdf_from_db(str(self.pdf), db, panels=[panel])
        export_dxf(self.dxf, db, panels=[panel])
        dxf_labels = [entity.dxf.text for entity in ezdxf.readfile(self.dxf)
                      .modelspace().query('TEXT[layer=="XS_LABEL"]')]
        document = ezdxf.readfile(self.dxf)
        locations = list(document.modelspace().query('LINE[layer=="CROSS_SECTIONS"]'))
        expected_count = len([row for row in db.working if row.kind == "xs"])
        self.assertEqual(dxf_labels, [])
        self.assertEqual(len(locations), expected_count * 2)

    def test_shortened_panel_keeps_physical_width_and_blank_page_space(self):
        panel = PanelDef(19, 25., 125., 0., 20., h_scale=50., v_scale=5.)
        render_profile_pdf_from_db(str(self.pdf), sample(panel))
        page, rectangles, _ = pdf_geometry(self.pdf)
        self.assertAlmostEqual(float(page.mediabox.width), 17. * 72, places=3)
        self.assertAlmostEqual(float(page.mediabox.height), 11. * 72, places=3)
        self.assertTrue(any(
            all(abs(actual - expected) < .001 for actual, expected in zip(rectangle, (126., 72., 144., 288.)))
            for rectangle in rectangles), rectangles)
        self.assertLess(126. + 144., float(page.mediabox.width))

    def test_pdf_renders_vertical_annotation_at_source_text(self):
        from backend.app.core.rasplot_db import Landmark
        db = sample()
        db.landmarks = [Landmark(1, 3000., 120., "Reviewed vertical note", orientation="Vertical", adjusted=True)]
        render_profile_pdf_from_db(str(self.pdf), db)
        text = PdfReader(self.pdf).pages[0].extract_text()
        self.assertIn("Reviewed vertical note", text)

    def test_revision_interpolated_endpoints_agree_in_pdf_and_dxf(self):
        db=sample();revision=(1000.,5000.)
        render_profile_pdf_from_db(str(self.pdf),db,revision_interval=revision)
        points=self.assert_pdf_dimensions(db.panels[0])
        export_dxf(self.dxf,db,revision_interval=revision)
        line=list(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="TEST"]'))[0]
        pts=list(line.get_points())
        for point,station in zip(pts,revision):
            self.assertAlmostEqual(point[0],1.75+station/500.,places=8)
            self.assertAlmostEqual(point[1],1.+station/6500.*9.,places=8)
            self.assertTrue(any(abs(x-point[0]*72)<.001 and abs(y-point[1]*72)<.001 for x,y in points),points)

    def test_vertical_overflow_rejected_by_both_without_creating_files(self):
        db=sample();db.working[-1].profiles["Test"]=146.
        for writer,path in [(render_profile_pdf_from_db,self.pdf),(export_dxf,self.dxf)]:
            with self.subTest(writer=writer.__name__),self.assertRaisesRegex(ValueError,"Panel 3.*outside saved range"):
                writer(str(path),db)
            self.assertFalse(path.exists())

    def test_vertical_validation_uses_clipped_interpolated_revision(self):
        db=sample();db.working[-1].profiles["Test"]=190.
        # Out-of-range upstream values outside revision need not block this reach portion.
        render_profile_pdf_from_db(str(self.pdf),db,revision_interval=(0.,2000.))
        export_dxf(self.dxf,db,revision_interval=(0.,2000.))
        with self.assertRaisesRegex(ValueError,"outside saved range"):
            export_dxf(self.dxf,db,revision_interval=(0.,6000.))

    def test_selected_profiles_match_between_formats(self):
        db=sample();db.profiles.append(ProfileMeta(name="Excluded",profile_id="Excluded",key="Excluded",layer="EXCLUDED",color="#00FF00"))
        for row in db.working: row.profiles["Excluded"]=1000.
        render_profile_pdf_from_db(str(self.pdf),db,profile_names=["Test"])
        export_dxf(self.dxf,db,profile_names=["Test"])
        self.assertNotIn("Excluded",PdfReader(self.pdf).pages[0].extract_text())
        self.assertEqual(len(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="EXCLUDED"]')),0)
        for writer,path in [(render_profile_pdf_from_db,self.pdf),(export_dxf,self.dxf)]:
            with self.assertRaisesRegex(ValueError,"Excluded"):
                writer(str(path),db,profile_names=["Excluded"])

    def test_all_offered_versions_are_exact(self):
        for version,expected in [("R2013","AC1027"),("R2010","AC1024"),("R2007","AC1021"),("R2000","AC1015")]:
            with self.subTest(version=version):
                export_dxf(self.dxf,sample(),dxf_version=version)
                doc=ezdxf.readfile(self.dxf)
                self.assertEqual(doc.dxfversion,expected)
                self.assertEqual(doc.header["$INSUNITS"],1)
                self.assertEqual(doc.header["$MEASUREMENT"],0)

    def test_page_too_small_is_rejected_not_rescaled(self):
        for writer,path in [(render_profile_pdf_from_db,self.pdf),(export_dxf,self.dxf)]:
            with self.assertRaisesRegex(ValueError,"page at least"):
                writer(str(path),sample(),custom_page=(11.,8.5))

    def test_external_matplotlib_layout_defaults_cannot_change_scale(self):
        import matplotlib
        with matplotlib.rc_context({"figure.autolayout":True,"figure.constrained_layout.use":True,"savefig.bbox":"tight"}):
            render_profile_pdf_from_db(str(self.pdf),sample())
        self.assert_pdf_dimensions(sample().panels[0])
        page=PdfReader(self.pdf).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width),17.*72,places=3)

    def test_fractional_plot_grid_does_not_extend_outside_border(self):
        panel=PanelDef(1,0.,625.,100.,108.5,h_scale=100.,v_scale=2.)
        export_dxf(self.dxf,sample(panel))
        doc = ezdxf.readfile(self.dxf)
        grid_lines = list(doc.modelspace().query('LINE[layer=="GRID_MAJOR"]'))
        grid_lines += list(doc.modelspace().query('LINE[layer=="GRID_MINOR"]'))
        for line in grid_lines:
            for point in (line.dxf.start,line.dxf.end):
                self.assertTrue(1.75-1e-8 <= point.x <= 8.+1e-8)
                self.assertTrue(1.-1e-8 <= point.y <= 5.25+1e-8)

    def test_api_returns_actionable_errors(self):
        from fastapi import HTTPException
        from backend.app.routers.hecras_api import _SESSIONS
        from backend.app.routers.report import export_profile,ExportOptions
        db=sample();_SESSIONS["scale-test"]={"db":db}
        self.addCleanup(lambda:_SESSIONS.pop("scale-test",None))
        with self.assertRaises(HTTPException) as exc:
            export_profile(ExportOptions(session_id="scale-test",format="dxf",dxf_version="bad",panel_numbers=[3]))
        self.assertEqual(exc.exception.status_code,400)
        self.assertIn("version",exc.exception.detail)
        db.working[-1].profiles["Test"]=146.
        with self.assertRaises(HTTPException) as exc:
            export_profile(ExportOptions(session_id="scale-test",panel_numbers=[3]))
        self.assertEqual(exc.exception.status_code,400)
        self.assertIn("Panel Setup",exc.exception.detail)

    def test_selected_two_profiles_both_export(self):
        db=sample();db.profiles.append(ProfileMeta(name="Second",profile_id="Second",key="Second",layer="SECOND",selected=True,color="#0000FF"))
        for row in db.working: row.profiles["Second"]=110.
        render_profile_pdf_from_db(str(self.pdf),db)
        export_dxf(self.dxf,db)
        text=PdfReader(self.pdf).pages[0].extract_text()
        self.assertIn("Test","".join(text.split())); self.assertIn("Second",text)
        self.assertEqual(len(ezdxf.readfile(self.dxf).modelspace().query('LWPOLYLINE[layer=="SECOND"]')),1)

    def test_invalid_scales_rejected_before_automatic_generation(self):
        db=sample();db.panels=[]
        for value in [0.,-1.,float("nan"),float("inf")]:
            for writer,path in [(render_profile_pdf_from_db,self.pdf),(export_dxf,self.dxf)]:
                with self.subTest(value=value,writer=writer.__name__),self.assertRaisesRegex(ValueError,"scales"):
                    writer(str(path),db,h_scale=value)

    def test_saved_panel_subset_api_preserves_definition_and_scale(self):
        from dataclasses import asdict,replace
        from backend.app.routers.hecras_api import _SESSIONS
        from backend.app.routers.report import export_profile,ExportOptions
        db=sample();db.panels.append(replace(db.panels[0],panel=7,panel_x=30.,panel_y=12.))
        _SESSIONS["scale-subset"]={"db":db}
        self.addCleanup(lambda:_SESSIONS.pop("scale-subset",None))
        saved=[asdict(p) for p in db.panels]
        for fmt in ["pdf","dxf"]:
            result=export_profile(ExportOptions(session_id="scale-subset",format=fmt,panel_numbers=[7]))
            self.assertEqual(result["panel_count"],1)
            self.assertEqual(result["h_scale"],500.)
            self.assertEqual(result["v_scale"],5.)
        self.assertEqual([asdict(p) for p in db.panels],saved)

    def test_muncie_to_north_metadata_isolation(self):
        from backend.app.routers.hecras_api import open_source,OpenRequest,_SESSIONS
        root=Path(__file__).resolve().parents[2]
        muncie=root.parent/"Muncie/Muncie.p04.hdf"
        north=root/"RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"
        if not muncie.exists() or not north.exists(): self.skipTest("Real project fixtures unavailable")
        a=open_source(OpenRequest(path=str(muncie)))["session_id"]
        self.addCleanup(lambda:_SESSIONS.pop(a,None))
        _SESSIONS[a]["db"].header.community="MUNCIE, CITY OF"
        _SESSIONS[a]["db"].header.county="DELAWARE COUNTY"
        _SESSIONS[a]["db"].header.state="IN"
        b=open_source(OpenRequest(path=str(north)))["session_id"]
        self.addCleanup(lambda:_SESSIONS.pop(b,None))
        header=_SESSIONS[b]["db"].header
        self.assertEqual((header.community,header.county,header.state),("","",""))
        self.assertIn("WHITE TANKS",header.stream_name)
