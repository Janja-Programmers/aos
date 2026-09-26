from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aos.services import moderation_service


class TestModerationPayloadNormalization(unittest.TestCase):
    def _short(self, *, caption: str = "", revision: int = 1, generation: int = 1):
        return SimpleNamespace(
            name="SHORT-TEST",
            owner="test@example.com",
            lifecycle_status="Draft",
            content_type="Video",
            caption=caption,
            raw_video_media=None,
            poster_media=None,
            storyboard_media=None,
            cover_media=None,
            revision=revision,
            moderation_generation=generation,
        )

    def _get_all(self, hashtags):
        def fake_get_all(doctype, **_kwargs):
            if doctype == "AOS Short Hashtag":
                return [SimpleNamespace(hashtag=value) for value in hashtags]
            if doctype == "AOS Short Mode":
                return []
            raise AssertionError(f"unexpected get_all: {doctype}")
        return fake_get_all

    def test_short_with_no_caption_or_hashtags_emits_no_text_items(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(name="MOD-JOB")

        with (
            patch.object(moderation_service.frappe, "get_doc", return_value=self._short()),
            patch.object(moderation_service.frappe, "get_all", side_effect=self._get_all([])),
            patch.object(moderation_service, "create_moderation_job", side_effect=fake_create),
        ):
            moderation_service.enqueue_short_moderation("SHORT-TEST")

        self.assertEqual(captured["text_items"], [])

    def test_short_hashtags_are_flattened_as_meaningful_text_not_json(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(name="MOD-JOB")

        with (
            patch.object(moderation_service.frappe, "get_doc", return_value=self._short()),
            patch.object(
                moderation_service.frappe,
                "get_all",
                side_effect=self._get_all(["travel", "beach"]),
            ),
            patch.object(moderation_service, "create_moderation_job", side_effect=fake_create),
        ):
            moderation_service.enqueue_short_moderation("SHORT-TEST")

        self.assertEqual(
            captured["text_items"],
            [{"field": "hashtags", "text": "travel beach", "content_type": "text/plain"}],
        )
        self.assertNotIn("[]", str(captured["text_items"]))
