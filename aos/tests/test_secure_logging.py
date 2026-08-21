from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from aos.utils.secure_logging import (
    REDACTED,
    install_frappe_traceback_redaction,
    redact_error_log_document,
    sanitize_sentry_event,
    redact_sensitive_data,
    redact_sensitive_text,
)


class TestSecureLogging(TestCase):
    def test_text_redaction_covers_contextual_traceback_object_reprs(self):
        raw = (
            "self = MariaDBDatabase(host='127.0.0.1', password='db-secret-123', object_key='keep/me')\n"
            "config = VideoProcessingConfig(service_secret='service-secret-456', callback_secret='callback-secret-789')\n"
            "context = ActiveOutboxDispatchContext(dispatch_token='dispatch-secret-123', active_dispatch_token='active-secret-456')\n"
            "Authorization: Bearer bearer-secret-0123456789\n"
            + "redis://worker:"
            + "redis-password"
            + "@127.0.0.1:6379/0\n"
        )
        sanitized = redact_sensitive_text(raw)

        for secret in (
            "db-secret-123",
            "service-secret-456",
            "callback-secret-789",
            "dispatch-secret-123",
            "active-secret-456",
            "bearer-secret-0123456789",
            "redis-password",
        ):
            self.assertNotIn(secret, sanitized)
        self.assertIn("object_key='keep/me'", sanitized)
        self.assertIn(REDACTED, sanitized)

    def test_structured_redaction_is_recursive_and_does_not_mask_object_key(self):
        source = {
            "password": "db-secret",  # pragma: allowlist secret
            "nested": {
                "callback_secret": "callback-secret",  # pragma: allowlist secret
                "dispatch_token": "dispatch-token",
                "object_key": "shorts/raw/file.mp4",
            },
        }
        sanitized = redact_sensitive_data(source)
        self.assertEqual(sanitized["password"], REDACTED)
        self.assertEqual(sanitized["nested"]["callback_secret"], REDACTED)
        self.assertEqual(sanitized["nested"]["dispatch_token"], REDACTED)
        self.assertEqual(sanitized["nested"]["object_key"], "shorts/raw/file.mp4")

    def test_sentry_event_redaction_covers_request_and_frame_locals(self):
        event = {
            "request": {
                "headers": {"Authorization": "Bearer sentry-auth-secret"},
                "data": {"password": "request-password", "object_key": "keep/me"},  # pragma: allowlist secret
            },
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {
                                    "vars": {
                                        "self": "DB(password='frame-password')",  # pragma: allowlist secret
                                        "config": "Config(service_secret='frame-service-secret')",  # pragma: allowlist secret
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        }
        sanitized = sanitize_sentry_event(event)
        serialized = repr(sanitized)
        for secret in (
            "sentry-auth-secret",
            "request-password",
            "frame-password",
            "frame-service-secret",
        ):
            self.assertNotIn(secret, serialized)
        self.assertIn("keep/me", serialized)

    def test_error_log_hook_redacts_error_metadata_and_title(self):
        doc = SimpleNamespace(
            error="password='db-secret' callback_secret='callback-secret'",
            metadata='{"authorization":"Bearer auth-secret-12345678","path":"/jobs"}',
            method="token=method-secret",
        )
        redact_error_log_document(doc)
        serialized = f"{doc.error}\n{doc.metadata}\n{doc.method}"
        self.assertNotIn("db-secret", serialized)
        self.assertNotIn("callback-secret", serialized)
        self.assertNotIn("auth-secret-12345678", serialized)
        self.assertNotIn("method-secret", serialized)

    def test_traceback_wrapper_redacts_before_error_log_or_telemetry_consumes_it(self):
        fake_frappe = ModuleType("frappe")
        fake_utils = ModuleType("frappe.utils")

        def unsafe_traceback(*_args, **_kwargs):
            return "self = DB(password='trace-secret') config = C(service_secret='svc-secret')"

        fake_frappe.get_traceback = unsafe_traceback
        fake_frappe.utils = fake_utils
        fake_utils.get_traceback = unsafe_traceback

        with patch.dict(sys.modules, {"frappe": fake_frappe, "frappe.utils": fake_utils}):
            self.assertTrue(install_frappe_traceback_redaction())
            result = fake_frappe.get_traceback(with_context=True)
            self.assertNotIn("trace-secret", result)
            self.assertNotIn("svc-secret", result)
            self.assertIn(REDACTED, result)
            self.assertTrue(install_frappe_traceback_redaction())
