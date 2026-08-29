from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shorts.management import get_short_impl, my_shorts_impl
from aos.api.shorts.sounds import list_sounds_impl, my_favorite_sounds_impl, search_sounds_impl
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

    def test_create_short_accepts_reusable_sound_selection(self):
        clean = validate_public_kwargs(
            {
                "raw_video_media": "MEDIA-2026-00001",
                "sound_id": "SOUND-2026-00001",
                "sound_start_ms": 1000,
                "sound_duration_ms": 5000,
                "sound_volume": 0.7,
            },
            ENDPOINT_SPECS["create_short"],
        )

        self.assertEqual(clean["sound_id"], "SOUND-2026-00001")
        self.assertEqual(clean["sound_start_ms"], 1000)
        self.assertEqual(clean["sound_duration_ms"], 5000)
        self.assertEqual(clean["sound_volume"], 0.7)

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

    def test_authenticated_short_status_rate_limit_is_user_scoped(self):
        limited = {
            "ok": False,
            "message": "Too many requests. Please try again shortly.",
            "error": "RATE_LIMIT",
            "data": {},
        }
        with (
            patch("aos.api.shorts.management._get_optional_viewer", return_value="user@example.test"),
            patch("aos.api.shorts.management.rate_limit", return_value=limited) as limiter,
        ):
            self.assertEqual(get_short_impl(short_id="SHORT-2026-00137"), limited)

        key = limiter.call_args.kwargs["key"]
        self.assertTrue(key.startswith("aos:rl:shorts:get:user:sha256:"), key)
        self.assertNotIn("user@example.test", key)

    def test_authenticated_sound_search_is_user_scoped_and_ranked(self):
        captured: dict[str, object] = {}

        def fake_sql(query, params=None, **kwargs):
            captured["query"] = query
            captured["params"] = tuple(params or ())
            return []

        with (
            patch("aos.api.shorts.sounds._get_optional_viewer", return_value="user@example.test"),
            patch("aos.api.shorts.sounds.rate_limit", return_value=None) as limiter,
            patch("aos.api.shorts.sounds.frappe.db.sql", side_effect=fake_sql),
        ):
            response = search_sounds_impl(q="Math", limit=20)

        self.assertTrue(response.get("ok"), response)
        key = limiter.call_args.kwargs["key"]
        self.assertTrue(key.startswith("aos:rl:shorts:sounds:search:user:sha256:"), key)
        self.assertNotIn("user@example.test", key)
        self.assertIn("CASE", str(captured["query"]))
        self.assertEqual(captured["params"], ("original", "%Math%", "%Math%", "Math", "Math%", "Math", "Math%", 20))

    def test_sound_catalog_discovery_excludes_original_audio(self):
        captured: list[tuple[str, tuple[object, ...]]] = []

        def fake_sql(query, params=None, **kwargs):
            captured.append((str(query), tuple(params or ())))
            return []

        with (
            patch("aos.api.shorts.sounds._get_optional_viewer", return_value=None),
            patch("aos.api.shorts.sounds.require_login", return_value=("user@example.test", None)),
            patch("aos.api.shorts.sounds.rate_limit", return_value=None),
            patch("aos.api.shorts.sounds.frappe.db.sql", side_effect=fake_sql),
        ):
            listed = list_sounds_impl(limit=20)
            searched = search_sounds_impl(q="original", limit=20)
            favorites = my_favorite_sounds_impl(limit=20)

        self.assertTrue(listed.get("ok"), listed)
        self.assertTrue(searched.get("ok"), searched)
        self.assertTrue(favorites.get("ok"), favorites)
        self.assertEqual(len(captured), 3)

        list_query, list_params = captured[0]
        search_query, search_params = captured[1]
        favorite_query, favorite_params = captured[2]

        self.assertIn("AND source_type <> %s", list_query)
        self.assertEqual(list_params[0], "original")
        self.assertIn("AND source_type <> %s", search_query)
        self.assertEqual(search_params[0], "original")
        self.assertIn("AND snd.source_type <> %s", favorite_query)
        self.assertEqual(favorite_params[1], "original")

    def test_original_sound_remains_available_from_short_context(self):
        source = self._source("api/shorts/sounds.py")
        start = source.index("def get_short_sound_map")
        end = source.index("def set_short_sound", start)
        short_context = source[start:end]
        self.assertIn("ss.is_original_audio", short_context)
        self.assertNotIn("snd.source_type <> %s", short_context)

    def test_sound_search_sql_executes_on_mariadb(self):
        # Regression: the backslash escape literal must be encoded as two SQL
        # backslashes so MariaDB receives one valid ESCAPE character.
        with (
            patch("aos.api.shorts.sounds._get_optional_viewer", return_value=None),
            patch("aos.api.shorts.sounds.rate_limit", return_value=None),
        ):
            response = search_sounds_impl(q="test", limit=1)

        self.assertTrue(response.get("ok"), response)

    def test_all_feed_candidates_are_advisory_and_order_matches_cursor(self):
        source = self._source("api/shorts/feed.py")
        self.assertNotIn("if not candidate_ids", source)
        self.assertNotIn("s.name IN %(candidate_ids)s", source)
        self.assertIn("COALESCE(s.ranking_score, 0) DESC", source)
        self.assertIn("EXISTS (", source)
        self.assertIn("filter_viewable_rows", source)

    def test_my_shorts_profile_scope_is_strictly_supported(self):
        spec = ENDPOINT_SPECS["my_shorts"]
        self.assertEqual(
            validate_public_kwargs({"scope": "private", "limit": 12}, spec),
            {"scope": "private", "limit": 12},
        )

        queries: list[str] = []

        def fake_sql(query, *args, **kwargs):
            queries.append(str(query))
            return []

        with (
            patch("aos.api.shorts.management.require_login", return_value=("user@example.test", None)),
            patch("aos.api.shorts.management.rate_limit", return_value=None),
            patch("aos.api.shorts.management.frappe.db.sql", side_effect=fake_sql),
        ):
            private_response = my_shorts_impl(scope="private", limit=12)
            posts_response = my_shorts_impl(scope="posts", limit=12)
            invalid_response = my_shorts_impl(scope="public", limit=12)

        self.assertTrue(private_response.get("ok"), private_response)
        self.assertTrue(posts_response.get("ok"), posts_response)
        self.assertEqual(invalid_response.get("error"), "VALIDATION_ERROR")
        self.assertIn("s.audience = 'only_me'", queries[0])
        self.assertIn("s.audience != 'only_me'", queries[1])

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
        roots = [
            "api/shorts",
            "services/shorts",
            "services/video_processing_service.py",
            "patches/v1_0/harden_shorts_subsystem.py",
            "patches/v1_0/install_shorts_indexes.py",
            "patches/v1_0/initialize_short_classification_metadata.py",
        ]
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

    def test_content_mode_is_server_classified_not_creator_controlled(self):
        source = self._source("api/shorts/upload.py")
        update_section = source[source.index("def update_short_metadata_impl"): ]
        self.assertIn("classify_for_publish", update_section)
        self.assertIn("legacy_content_mode=kwargs.get(\"content_mode\")", update_section)
        self.assertNotIn("validate_content_mode(kwargs.get(\"content_mode\"))", update_section)
        self.assertIn("has_shop_context=bool(doc.ad)", update_section)

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
