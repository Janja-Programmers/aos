from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from aos.api.shorts.sounds import validate_existing_short_sound_for_mode
from aos.services.shorts.original_sounds import ensure_original_sound_for_short


class TestShopOriginalSoundCompatibility(FrappeTestCase):
    def test_source_original_audio_does_not_require_commercial_catalog_flag(self):
        with patch(
            "aos.api.shorts.sounds.frappe.db.sql",
            return_value=[
                {
                    "sound": "SOUND-2026-00001",
                    "is_original_audio": 1,
                    "status": "active",
                    "is_commercial_safe": 0,
                }
            ],
        ):
            response = validate_existing_short_sound_for_mode(
                short_id="SHORT-2026-00001",
                content_mode="shop",
            )

        self.assertIsNone(response)


class TestOriginalSoundLifecycle(FrappeTestCase):
    def test_existing_original_link_is_idempotent(self):
        short = SimpleNamespace(
            name="SHORT-2026-00001",
            owner="creator@example.test",
            raw_video_media="MEDIA-00000000000000000000000000000001",
        )
        with (
            patch(
                "aos.services.shorts.original_sounds._existing_short_sound",
                return_value={
                    "name": "SH-SND-2026-00001",
                    "sound": "SOUND-2026-00001",
                    "is_original_audio": 1,
                },
            ),
            patch("aos.services.shorts.original_sounds.frappe.get_doc") as get_doc,
        ):
            sound_id = ensure_original_sound_for_short(
                short=short,
                audio={"bucket": "public", "object_key": "sounds/uploads/original/x.m4a"},
            )

        self.assertEqual(sound_id, "SOUND-2026-00001")
        get_doc.assert_not_called()

    def test_selected_sound_is_never_replaced_by_callback(self):
        short = SimpleNamespace(
            name="SHORT-2026-00001",
            owner="creator@example.test",
            raw_video_media="MEDIA-00000000000000000000000000000001",
        )
        with (
            patch(
                "aos.services.shorts.original_sounds._existing_short_sound",
                return_value={
                    "name": "SH-SND-2026-00002",
                    "sound": "SOUND-2026-00099",
                    "is_original_audio": 0,
                },
            ),
            patch("aos.services.shorts.original_sounds.frappe.get_doc") as get_doc,
        ):
            sound_id = ensure_original_sound_for_short(
                short=short,
                audio={"bucket": "public", "object_key": "sounds/uploads/original/x.m4a"},
            )

        self.assertIsNone(sound_id)
        get_doc.assert_not_called()

    def test_processing_audio_creates_reusable_original_sound_and_link(self):
        short = SimpleNamespace(
            name="SHORT-2026-00001",
            owner="creator@example.test",
            raw_video_media="MEDIA-00000000000000000000000000000001",
        )
        sound_doc = MagicMock()
        sound_doc.name = "SOUND-2026-00001"
        short_sound_doc = MagicMock()
        created_payloads: list[dict] = []

        def fake_get_doc(payload):
            created_payloads.append(payload)
            return sound_doc if payload.get("doctype") == "AOS Sound" else short_sound_doc

        with (
            patch(
                "aos.services.shorts.original_sounds._existing_short_sound",
                side_effect=[None, None],
            ),
            patch(
                "aos.services.shorts.original_sounds.get_original_sound_id",
                return_value=None,
            ),
            patch(
                "aos.services.shorts.original_sounds._get_or_create_media",
                return_value=SimpleNamespace(name="MEDIA-00000000000000000000000000000002"),
            ),
            patch(
                "aos.services.shorts.original_sounds._creator_display_name",
                return_value="Bobby",
            ),
            patch(
                "aos.services.shorts.original_sounds.frappe.get_doc",
                side_effect=fake_get_doc,
            ),
        ):
            sound_id = ensure_original_sound_for_short(
                short=short,
                audio={
                    "bucket": "aos-public",
                    "object_key": "sounds/uploads/original/SHORT-2026-00001/v1/original.m4a",
                    "content_type": "audio/mp4",
                    "size_bytes": 1234,
                    "duration_seconds": 12.5,
                },
            )

        self.assertEqual(sound_id, "SOUND-2026-00001")
        self.assertEqual(created_payloads[0]["source_type"], "original")
        self.assertEqual(created_payloads[0]["created_from_short"], short.name)
        self.assertEqual(created_payloads[0]["title"], "original sound - Bobby")
        self.assertEqual(created_payloads[0]["sound_media"], "MEDIA-00000000000000000000000000000002")
        self.assertEqual(created_payloads[1]["sound"], "SOUND-2026-00001")
        self.assertEqual(created_payloads[1]["short"], short.name)
        self.assertEqual(created_payloads[1]["is_original_audio"], 1)
        sound_doc.insert.assert_called_once_with(ignore_permissions=True)
        short_sound_doc.insert.assert_called_once_with(ignore_permissions=True)
