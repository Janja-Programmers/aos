from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shorts.utils import decode_cursor, encode_cursor
from aos.services.shorts.errors import ShortsCursorError, ShortsError
from aos.services.shorts.endpoints import ENDPOINT_SPECS
from aos.services.shorts.validation import validate_public_kwargs


class TestShortsApiContracts(FrappeTestCase):
    def _source(self, relative: str) -> str:
        return Path(frappe.get_app_path("aos", *relative.split("/"))).read_text(encoding="utf-8")

    def test_transport_cmd_is_ignored_but_unknown_fields_are_rejected(self):
        spec = ENDPOINT_SPECS["toggle_like"]
        self.assertEqual(
            validate_public_kwargs({"cmd": "aos.api.v1.shorts.toggle_like", "short_id": "SHORT-2026-00137"}, spec),
            {"short_id": "SHORT-2026-00137"},
        )
        with self.assertRaises(ShortsError) as ctx:
            validate_public_kwargs({"short_id": "SHORT-2026-00137", "owner": "forged"}, spec)
        self.assertEqual(ctx.exception.code, "SHORTS_UNKNOWN_FIELD")

    def test_conflicting_aliases_and_malformed_ids_are_rejected(self):
        with self.assertRaises(ShortsError) as ctx:
            validate_public_kwargs(
                {"raw_video_media": "MEDIA-2026-00001", "media_id": "MEDIA-2026-00002"},
                ENDPOINT_SPECS["create_short"],
            )
        self.assertEqual(ctx.exception.code, "SHORTS_ALIAS_CONFLICT")
        with self.assertRaises(ShortsError) as ctx:
            validate_public_kwargs({"short_id": "../SHORT-1"}, ENDPOINT_SPECS["get_short"])
        self.assertEqual(ctx.exception.code, "SHORTS_INVALID_IDENTIFIER")

    def test_cursor_is_signed_and_tampering_is_rejected(self):
        with patch("aos.api.shorts.utils._cursor_secret", return_value=b"test-secret"):
            cursor = encode_cursor({"created_on": "2026-08-01 00:00:00", "name": "SHORT-2026-00137"})
            self.assertEqual(decode_cursor(cursor)["name"], "SHORT-2026-00137")
            replacement = "A" if cursor[-1] != "A" else "B"
            with self.assertRaises(ShortsCursorError):
                decode_cursor(cursor[:-1] + replacement)

    def test_all_feed_candidates_are_advisory_and_order_matches_cursor(self):
        source = self._source("api/shorts/feed.py")
        self.assertNotIn("if not candidate_ids", source)
        self.assertNotIn("s.name IN %(candidate_ids)s", source)
        self.assertIn("COALESCE(s.ranking_score, 0) DESC", source)
        self.assertIn("EXISTS (", source)
        self.assertIn("filter_viewable_rows", source)

    def test_v1_endpoints_have_strict_specs_and_rate_policy_entries(self):
        wrapper = ast.parse(self._source("api/v1/shorts/__init__.py"))
        public_names = {
            node.name
            for node in wrapper.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "_call"
        }
        self.assertEqual(public_names, set(ENDPOINT_SPECS))
        policy_path = Path(frappe.get_app_path("aos")).parent / "ci" / "public-endpoint-rate-limits.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        methods = {str(row.get("endpoint") or "") for row in policy}
        for name in public_names:
            self.assertIn(f"aos.api.v1.shorts.__init__.{name}", methods)

    def test_shorts_services_do_not_commit_or_full_rollback(self):
        roots = ["api/shorts", "services/shorts", "services/video_processing_service.py", "patches/v1_0/harden_shorts_subsystem.py"]
        offenders: list[str] = []
        for relative in roots:
            path = Path(frappe.get_app_path("aos", *relative.split("/")))
            files = [path] if path.is_file() else list(path.rglob("*.py"))
            for file in files:
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr == "commit":
                        offenders.append(f"{file}:{node.lineno}:commit")
                    if node.func.attr == "rollback" and not node.keywords:
                        offenders.append(f"{file}:{node.lineno}:full-rollback")
        self.assertEqual(offenders, [])

    def test_public_download_does_not_return_object_key(self):
        source = self._source("api/shorts/library.py")
        response_section = source[source.index('"Download URL generated."'):]
        self.assertNotIn('"download_file_key":', response_section)

    def test_processing_callback_is_generation_and_prefix_safe(self):
        source = self._source("services/video_processing_service.py")
        for token in (
            "job_generation",
            "SUPERSEDED",
            "SHORT_DELETED",
            "_validated_processed_keys",
            "validate_object_key",
        ):
            self.assertIn(token, source)
        self.assertNotIn('short.playback_url = str(payload.get("playback_url")', source)
