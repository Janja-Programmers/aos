from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shared.public_errors import (
    is_sensitive_exception_message,
    safe_exception_message,
    safe_fail_from_exception,
)


class TestPublicErrorSafety(FrappeTestCase):
    """Focused checks that public APIs do not expose raw internals."""

    def setUp(self):
        frappe.local.response = {}

    def test_safe_exception_message_keeps_short_public_validation_messages(self):
        self.assertEqual(
            safe_exception_message(frappe.ValidationError("You cannot follow yourself."), "Invalid request."),
            "You cannot follow yourself.",
        )

    def test_safe_exception_message_redacts_sql_paths_urls_and_secrets(self):
        unsafe_messages = (
            "OperationalError: SELECT password FROM `tabUser` WHERE name='x'",
            "Traceback File /home/aos/frappe-bench/apps/aos/aos/api/file.py line 10",
            "Upstream failed at http://internal-service:8000/debug?token=secret",
            "AWS4-HMAC-SHA256 Credential=minioadmin/access_key/secret",
        )

        for message in unsafe_messages:
            with self.subTest(message=message):
                self.assertTrue(is_sensitive_exception_message(message))
                self.assertEqual(
                    safe_exception_message(Exception(message), "Invalid request."),
                    "Invalid request.",
                )

    def test_safe_fail_from_exception_logs_internals_but_returns_fallback(self):
        exc = RuntimeError("SELECT secret FROM `tabUser` WHERE name='Administrator'")

        with patch("aos.api.shared.public_errors.frappe.log_error") as log_error:
            response = safe_fail_from_exception(
                exc,
                fallback="Invalid request.",
                error="VALIDATION_ERROR",
                log_title="AOS Public Error Safety Test",
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["message"], "Invalid request.")
        self.assertEqual(response["error"], "VALIDATION_ERROR")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)
        self.assertTrue(log_error.called)

    def test_public_api_fail_calls_do_not_use_raw_exception_strings(self):
        offenders = []
        app_root = Path(__file__).resolve().parents[1]

        for path in (app_root / "api").rglob("*.py"):
            if path.name == "responses.py":
                continue
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except Exception as exc:  # pragma: no cover - compileall covers this too.
                offenders.append(f"{path}: parse failed: {exc}")
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name != "fail":
                    continue

                source = ast.get_source_segment(text, node) or ""
                if "str(" in source or ".message" in source or "get_traceback" in source:
                    offenders.append(f"{path}:{node.lineno}: {source}")

        self.assertFalse(offenders, "Raw exception details returned by fail():\n" + "\n".join(offenders))
