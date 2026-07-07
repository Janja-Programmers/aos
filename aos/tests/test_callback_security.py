from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.tests.utils import FrappeTestCase

from aos.api.shared import callback_security
from aos.api.shared.callback_security import CallbackSecurityError, read_signed_json_callback_payload
from aos.api.video_processing import callback as video_callback
from aos.services import (
    analytics_pipeline_service,
    moderation_service,
    notification_delivery_service,
    search_ranking_service,
    video_processing_service,
)


class _FakeRequest:
    def __init__(self, body: bytes):
        self._body = body

    def get_data(self):
        return self._body


class TestCallbackSecurity(FrappeTestCase):
    secret = "test-callback-secret"
    timestamp = "1893456000"

    def _body(self, payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")

    def _signature(self, body: bytes, *, timestamp: str | None = None) -> str:
        ts = timestamp or self.timestamp
        signed_payload = callback_security.build_timestamped_signature_payload(body, ts)
        return video_processing_service.build_signature(self.secret, signed_payload)

    def _request_patches(self, body: bytes, headers: dict[str, str | None]):
        def get_header(name: str):
            return headers.get(name)

        return (
            patch.object(callback_security.frappe, "request", _FakeRequest(body), create=True),
            patch.object(callback_security.frappe, "get_request_header", side_effect=get_header, create=True),
            patch.object(callback_security.time, "time", return_value=int(self.timestamp)),
        )

    def _read_payload(self, body: bytes, headers: dict[str, str | None]):
        request_patch, header_patch, time_patch = self._request_patches(body, headers)
        with request_patch, header_patch, time_patch:
            return read_signed_json_callback_payload(
                callback_name="test callback",
                callback_secret=self.secret,
                signature_header="X-Test-Signature",
                verify_signature=video_processing_service.verify_signature,
                max_age_seconds=300,
            )

    def test_valid_timestamped_signature_succeeds(self):
        body = self._body({"job_id": "JOB-001", "status": "ready"})
        payload = self._read_payload(
            body,
            {
                "X-AOS-Callback-Timestamp": self.timestamp,
                "X-Test-Signature": self._signature(body),
            },
        )
        self.assertEqual(payload["job_id"], "JOB-001")
        self.assertEqual(payload["status"], "ready")

    def test_missing_signature_fails(self):
        body = self._body({"job_id": "JOB-001", "status": "ready"})
        with self.assertRaises(CallbackSecurityError) as ctx:
            self._read_payload(body, {"X-AOS-Callback-Timestamp": self.timestamp})
        self.assertEqual(ctx.exception.code, "UNAUTHORIZED")

    def test_invalid_signature_fails(self):
        body = self._body({"job_id": "JOB-001", "status": "ready"})
        with self.assertRaises(CallbackSecurityError):
            self._read_payload(
                body,
                {
                    "X-AOS-Callback-Timestamp": self.timestamp,
                    "X-Test-Signature": "sha256=bad",
                },
            )

    def test_missing_secret_fails_closed(self):
        body = self._body({"job_id": "JOB-001", "status": "ready"})
        request_patch, header_patch, time_patch = self._request_patches(
            body,
            {
                "X-AOS-Callback-Timestamp": self.timestamp,
                "X-Test-Signature": self._signature(body),
            },
        )
        with request_patch, header_patch, time_patch:
            with patch.object(callback_security.frappe, "log_error") as log_error:
                with self.assertRaises(CallbackSecurityError) as ctx:
                    read_signed_json_callback_payload(
                        callback_name="test callback",
                        callback_secret="",
                        signature_header="X-Test-Signature",
                        verify_signature=video_processing_service.verify_signature,
                        max_age_seconds=300,
                    )
        self.assertEqual(ctx.exception.code, "CALLBACK_AUTH_NOT_CONFIGURED")
        log_error.assert_called_once_with(
            "Missing callback secret for test callback.",
            "AOS callback auth misconfigured",
        )

    def test_empty_body_fails(self):
        with self.assertRaises(CallbackSecurityError) as ctx:
            self._read_payload(
                b"",
                {
                    "X-AOS-Callback-Timestamp": self.timestamp,
                    "X-Test-Signature": "sha256=unused",
                },
            )
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_expired_timestamp_fails(self):
        body = self._body({"job_id": "JOB-001", "status": "ready"})
        expired = str(int(self.timestamp) - 301)
        with self.assertRaises(CallbackSecurityError):
            self._read_payload(
                body,
                {
                    "X-AOS-Callback-Timestamp": expired,
                    "X-Test-Signature": self._signature(body, timestamp=expired),
                },
            )

    def test_signed_kwargs_fallback_is_not_allowed(self):
        handler = Mock()
        request_patch, header_patch, time_patch = self._request_patches(b"", {})
        with request_patch, header_patch, time_patch:
            with patch.object(video_callback, "get_video_processing_config", return_value=SimpleNamespace(callback_secret=self.secret)):
                with patch.object(video_callback, "handle_video_processing_callback", handler):
                    response = video_callback.handle_callback_impl(job_id="JOB-001", status="ready")

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "VALIDATION_ERROR")
        handler.assert_not_called()

    def test_service_signature_verifiers_fail_closed_without_secret(self):
        for service in (
            video_processing_service,
            moderation_service,
            search_ranking_service,
            analytics_pipeline_service,
            notification_delivery_service,
        ):
            with self.subTest(service=service.__name__):
                self.assertFalse(service.verify_signature("", b"{}", "sha256=anything"))
                self.assertFalse(service.verify_signature(None, b"{}", "sha256=anything"))

    def test_video_ready_callback_is_idempotent(self):
        job = SimpleNamespace(name="JOB-001", status="Ready")
        with patch.object(video_processing_service.frappe.db, "exists", return_value=True):
            with patch.object(video_processing_service.frappe, "get_doc", return_value=job):
                with patch.object(video_processing_service, "mark_video_job_ready") as mark_ready:
                    result = video_processing_service.handle_video_processing_callback(
                        {"job_id": job.name, "status": "ready"}
                    )
        self.assertIs(result, job)
        mark_ready.assert_not_called()

    def test_video_terminal_status_blocks_conflicting_callback(self):
        job = SimpleNamespace(name="JOB-001", status="Ready")
        with patch.object(video_processing_service.frappe.db, "exists", return_value=True):
            with patch.object(video_processing_service.frappe, "get_doc", return_value=job):
                with self.assertRaises(video_processing_service.VideoProcessingError):
                    video_processing_service.handle_video_processing_callback(
                        {"job_id": job.name, "status": "failed", "error": "late failure"}
                    )
