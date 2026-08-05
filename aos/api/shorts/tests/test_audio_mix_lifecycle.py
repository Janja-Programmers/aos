from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from aos.api.shorts import sounds


class TestShortAudioMixLifecycle(FrappeTestCase):
    def test_audio_reprocess_returns_durable_job(self):
        expected = SimpleNamespace(name="VIDEO-JOB-2026-00001", status="Queued")
        with (
            patch.object(sounds.frappe.db, "get_value", return_value="ready"),
            patch.object(sounds, "create_video_processing_job", return_value=expected) as create_job,
        ):
            actual = sounds.enqueue_short_audio_reprocess("SHORT-2026-00001")

        self.assertIs(actual, expected)
        create_job.assert_called_once_with(
            short_id="SHORT-2026-00001",
            force=True,
            reason="audio_reprocess",
            enqueue=True,
        )

    def test_audio_reprocess_failure_is_not_swallowed(self):
        with (
            patch.object(sounds.frappe.db, "get_value", return_value="ready"),
            patch.object(
                sounds,
                "create_video_processing_job",
                side_effect=RuntimeError("synthetic queue failure"),
            ),
            patch.object(sounds.frappe, "log_error"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic queue failure"):
                sounds.enqueue_short_audio_reprocess("SHORT-2026-00001")


    def test_processing_callback_and_recovery_are_wired(self):
        app_root = Path(__file__).resolve().parents[3]
        service_source = (app_root / "services" / "video_processing_service.py").read_text(
            encoding="utf-8"
        )
        tasks_source = (app_root / "tasks" / "shorts.py").read_text(encoding="utf-8")
        hooks_source = (app_root / "hooks.py").read_text(encoding="utf-8")

        self.assertIn('_set_audio_mix_state(job, "processing")', service_source)
        self.assertIn('"AUDIO_MIX_NOT_APPLIED"', service_source)
        self.assertIn("def recover_pending_audio_mixes", tasks_source)
        self.assertIn("cancelled_stale", tasks_source)
        self.assertIn("AUDIO_MIX_RECOVERY_STALE", tasks_source)
        self.assertIn("skipped_active", tasks_source)
        self.assertNotIn("AND NOT EXISTS (", tasks_source[tasks_source.index("def recover_pending_audio_mixes"):tasks_source.index("def maintain_short_integrity")])
        self.assertIn("aos.tasks.shorts.recover_pending_audio_mixes", hooks_source)

    def test_publish_response_uses_actual_audio_job_state(self):
        root = Path(__file__).resolve().parents[1]
        upload_source = (root / "upload.py").read_text(encoding="utf-8")
        sounds_source = (root / "sounds.py").read_text(encoding="utf-8")

        self.assertIn('"audio_mix_job_id": getattr(audio_job, "name", None)', upload_source)
        self.assertIn('"audio_mix_job_status": getattr(audio_job, "status", None)', upload_source)
        self.assertIn('frappe.db.get_value(\n            "AOS Short", doc.name, "audio_mix_status"', upload_source)
        self.assertNotIn('"pending" if sound_id', upload_source)
        enqueue_section = sounds_source[
            sounds_source.index("def enqueue_short_audio_reprocess"):
            sounds_source.index("def validate_existing_short_sound_for_mode")
        ]
        self.assertIn("        raise", enqueue_section)
        self.assertNotIn('values["audio_mix_status"] = "pending"', enqueue_section)

    def test_ready_short_audio_mix_can_use_the_existing_retry_endpoint(self):
        root = Path(__file__).resolve().parents[1]
        management_source = (root / "management.py").read_text(encoding="utf-8")

        retry_section = management_source[
            management_source.index("def retry_processing_impl"):
        ]
        self.assertIn('reason="audio_reprocess"', retry_section)
        self.assertIn('force=True', retry_section)
        self.assertIn('"Sound processing restarted."', retry_section)
        self.assertIn('"audio_mix_status": "pending"', retry_section)
        self.assertIn('"AOS Short Sound"', retry_section)
