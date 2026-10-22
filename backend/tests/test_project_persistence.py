import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.app.core.project_file import (
    ProjectDocument, ProjectFileError, ProjectFileTooLargeError,
    SourceModifiedError, deserialize_project_document, fingerprint_source,
    read_project_file, serialize_project_document, verify_project_source,
    write_project_file,
)


def valid_document():
    source_hash = "a" * 64
    return {
        "format": "rasplot-project",
        "schema_version": 1,
        "project_id": "12345678-1234-5678-9234-567812345678",
        "saved_at_utc": "2026-10-16T12:00:00Z",
        "created_with": {"application_version": "0.2.0-dev", "build_id": "test"},
        "source": {
            "kind": "hec-ras-plan-hdf",
            "relative_path": "model/Project.p02.hdf",
            "absolute_path_hint": "C:\\Models\\Project.p02.hdf",
            "file_name": "Project.p02.hdf",
            "fingerprint": {
                "algorithm": "sha256", "sha256": source_hash,
                "size_bytes": 100, "mtime_ns": 200,
            },
            "model_identity": {
                "model_id": "model-identity", "ras_version": "6.4.1",
                "plan_identity": {}, "geometry_identity": {},
            },
        },
        "engineering_context": {
            "mode": "single_reach", "reach_id": "reach-1",
            "flooding_source": None, "stationing_start": 0.0,
        },
        "working_overrides": {
            "channel_lengths": [{
                "xs_identity": "xs-1", "source_value": 217.3,
                "working_value": 237.3,
            }],
        },
        "profiles": {
            "selected_profile_ids": ["profile-1"],
            "mappings": [], "user_adjustments": [],
        },
        "revision": {"explicit": True, "start_station": 0.0, "end_station": 6500.0},
        "panel_set": {
            "review_state": "REVIEWED",
            "review_evidence": {
                "source_sha256": source_hash,
                "panel_inputs_sha256": "b" * 64,
                "engineering_version": "version-1",
            },
            "panels": [{
                "panel": 1, "panel_suffix": "p", "start_station": 0.0,
                "end_station": 6500.0, "start_elevation": 5579.72,
                "end_elevation": 5624.72, "panel_x": None, "panel_y": None,
                "h_scale": 500.0, "v_scale": 5.0, "review_state": "REVIEWED",
            }],
        },
        "drawing_settings": {
            "page_size": "11x17", "distance_unit": "Feet",
            "station_origin": 0.0, "station_minor": 100.0,
            "station_major": 500.0, "elevation_origin": 0.0,
            "elevation_minor": 1.0, "elevation_major": 5.0, "agency": "",
        },
        "project_information": {
            "stream_name": "Stream", "community": "", "county": "",
            "state": "", "vertical_datum": "UNKNOWN",
            "datum_provenance": "UNKNOWN", "require_known_datum": False,
        },
    }


def extend_document(raw):
    raw["source"]["semantic_identity"] = {
        "contract_version": "rasplot-source-identity-v1",
        "model_id": "model-identity",
        "ras_version": "6.4.1",
        "unit_system": "US Customary",
        "plan_identity": {"Plan Name": "Synthetic"},
        "geometry_file_type": "RAS Geometry",
        "geometry_file_version": "6.4.1",
        "reaches": [{
            "reach_id": "reach-1", "river": "River", "reach": "Reach",
            "ordered_xs_ids": ["xs-1", "xs-2"],
        }],
        "profiles": [{
            "profile_id": "profile-1", "source_name": "1% Future",
            "result_type": "SOURCE_WSEL", "source_dataset": "Results/WSEL",
            "ordered_xs_ids": ["xs-1", "xs-2"],
        }],
        "structures": [{
            "structure_id": "structure-1", "river": "River", "reach": "Reach",
            "river_station": "900", "structure_type": "CULVERT",
            "source_record_id": "record-1",
        }],
        "connections": [],
    }
    raw["engineering_context"]["stationing_state"] = "FRESH"
    raw["working_overrides"].update({
        "elevations": [{
            "row_identity": "xs-1", "field": "MINCHEL",
            "original_value": 100.0, "working_value": 101.0,
            "provenance": "USER_ENTERED",
        }],
        "labels": [{"row_identity": "xs-1", "field": "LABEL_TEXT", "value": "Road"}],
        "fix_drawdowns_applied": True,
    })
    raw["profiles"] = {
        "selected_profile_ids": ["profile-1"],
        "mappings": [{
            "profile_id": "profile-1", "display_name": "Future condition",
            "frequency": "", "mapping_provenance": "USER_ENTERED",
            "color": "#196bd6", "linetype": "DASHED", "lineweight": 0.3,
            "legend_text": "Future condition", "layer": "PROF_FUTURE",
        }],
        "backwater": [{
            "profile_id": "profile-1", "elevation": 110.0,
            "text": "User-entered reference", "provenance": "USER_ENTERED_REFERENCE_ONLY",
        }],
        "user_adjustments": [{
            "xs_identity": "xs-1", "profile_id": "profile-1",
            "original_value": 109.0, "adjusted_value": 110.0,
            "reason": "USER_FIX_DRAWDOWNS", "provenance": "USER_ENTERED",
        }],
    }
    raw["panel_set"]["review_evidence"] = {
        "contract_version": "rasplot-panel-review-v1",
        "source_sha256": "a" * 64,
        "dependency_sha256": "c" * 64,
        "ordered_panel_definition_sha256": "d" * 64,
        "engineering_version": "diagnostic-only",
    }
    raw["drawing_settings"]["use_node_names"] = True
    raw["project_information"].update({
        "stream_distance_label": "mouth", "start_panel_no": 4,
        "table_name": "Table 24", "table_title": "FLOODWAY DATA",
    })
    raw["reviewed_annotations"] = [{
        "annotation_id": 1, "distance": 1200.0, "elevation": 115.0,
        "text": "Reviewed road crossing", "orientation": "Vertical",
        "underline": True, "adjusted": True, "claimed_review_state": "REVIEWED",
    }]
    return raw


class ProjectPersistenceFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_valid_document_round_trip_is_canonical_and_safe(self):
        document = deserialize_project_document(json.dumps(valid_document()))
        self.assertIsInstance(document, ProjectDocument)
        self.assertEqual(document.panel_set.panels[0].panel_suffix, "P")
        encoded = serialize_project_document(document)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(deserialize_project_document(encoded), document)

    def test_legacy_missing_suffix_defaults_to_blank(self):
        raw = valid_document()
        del raw["panel_set"]["panels"][0]["panel_suffix"]
        document = deserialize_project_document(json.dumps(raw))
        self.assertEqual(document.panel_set.panels[0].panel_suffix, "")

    def test_schema_version_unknown_fields_and_corrupt_json_are_rejected(self):
        raw = valid_document()
        raw["schema_version"] = 2
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = valid_document(); raw["unexpected"] = True
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        for content in (b"{broken", b"\xff\xfe", b"[]"):
            with self.assertRaises(ProjectFileError):
                deserialize_project_document(content)

    def test_panel_ids_suffix_bounds_and_scales_are_strict(self):
        changes = [
            ("panel", 0), ("panel", "1"), ("panel_suffix", "P1"),
            ("panel_suffix", "ABCD"), ("end_station", float("inf")),
            ("end_station", 0.0), ("end_elevation", 5579.72),
            ("h_scale", 0.0), ("v_scale", -1.0),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                raw = valid_document()
                raw["panel_set"]["panels"][0][field] = value
                with self.assertRaises(ProjectFileError):
                    deserialize_project_document(json.dumps(raw))

    def test_duplicate_identities_are_rejected(self):
        raw = valid_document()
        raw["panel_set"]["panels"].append(dict(raw["panel_set"]["panels"][0]))
        with self.assertRaisesRegex(ProjectFileError, "Panel IDs must be unique"):
            deserialize_project_document(json.dumps(raw))
        raw = valid_document()
        raw["profiles"]["selected_profile_ids"] = ["profile-1", "profile-1"]
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = valid_document()
        raw["working_overrides"]["channel_lengths"].append(
            dict(raw["working_overrides"]["channel_lengths"][0]))
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))

    def test_malformed_paths_are_rejected(self):
        mutations = [
            ("relative_path", "../Project.p02.hdf"),
            ("relative_path", "C:\\Models\\Project.p02.hdf"),
            ("absolute_path_hint", "relative/Project.p02.hdf"),
            ("file_name", "folder/Project.p02.hdf"),
            ("relative_path", "model/bad\x00Project.p02.hdf"),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                raw = valid_document(); raw["source"][field] = value
                with self.assertRaises(ProjectFileError):
                    deserialize_project_document(json.dumps(raw))
        raw = valid_document()
        raw["source"]["relative_path"] = None
        raw["source"]["absolute_path_hint"] = None
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))

    def test_project_file_size_limit_and_invalid_file(self):
        path = self.root / "large.rasplot.json"
        path.write_bytes(b"x" * 20)
        with self.assertRaises(ProjectFileTooLargeError):
            read_project_file(path, max_bytes=10)
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ProjectFileError):
            read_project_file(path)

    def test_streaming_source_fingerprint(self):
        path = self.root / "Project.p02.hdf"
        content = (b"0123456789abcdef" * 200000) + b"end"
        path.write_bytes(content)
        result = fingerprint_source(path, chunk_bytes=4096)
        self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(result.size_bytes, len(content))
        self.assertEqual(result.mtime_ns, path.stat().st_mtime_ns)

    def test_source_modification_during_hash_is_detected(self):
        path = self.root / "Project.p02.hdf"
        path.write_bytes(b"source")
        original_stat = Path.stat
        calls = 0

        def changing_stat(instance, *args, **kwargs):
            nonlocal calls
            result = original_stat(instance, *args, **kwargs)
            if instance == path:
                calls += 1
                if calls >= 3:
                    values = list(result)
                    values[8] = result.st_mtime + 1
                    return os.stat_result(values)
            return result

        with patch.object(Path, "stat", changing_stat):
            with self.assertRaises(SourceModifiedError):
                fingerprint_source(path, chunk_bytes=2)

    def test_atomic_write_refuses_implicit_overwrite_and_reads_back(self):
        path = self.root / "saved.rasplot.json"
        first = deserialize_project_document(json.dumps(valid_document()))
        write_project_file(path, first)
        original = path.read_bytes()
        with self.assertRaisesRegex(ProjectFileError, "already exists"):
            write_project_file(path, first)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(read_project_file(path), first)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_failed_atomic_replace_preserves_existing_file_and_cleans_temp(self):
        path = self.root / "saved.rasplot.json"
        path.write_bytes(b"original")
        document = deserialize_project_document(json.dumps(valid_document()))
        with patch("backend.app.core.project_file.os.replace", side_effect=OSError("denied")):
            with self.assertRaisesRegex(ProjectFileError, "atomically"):
                write_project_file(path, document, overwrite=True)
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_wrong_extension_and_missing_directory_are_rejected(self):
        document = deserialize_project_document(json.dumps(valid_document()))
        with self.assertRaises(ProjectFileError):
            write_project_file(self.root / "project.json", document)
        with self.assertRaises(ProjectFileError):
            write_project_file(self.root / "missing" / "project.rasplot.json", document)

    def test_extended_durable_state_round_trip(self):
        document = deserialize_project_document(json.dumps(extend_document(valid_document())))
        self.assertEqual(document.source.semantic_identity.contract_version,
                         "rasplot-source-identity-v1")
        self.assertEqual(document.engineering_context.stationing_state, "FRESH")
        self.assertEqual(document.working_overrides.elevations[0].working_value, 101.0)
        self.assertEqual(document.working_overrides.labels[0].value, "Road")
        self.assertTrue(document.working_overrides.fix_drawdowns_applied)
        self.assertEqual(document.profiles.mappings[0].layer, "PROF_FUTURE")
        self.assertEqual(document.profiles.backwater[0].elevation, 110.0)
        self.assertEqual(document.profiles.user_adjustments[0].reason,
                         "USER_FIX_DRAWDOWNS")
        self.assertEqual(document.reviewed_annotations[0].text,
                         "Reviewed road crossing")
        self.assertTrue(document.drawing_settings.use_node_names)
        self.assertEqual(deserialize_project_document(
            serialize_project_document(document)), document)

    def test_semantic_identity_is_strict_and_versioned(self):
        raw = extend_document(valid_document())
        raw["source"]["semantic_identity"]["unknown"] = True
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["source"]["semantic_identity"]["contract_version"] = "future-v2"
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["source"]["semantic_identity"]["reaches"][0]["ordered_xs_ids"] = ["xs-1", "xs-1"]
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["source"]["semantic_identity"]["profiles"][0]["profile_id"] = "unknown-profile"
        with self.assertRaisesRegex(ProjectFileError, "unknown semantic profile"):
            deserialize_project_document(json.dumps(raw))

    def test_reviewed_annotations_are_validated(self):
        raw = extend_document(valid_document())
        raw["reviewed_annotations"].append(dict(raw["reviewed_annotations"][0]))
        with self.assertRaisesRegex(ProjectFileError, "Annotation identities must be unique"):
            deserialize_project_document(json.dumps(raw))
        for field, value in (("distance", float("nan")), ("elevation", float("inf")),
                             ("text", "  "), ("orientation", "Diagonal")):
            with self.subTest(field=field):
                raw = extend_document(valid_document())
                raw["reviewed_annotations"][0][field] = value
                with self.assertRaises(ProjectFileError):
                    deserialize_project_document(json.dumps(raw))

    def test_working_table_edits_are_strict_and_unique(self):
        raw = extend_document(valid_document())
        raw["working_overrides"]["elevations"].append(
            dict(raw["working_overrides"]["elevations"][0]))
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["working_overrides"]["labels"].append(
            dict(raw["working_overrides"]["labels"][0]))
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["profiles"]["user_adjustments"].append(
            dict(raw["profiles"]["user_adjustments"][0]))
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["working_overrides"]["elevations"][0]["working_value"] = float("inf")
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["working_overrides"]["elevations"][0].update(
            field="LOW_CHORD", working_value=None)
        with self.assertRaisesRegex(ProjectFileError, "clearing is not"):
            deserialize_project_document(json.dumps(raw))

    def test_fresh_and_dirty_stationing_combinations(self):
        fresh = deserialize_project_document(json.dumps(extend_document(valid_document())))
        self.assertEqual(fresh.engineering_context.stationing_state, "FRESH")
        raw = extend_document(valid_document())
        raw["engineering_context"]["stationing_state"] = "DIRTY"
        raw["panel_set"]["review_state"] = "REVIEW_REQUIRED"
        raw["panel_set"]["panels"][0]["review_state"] = "REVIEW_REQUIRED"
        dirty = deserialize_project_document(json.dumps(raw))
        self.assertEqual(dirty.engineering_context.stationing_state, "DIRTY")
        raw = extend_document(valid_document())
        raw["engineering_context"]["stationing_state"] = "DIRTY"
        with self.assertRaisesRegex(ProjectFileError, "Dirty stationing"):
            deserialize_project_document(json.dumps(raw))

    def test_review_evidence_contracts_and_required_fields(self):
        legacy = deserialize_project_document(json.dumps(valid_document()))
        self.assertEqual(legacy.panel_set.review_evidence.contract_version,
                         "legacy-runtime-v0")
        current = deserialize_project_document(json.dumps(extend_document(valid_document())))
        self.assertEqual(current.panel_set.review_evidence.contract_version,
                         "rasplot-panel-review-v1")
        for missing in ("dependency_sha256", "ordered_panel_definition_sha256"):
            raw = extend_document(valid_document())
            del raw["panel_set"]["review_evidence"][missing]
            with self.assertRaises(ProjectFileError):
                deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["panel_set"]["review_evidence"]["contract_version"] = "future-review-v2"
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = valid_document()
        del raw["panel_set"]["review_evidence"]["panel_inputs_sha256"]
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["panel_set"]["review_evidence"]["source_sha256"] = "e" * 64
        with self.assertRaisesRegex(ProjectFileError, "must match"):
            deserialize_project_document(json.dumps(raw))

    def test_fix_drawdowns_state_must_match_adjustments(self):
        raw = extend_document(valid_document())
        raw["working_overrides"]["fix_drawdowns_applied"] = False
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))
        raw = extend_document(valid_document())
        raw["profiles"]["user_adjustments"] = []
        with self.assertRaises(ProjectFileError):
            deserialize_project_document(json.dumps(raw))

    def test_canonical_serialization_is_deterministic(self):
        first = extend_document(valid_document())
        second = json.loads(json.dumps(first))
        second["source"]["semantic_identity"]["plan_identity"] = {
            "z": "last", "a": "first"
        }
        first["source"]["semantic_identity"]["plan_identity"] = {
            "a": "first", "z": "last"
        }
        first_bytes = serialize_project_document(first)
        second_bytes = serialize_project_document(second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_bytes, serialize_project_document(
            deserialize_project_document(first_bytes)))


class ProjectSourceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_dir = self.root / "portable"
        self.project_dir.mkdir()
        self.project_path = self.project_dir / "profile.rasplot.json"
        self.source_path = self.project_dir / "model" / "Project.p02.hdf"
        self.source_path.parent.mkdir()
        self.source_path.write_bytes(b"verified synthetic HDF bytes")
        raw = valid_document()
        actual = fingerprint_source(self.source_path, chunk_bytes=3)
        raw["source"]["fingerprint"] = actual.model_dump()
        raw["panel_set"]["review_evidence"]["source_sha256"] = actual.sha256
        raw["source"]["relative_path"] = "model/Project.p02.hdf"
        raw["source"]["absolute_path_hint"] = str(self.source_path.resolve())
        self.document = deserialize_project_document(json.dumps(raw))

    def test_same_location_source_is_verified(self):
        result = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(Path(result.resolved_path), self.source_path.resolve())
        self.assertEqual(result.resolution_origin, "PROJECT_RELATIVE")
        self.assertFalse(result.relocated)
        self.assertTrue(result.semantic_identity_required)

    def test_project_and_hdf_moved_together_use_relative_path(self):
        moved = self.root / "moved"
        moved_source = moved / "model" / "Project.p02.hdf"
        moved_source.parent.mkdir(parents=True)
        moved_source.write_bytes(self.source_path.read_bytes())
        self.source_path.unlink()
        result = verify_project_source(moved / "profile.rasplot.json", self.document.source)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.resolution_origin, "PROJECT_RELATIVE")
        self.assertTrue(result.relocated)

    def test_separately_relocated_hdf_uses_explicit_replacement(self):
        replacement = self.root / "other location" / "Project.p02.hdf"
        replacement.parent.mkdir()
        replacement.write_bytes(self.source_path.read_bytes())
        self.source_path.unlink()
        result = verify_project_source(
            self.project_path, self.document.source, replacement_path=replacement.resolve())
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.resolution_origin, "CALLER_REPLACEMENT")
        self.assertTrue(result.relocated)

    def test_missing_source_is_explicit(self):
        self.source_path.unlink()
        result = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(result.status, "SOURCE_MISSING")
        self.assertIsNone(result.fingerprint)
        self.assertTrue(result.warnings)

    def test_modified_source_is_never_verified(self):
        self.source_path.write_bytes(b"modified")
        result = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(result.status, "SOURCE_CHANGED")
        self.assertIsNone(result.fingerprint)

    def test_same_filename_with_different_content_is_changed(self):
        replacement = self.root / "replacement" / self.source_path.name
        replacement.parent.mkdir()
        replacement.write_bytes(b"different file with same name")
        self.source_path.unlink()
        result = verify_project_source(
            self.project_path, self.document.source, replacement_path=replacement.resolve())
        self.assertEqual(result.status, "SOURCE_CHANGED")
        self.assertNotEqual(result.attempts[-1].status, "VERIFIED")

    def test_invalid_replacement_path_is_not_read(self):
        with patch("backend.app.core.project_file.fingerprint_source") as fingerprint:
            fingerprint.return_value = self.document.source.fingerprint
            result = verify_project_source(
                self.project_path, self.document.source,
                replacement_path="relative/Unsafe.p02.hdf")
        self.assertEqual(result.status, "VERIFIED")
        fingerprint.assert_called_once_with(self.source_path.resolve())
        self.assertTrue(any(attempt.status == "SOURCE_UNREADABLE" for attempt in result.attempts))

    def test_unreadable_source_is_distinct(self):
        with patch("backend.app.core.project_file.fingerprint_source",
                   side_effect=ProjectFileError("access denied")):
            result = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(result.status, "SOURCE_UNREADABLE")
        self.assertTrue(any("access denied" in warning for warning in result.warnings))

    def test_source_changing_during_hash_is_unstable(self):
        with patch("backend.app.core.project_file.fingerprint_source",
                   side_effect=SourceModifiedError("changed during hash")):
            result = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(result.status, "SOURCE_UNSTABLE")
        self.assertTrue(any("changed during hash" in warning for warning in result.warnings))

    def test_resolution_priority_prefers_verified_relative_candidate(self):
        absolute = self.root / "absolute" / "Project.p02.hdf"
        replacement = self.root / "replacement" / "Project.p02.hdf"
        absolute.parent.mkdir(); replacement.parent.mkdir()
        absolute.write_bytes(self.source_path.read_bytes())
        replacement.write_bytes(self.source_path.read_bytes())
        raw = self.document.model_dump(mode="json")
        raw["source"]["absolute_path_hint"] = str(absolute.resolve())
        source = deserialize_project_document(json.dumps(raw)).source
        result = verify_project_source(
            self.project_path, source, replacement_path=replacement.resolve())
        self.assertEqual(result.resolution_origin, "PROJECT_RELATIVE")
        self.assertEqual(len(result.attempts), 1)

    def test_changed_higher_priority_candidate_can_fall_through_to_verified_hint(self):
        absolute = self.root / "absolute" / "Project.p02.hdf"
        absolute.parent.mkdir()
        absolute.write_bytes(self.source_path.read_bytes())
        self.source_path.write_bytes(b"changed relative candidate")
        raw = self.document.model_dump(mode="json")
        raw["source"]["absolute_path_hint"] = str(absolute.resolve())
        source = deserialize_project_document(json.dumps(raw)).source
        result = verify_project_source(self.project_path, source)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.resolution_origin, "ABSOLUTE_HINT")
        self.assertEqual([attempt.status for attempt in result.attempts],
                         ["SOURCE_CHANGED", "VERIFIED"])

    def test_verification_is_session_independent(self):
        first = verify_project_source(self.project_path, self.document.source)
        second = verify_project_source(self.project_path, self.document.source)
        self.assertEqual(first, second)
        self.assertEqual(first.status, "VERIFIED")


if __name__ == "__main__":
    unittest.main()
