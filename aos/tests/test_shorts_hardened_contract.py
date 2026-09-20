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
        source = (ROOT / "aos/aos/doctype/aos_short_view/aos_short_view.py").read_text()
        self.assertIn('f"{self.short}|{actor_key}"', source)
        self.assertIn("sha256", source)

    def test_only_four_content_modes_exist(self):
        source = (ROOT / "aos/services/shorts/constants.py").read_text()
        match = re.search(r"CONTENT_MODES\s*=\s*([^\n]+)", source)
        self.assertIsNotNone(match)
        for value in ("shop", "geo", "vibes", "learn"):
            self.assertIn(value, match.group(1))
        for value in ("community", "talent", "friends", "trending", "nearby"):
            self.assertNotIn(value, match.group(1))
