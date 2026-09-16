from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.media.tests.test_service import FakeStorage, PNG_64
from aos.integrations.ai.background_removal_client import (
    BackgroundRemovalProcessingError,
    BackgroundRemovalUnavailableError,
)
from aos.services.media.background_processing import MediaProcessingService
from aos.services.media.media_service import MediaPermissionError, MediaService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestMediaBackgroundProcessing(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("media-processing")
        self.created_users: list[str] = []
        self.user = self.make_user("owner", with_preference=False)
        self.other_user = self.make_user("other", with_preference=False)
        self.storage = FakeStorage()
        self.media = MediaService(storage=self.storage)
        self.processing = MediaProcessingService(media_service=self.media)
        frappe.set_user("Administrator")

        self.source = self.media.create_uploaded_from_bytes(
            user=self.user,
            purpose="profile_image",
            filename="avatar.png",
            content_type="image/png",
            data=PNG_64,
            width=64,
            height=64,
        )
        frappe.db.commit()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _request(self):
        with patch.object(self.processing, "enqueue") as enqueue:
            job = self.processing.request_background_removal(
                user=self.user,
                source_media_id=self.source.name,
                result_purpose="profile_image",
            )
        enqueue.assert_called_once_with(job.name, after_commit=True)
        return job

    @staticmethod
    def _client_settings():
        return SimpleNamespace(max_image_bytes=10 * 1024 * 1024)

    def test_duplicate_request_reuses_one_durable_job(self):
        first = self._request()
        with patch.object(self.processing, "enqueue") as enqueue:
            second = self.processing.request_background_removal(
                user=self.user,
                source_media_id=self.source.name,
                result_purpose="profile_image",
            )
        enqueue.assert_not_called()
        self.assertEqual(first.name, second.name)
        self.assertEqual(
            frappe.db.count(
                "AOS Media Processing Job",
                {"owner_user": self.user, "source_media": self.source.name},
            ),
            1,
        )

    def test_explicit_request_requeues_terminal_failed_job(self):
        job = self._request()
        frappe.db.set_value(
            "AOS Media Processing Job",
            job.name,
            {
                "status": "Failed",
                "attempt_count": 3,
                "completed_at": frappe.utils.now_datetime(),
                "last_error_code": "MEDIA_PROCESSING_ERROR",
                "last_error_message": "Media processing failed safely.",
            },
            update_modified=False,
        )

        with patch.object(self.processing, "enqueue") as enqueue:
            retried = self.processing.request_background_removal(
                user=self.user,
                source_media_id=self.source.name,
                result_purpose="profile_image",
            )

        self.assertEqual(retried.name, job.name)
        self.assertEqual(retried.status, "Queued")
        self.assertEqual(int(retried.attempt_count or 0), 0)
        self.assertFalse(retried.completed_at)
        self.assertFalse(retried.last_error_code)
        enqueue.assert_called_once_with(job.name, after_commit=True)

    def test_processing_job_is_owner_scoped(self):
        job = self._request()
        with self.assertRaises(MediaPermissionError) as exc:
            self.processing.get_job(user=self.other_user, job_id=job.name)
        self.assertEqual(exc.exception.code, "MEDIA_ACCESS_DENIED")

    def test_success_is_idempotent_across_duplicate_worker_execution(self):
        job = self._request()
        with (
            patch(
                "aos.services.media.background_processing.get_background_removal_client_settings",
                return_value=self._client_settings(),
            ),
            patch(
                "aos.services.media.background_processing.remove_background_from_file",
                return_value=SimpleNamespace(content=PNG_64),
            ) as remove,
        ):
            self.assertEqual(self.processing.process(job_id=job.name), "Succeeded")
            self.assertEqual(self.processing.process(job_id=job.name), "Succeeded")

        remove.assert_called_once()
        final_job = frappe.get_doc("AOS Media Processing Job", job.name)
        self.assertEqual(final_job.status, "Succeeded")
        self.assertTrue(final_job.result_media)
        self.assertEqual(
            frappe.db.count("AOS Media Object", {"processing_job": job.name}),
            1,
        )
        result = frappe.get_doc("AOS Media Object", final_job.result_media)
        self.assertEqual(result.owner_user, self.user)
        self.assertEqual(result.derived_from_media, self.source.name)
        self.assertEqual(result.purpose, "profile_image")
        self.assertTrue(self.storage.object_exists(result.bucket, result.object_key))

    def test_success_does_not_create_redundant_notification(self):
        job = self._request()
        with (
            patch(
                "aos.services.media.background_processing.get_background_removal_client_settings",
                return_value=self._client_settings(),
            ),
            patch(
                "aos.services.media.background_processing.remove_background_from_file",
                return_value=SimpleNamespace(content=PNG_64),
            ),
            patch(
                "aos.services.notifications.service.NotificationService.notify_media_processing_failed"
            ) as notify_failure,
        ):
            self.assertEqual(self.processing.process(job_id=job.name), "Succeeded")
            self.assertEqual(self.processing.process(job_id=job.name), "Succeeded")

        notify_failure.assert_not_called()
        final_job = frappe.get_doc("AOS Media Processing Job", job.name)
        self.assertEqual(final_job.status, "Succeeded")
        self.assertTrue(final_job.result_media)
        self.assertEqual(
            frappe.db.count("AOS Media Object", {"processing_job": job.name}),
            1,
        )

    def test_failure_state_survives_notification_failure(self):
        job = self._request()
        with (
            patch(
                "aos.services.media.background_processing.get_background_removal_client_settings",
                return_value=self._client_settings(),
            ),
            patch(
                "aos.services.media.background_processing.remove_background_from_file",
                side_effect=BackgroundRemovalProcessingError("rejected"),
            ),
            patch(
                "aos.services.notifications.service.NotificationService.notify_media_processing_failed",
                side_effect=RuntimeError("notification unavailable"),
            ) as notify,
        ):
            self.assertEqual(self.processing.process(job_id=job.name), "Failed")

        notify.assert_called_once()
        final_job = frappe.get_doc("AOS Media Processing Job", job.name)
        self.assertEqual(final_job.status, "Failed")
        self.assertEqual(final_job.last_error_code, "BACKGROUND_REMOVAL_FAILED")

    def test_processor_rejection_fails_job_without_corrupting_original(self):
        job = self._request()
        source_identity = (self.source.bucket, self.source.object_key)
        with (
            patch(
                "aos.services.media.background_processing.get_background_removal_client_settings",
                return_value=self._client_settings(),
            ),
            patch(
                "aos.services.media.background_processing.remove_background_from_file",
                side_effect=BackgroundRemovalProcessingError("rejected"),
            ),
        ):
            self.assertEqual(self.processing.process(job_id=job.name), "Failed")

        final_job = frappe.get_doc("AOS Media Processing Job", job.name)
        self.assertEqual(final_job.status, "Failed")
        self.assertFalse(final_job.result_media)
        self.assertEqual(final_job.last_error_code, "BACKGROUND_REMOVAL_FAILED")
        self.assertTrue(self.storage.object_exists(*source_identity))
        self.assertEqual(frappe.get_doc("AOS Media Object", self.source.name).status, "Uploaded")

    def test_temporary_processor_outage_schedules_bounded_retry(self):
        job = self._request()
        with (
            patch(
                "aos.services.media.background_processing.get_background_removal_client_settings",
                return_value=self._client_settings(),
            ),
            patch(
                "aos.services.media.background_processing.remove_background_from_file",
                side_effect=BackgroundRemovalUnavailableError("offline"),
            ),
        ):
            self.assertEqual(self.processing.process(job_id=job.name), "Retry Waiting")

        final_job = frappe.get_doc("AOS Media Processing Job", job.name)
        self.assertEqual(final_job.status, "Retry Waiting")
        self.assertEqual(final_job.attempt_count, 1)
        self.assertEqual(final_job.last_error_code, "BACKGROUND_REMOVAL_UNAVAILABLE")
        self.assertTrue(final_job.next_attempt_at)
        self.assertFalse(final_job.result_media)
