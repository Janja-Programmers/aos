from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.aos.doctype.aos_sound.aos_sound import AOSSound
from aos.api.shorts.sounds import create_sound_impl


class TestSoundMediaHooks(FrappeTestCase):
    def test_sound_media_is_validated_and_public_fields_are_synchronized(self):
        sound = SimpleNamespace(
            sound_media="MEDIA-00000000000000000000000000000001",
            doctype="AOS Sound",
            name="new-aos-sound-1",
            owner="Administrator",
            duration_seconds=0,
            get_doc_before_save=lambda: None,
            is_new=lambda: True,
        )
        media = SimpleNamespace(
            name="MEDIA-00000000000000000000000000000001",
            object_key="sounds/uploads/example.mp3",
            duration_seconds=42.5,
        )

        with (
            patch("aos.aos.doctype.aos_sound.aos_sound.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_sound.aos_sound.frappe.session") as session,
        ):
            session.user = "Administrator"
            service = service_factory.return_value
            service.validate_media_for_use.return_value = media
            service.get_public_url.return_value = "https://files.example/sounds/example.mp3"
            AOSSound._sync_sound_media(sound)

        service.validate_media_for_use.assert_called_once_with(
            media_id="MEDIA-00000000000000000000000000000001",
            user="Administrator",
            purpose="sound_upload",
            attached_doctype=None,
            attached_name=None,
        )
        self.assertEqual(sound.file_key, "sounds/uploads/example.mp3")
        self.assertEqual(sound.file_url, "https://files.example/sounds/example.mp3")
        self.assertEqual(sound.duration_seconds, 42.5)

    def test_existing_sound_audio_is_immutable(self):
        previous = SimpleNamespace(sound_media="MEDIA-00000000000000000000000000000002")
        sound = SimpleNamespace(
            sound_media="MEDIA-00000000000000000000000000000003",
            doctype="AOS Sound",
            name="SOUND-2026-00001",
            owner="Administrator",
            duration_seconds=10,
            get_doc_before_save=lambda: previous,
            is_new=lambda: False,
        )

        with self.assertRaises(frappe.ValidationError):
            AOSSound._sync_sound_media(sound)

    def test_sound_update_attaches_media_through_shared_lifecycle(self):
        sound = SimpleNamespace(
            sound_media="MEDIA-00000000000000000000000000000001",
            doctype="AOS Sound",
            name="SOUND-2026-00001",
            owner="Administrator",
        )
        with (
            patch("aos.aos.doctype.aos_sound.aos_sound.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_sound.aos_sound.frappe.session") as session,
        ):
            session.user = "Administrator"
            AOSSound._finalize_sound_media_relationship(sound)

        service_factory.return_value.attach_media.assert_called_once_with(
            media_id="MEDIA-00000000000000000000000000000001",
            user="Administrator",
            purpose="sound_upload",
            attached_doctype="AOS Sound",
            attached_name="SOUND-2026-00001",
            attached_field="sound_media",
        )

    def test_sound_delete_releases_media(self):
        sound = SimpleNamespace(
            sound_media="MEDIA-00000000000000000000000000000001",
            doctype="AOS Sound",
            name="SOUND-2026-00001",
            owner="Administrator",
        )
        with (
            patch("aos.aos.doctype.aos_sound.aos_sound.MediaService") as service_factory,
            patch("aos.aos.doctype.aos_sound.aos_sound.frappe.session") as session,
        ):
            session.user = "Administrator"
            AOSSound.on_trash(sound)

        service_factory.return_value.release_media.assert_called_once_with(
            media_id="MEDIA-00000000000000000000000000000001",
            user="Administrator",
            attached_doctype="AOS Sound",
            attached_name="SOUND-2026-00001",
            replacement_media_id=None,
        )

    def test_desk_uploader_uses_direct_media_pipeline(self):
        app_root = Path(frappe.get_app_path("aos"))
        source = (app_root / "aos" / "doctype" / "aos_sound" / "aos_sound.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('purpose: "sound_upload"', source)
        self.assertIn('initUpload: "aos.api.v1.media.init_upload"', source)
        self.assertIn('confirmUpload: "aos.api.v1.media.confirm_upload"', source)
        self.assertIn('xhr.open("PUT", uploadUrl, true)', source)
        self.assertIn("Audio cannot be replaced", (app_root / "aos" / "doctype" / "aos_sound" / "aos_sound.py").read_text(encoding="utf-8"))

    def test_public_create_sound_cannot_spoof_original_source_type(self):
        with (
            patch("aos.api.shorts.sounds.require_login", return_value=("user@example.test", None)),
            patch("aos.api.shorts.sounds.rate_limit", return_value=None),
            patch("aos.api.shorts.sounds._is_staff", return_value=False),
        ):
            response = create_sound_impl(
                sound_media="MEDIA-00000000000000000000000000000001",
                title="Spoofed original",
                artist="Creator",
                source_type="original",
                duration_seconds=10,
            )

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")

    def test_create_sound_api_relies_on_controller_attachment(self):
        app_root = Path(frappe.get_app_path("aos"))
        source = (app_root / "api" / "shorts" / "sounds.py").read_text(encoding="utf-8")
        section = source[source.index("def create_sound_impl"):source.index("# SOUND BROWSING")]
        self.assertNotIn("media_service.attach_media(", section)
        self.assertIn('"doctype": "AOS Sound"', section)
