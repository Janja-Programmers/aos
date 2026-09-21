from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest import TestCase

from aos.services.shorts.endpoints import ENDPOINT_SPECS
from aos.services.shorts.identity import SHORT_ID_RE, SOUND_ID_RE, generate_short_id, generate_sound_id
from aos.services.media.media_purposes import MEDIA_PURPOSES

ROOT = Path(__file__).resolve().parents[2]


class TestShortsHardenedContract(TestCase):
    def test_public_ids_are_opaque(self):
        short_id = generate_short_id()
        sound_id = generate_sound_id()
        self.assertRegex(short_id, SHORT_ID_RE)
        self.assertRegex(sound_id, SOUND_ID_RE)
        self.assertNotEqual(short_id, generate_short_id())
        self.assertNotEqual(sound_id, generate_sound_id())
        self.assertNotRegex(short_id, r"20\d\d|00001")

    def test_client_cannot_author_server_owned_classification_or_identity(self):
        create = ENDPOINT_SPECS["create_short"].fields
        update = ENDPOINT_SPECS["update_short"].fields
        for forbidden in {"owner", "creator", "content_mode", "modes", "moderation_status", "processing_status", "lifecycle_status", "ranking_score"}:
            self.assertNotIn(forbidden, create)
            self.assertNotIn(forbidden, update)

    def test_explicit_actions_replace_toggle_contracts(self):
        for name in ("like_short", "unlike_short", "save_short", "unsave_short", "repost_short", "undo_repost_short"):
            self.assertIn(name, ENDPOINT_SPECS)
        self.assertFalse(any(name.startswith("toggle") for name in ENDPOINT_SPECS))
        self.assertNotIn("feed_by_ad", ENDPOINT_SPECS)

    def test_media_purposes_cover_native_photo_and_derived_video_assets(self):
        for purpose in (
            "short_video_raw", "short_photo", "short_video_playback", "short_video_manifest",
            "short_poster", "short_storyboard", "short_storyboard_manifest", "short_download", "short_original_audio",
        ):
            self.assertIn(purpose, MEDIA_PURPOSES)
        self.assertTrue(MEDIA_PURPOSES["short_download"].is_private)
        self.assertFalse(MEDIA_PURPOSES["short_video_playback"].client_upload_allowed)
        self.assertIn("video/webm", MEDIA_PURPOSES["short_video_raw"].allowed_content_types)
        self.assertIn(".webm", MEDIA_PURPOSES["short_video_raw"].allowed_extensions)

    def test_short_schema_has_independent_state_machines(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_short/aos_short.json").read_text())
        fields = {f["fieldname"]: f for f in schema["fields"]}
        for name in ("lifecycle_status", "processing_status", "moderation_status", "processing_generation", "moderation_generation"):
            self.assertIn(name, fields)
        for name in ("visibility_status", "approval_status", "content_mode", "audio_mix_status", "file_key", "playback_url"):
            self.assertNotIn(name, fields)

    def test_sound_schema_has_no_commercial_product_flag(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_sound/aos_sound.json").read_text())
        fields = {f["fieldname"] for f in schema["fields"]}
        self.assertNotIn("is_commercial", fields)
        self.assertNotIn("is_commercial_safe", fields)

    def test_video_processing_is_not_a_v1_client_namespace(self):
        legacy = ROOT / "aos/api/v1/video_processing"
        self.assertFalse((legacy / "__init__.py").exists())
        self.assertFalse(any(legacy.glob("*.py")) if legacy.exists() else False)
        internal = ROOT / "aos/api/internal/video_processing/__init__.py"
        self.assertTrue(internal.exists())
        self.assertIn("handle_callback", internal.read_text())

    def test_shorts_tests_do_not_commit_transaction_local_fixtures(self):
        offenders = []
        for file in sorted((ROOT / "aos/tests").glob("test_shorts*.py")):
            tree = ast.parse(file.read_text(), filename=str(file))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit"
                ):
                    offenders.append(f"{file.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_short_request_flow_has_no_manual_commit(self):
        roots = [ROOT / "aos/services/shorts", ROOT / "aos/services/video_processing_service.py", ROOT / "aos/api/v1/shorts"]
        offenders = []
        for root in roots:
            files = [root] if root.is_file() else root.rglob("*.py")
            for file in files:
                tree = ast.parse(file.read_text(), filename=str(file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "commit":
                        offenders.append(f"{file.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_view_identity_is_scoped_to_short(self):
        view_source = (ROOT / "aos/aos/doctype/aos_short_view/aos_short_view.py").read_text()
        analytics_source = (ROOT / "aos/services/shorts/analytics.py").read_text()
        self.assertIn("short_view_identity_key(", view_source)
        self.assertIn("self.short", view_source)
        self.assertIn('f"{short}|{actor_key}"', analytics_source)
        self.assertIn("hashlib.sha256", analytics_source)


    def test_comment_replies_have_client_read_contract(self):
        self.assertIn("list_comment_replies", ENDPOINT_SPECS)
        self.assertEqual(ENDPOINT_SPECS["list_comment_replies"].fields, frozenset({"comment_id", "limit", "cursor"}))

    def test_event_dedupe_and_hot_dirty_cleanup_are_atomic(self):
        service = (ROOT / "aos/services/shorts/service.py").read_text()
        hot = (ROOT / "aos/services/shorts/hot_metrics.py").read_text()
        self.assertIn("_claim_event_dedupe(cache,dedupe_key)", service)
        self.assertIn("__dirty_version", hot)
        self.assertIn("frappe.db.after_commit.add", hot)
        self.assertIn("cache.eval", hot)
        self.assertIn("_redis_key(cache, _DIRTY_KEY)", hot)
        self.assertIn("SREM", hot)
        self.assertIn("qualifies_view(watch,duration_seconds=short.get('duration_seconds'))", service)
        self.assertIn("_record_hot_signal_best_effort", service)

    def test_internal_api_logging_keeps_traceback(self):
        source = (ROOT / "aos/services/shorts/api.py").read_text()
        self.assertIn("frappe.get_traceback()", source)
        self.assertIn('f"Shorts {operation_name} failed"', source)

    def test_sound_cursor_parameters_are_implemented(self):
        service = (ROOT / "aos/services/shorts/service.py").read_text()
        search_body = service.split("def search_sounds(**kwargs):", 1)[1].split("def get_sound", 1)[0]
        favorites_body = service.split("def my_favorite_sounds(**kwargs):", 1)[1].split("def sound_shorts", 1)[0]
        self.assertIn("decode_cursor", search_body)
        self.assertIn("next_cursor", search_body)
        self.assertIn("decode_cursor", favorites_body)
        self.assertIn("next_cursor", favorites_body)

    def test_reply_pagination_index_is_declared(self):
        source = (ROOT / "aos/patches/v1_0/install_shorts_indexes.py").read_text()
        self.assertIn("idx_short_comment_replies", source)
        self.assertIn('("root_comment", "status", "creation", "name")', source)


    def test_content_modes_are_content_derived_not_location_or_ad_gates(self):
        create = ENDPOINT_SPECS["create_short"].fields
        update = ENDPOINT_SPECS["update_short"].fields
        self.assertNotIn("place_id", create)
        self.assertNotIn("place_id", update)
        schema = json.loads((ROOT / "aos/aos/doctype/aos_short/aos_short.json").read_text())
        fields = {f["fieldname"] for f in schema["fields"]}
        self.assertNotIn("place", fields)
        classifier = (ROOT / "aos/services/shorts/classification.py").read_text()
        self.assertIn("scores.get('geo',0)>=0.50", classifier)
        self.assertIn("scores.get('shop',0)>=0.50", classifier)
        self.assertNotIn("AOS Location", classifier)
        serializer = (ROOT / "aos/services/shorts/serializers.py").read_text()
        self.assertNotIn("location_id", serializer)
        service = (ROOT / "aos/services/shorts/service.py").read_text()
        self.assertNotIn("s.place", service)
        self.assertNotIn("AOS Location", service)

    def test_short_daily_metrics_have_no_legacy_marketplace_enrichment(self):
        source = (ROOT / "aos/aos/doctype/aos_short_metrics_daily/aos_short_metrics_daily.py").read_text()
        schema = json.loads((ROOT / "aos/aos/doctype/aos_short_metrics_daily/aos_short_metrics_daily.json").read_text())
        fields = {f["fieldname"] for f in schema["fields"]}
        for legacy in ("ad", "seller", "country", "location", "place"):
            self.assertNotIn(legacy, fields)
        self.assertNotIn("_enrich_from_short", source)
        self.assertNotIn('["ad", "seller", "country"]', source)
        self.assertNotIn("AOS Location", source)

    def test_retry_recovery_handles_orphaned_links_without_document_save(self):
        source = (ROOT / "aos/services/video_processing_service.py").read_text()
        body = source.split("def recover_video_processing_jobs", 1)[1]
        self.assertIn("SOURCE_DELETED", body)
        self.assertIn("SOURCE_UNAVAILABLE", body)
        self.assertIn("UPDATE `tabAOS Video Processing Job`", body)
        self.assertIn("raw_exists", body)
        self.assertIn("source_exists", body)

    def test_video_companion_callback_allowlist_always_includes_site_host(self):
        config = (ROOT / "infra/video-processing/app/config.py").read_text()
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn('urlparse(_clean(os.getenv("VIDEO_CALLBACK_URL"))).hostname', config)
        self.assertIn('(*configured, *_csv("FRAPPE_SITE_NAME"), *_csv("VIDEO_CALLBACK_ALLOWED_HOSTS"))', config)
        self.assertIn('field(default_factory=_callback_allowed_hosts)', config)
        self.assertGreaterEqual(compose.count("VIDEO_CALLBACK_URL: ${VIDEO_CALLBACK_URL:?VIDEO_CALLBACK_URL is required}"), 3)

    def test_cross_feature_consumers_do_not_reintroduce_short_location_or_seller_fields(self):
        ranking = (ROOT / "aos/services/search_ranking_service.py").read_text()
        purge = (ROOT / "aos/services/account_purge_service.py").read_text()
        self.assertNotIn("short.place", ranking)
        self.assertNotIn('"place": str(short.place', ranking)
        self.assertNotIn("AOS Location", ranking.split("def build_short_index_document", 1)[1].split("def enqueue_ad_search_delete", 1)[0])
        short_purge = purge.split('if _doctype_exists("AOS Short"):', 1)[1].split('if _doctype_exists("AOS Live Stream"):', 1)[0]
        self.assertNotIn("seller IN", short_purge)
        self.assertIn("WHERE owner = %s", short_purge)

    def test_missing_video_callback_converges_via_durable_outbox(self):
        service = (ROOT / "aos/services/video_processing_service.py").read_text()
        worker = (ROOT / "infra/video-processing/app/worker.py").read_text()
        self.assertIn("def _handle_missing_video_processing_job_callback", service)
        self.assertIn("validate_callback_idempotency", service)
        self.assertIn('callback_status="cancelled"', service)
        self.assertIn('"processing_error": "PROCESSING_JOB_MISSING"', service)
        self.assertIn('"operation": payload.get("operation")', worker)
        self.assertIn('"operation": operation', worker)

    def test_video_callback_allowlist_has_one_additive_resolver(self):
        config = (ROOT / "infra/video-processing/app/config.py").read_text()
        self.assertEqual(config.count("def _callback_allowed_hosts()"), 1)
        self.assertEqual(config.count("from urllib.parse import urlparse"), 1)


    def test_only_four_content_modes_exist(self):
        source = (ROOT / "aos/services/shorts/constants.py").read_text()
        match = re.search(r"CONTENT_MODES\s*=\s*([^\n]+)", source)
        self.assertIsNotNone(match)
        for value in ("shop", "geo", "vibes", "learn"):
            self.assertIn(value, match.group(1))
        for value in ("community", "talent", "friends", "trending", "nearby"):
            self.assertNotIn(value, match.group(1))
