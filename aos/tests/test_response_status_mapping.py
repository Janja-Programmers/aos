from __future__ import annotations

import ast
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.shared.responses import DEFAULT_HTTP_STATUS_MAP, fail, http_status_for_code, ok


class TestResponseStatusMapping(FrappeTestCase):
    """Tests for stable API error-code to HTTP-status semantics."""

    def setUp(self):
        frappe.local.response = {}

    def test_ok_sets_success_status(self):
        response = ok("Done.")
        self.assertTrue(response["ok"])
        self.assertEqual(frappe.local.response.get("http_status_code"), 200)

    def test_validation_codes_use_unprocessable_entity(self):
        for code in ("VALIDATION_ERROR", "EMAIL_MISSING", "INVALID_AD", "PASSWORD_MISMATCH"):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 422)

    def test_catalog_codes_have_stable_statuses(self):
        for code in (
            "INVALID_CATALOG_INPUT",
            "INVALID_CATEGORY",
            "INVALID_CATEGORY_SCHEMA",
            "INVALID_CATEGORY_TREE",
            "CATEGORY_NOT_SELLABLE",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 422)
        self.assertEqual(http_status_for_code("CATEGORY_NOT_FOUND"), 404)
        self.assertEqual(http_status_for_code("CATALOG_DATA_ERROR"), 500)

    def test_auth_codes_use_unauthorized(self):
        for code in (
            "AUTH_REQUIRED",
            "INVALID_CREDENTIALS",
            "LOGIN_FAILED",
            "SESSION_INVALID",
            "TOKEN_INVALID",
            "TOKEN_VERIFY_FAILED",
            "UNAUTHORIZED",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 401)

    def test_permission_codes_use_forbidden(self):
        for code in (
            "ACCOUNT_DISABLED",
            "ACCOUNT_SUSPENDED",
            "EMAIL_NOT_VERIFIED",
            "FORBIDDEN",
            "NOT_VERIFIED",
            "PERMISSION_DENIED",
            "PREFERENCE_MISSING",
            "SELLER_REQUIRED",
            "USER_BLOCKED",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 403)

    def test_conflict_codes_use_conflict(self):
        for code in (
            "ACTIVE_CALL_EXISTS",
            "ALREADY_EXISTS",
            "ALREADY_REVIEWED",
            "CONFLICT",
            "COMPANION_ACTIVE_GENERATION_AHEAD",
            "COMPANION_CALLBACK_COMPLETE_EVIDENCE_INSUFFICIENT",
            "COMPANION_CALLBACK_COMPLETE_FRAPPE_STATE_MISMATCH",
            "INVALID_STATE",
            "MARKET_LOCKED",
            "OTP_USED",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 409)

    def test_rate_limit_codes_use_too_many_requests(self):
        for code in ("COOLDOWN", "OTP_MAX_ATTEMPTS", "RATE_LIMIT", "RATE_LIMITED"):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 429)

    def test_dependency_codes_use_service_unavailable(self):
        for code in (
            "BACKGROUND_REMOVAL_UNAVAILABLE",
            "CALLBACK_AUTH_NOT_CONFIGURED",
            "CONFIG_ERROR",
            "IMAGE_SEARCH_UNAVAILABLE",
            "MAP_SERVICE_ERROR",
            "SERVICE_UNAVAILABLE",
            "TRANSLATION_UNAVAILABLE",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 503)

    def test_invalid_video_callback_uses_bad_request(self):
        self.assertEqual(http_status_for_code("VIDEO_CALLBACK_INVALID"), 400)

    def test_callback_and_worker_failure_codes_use_bad_gateway(self):
        for code in (
            "ANALYTICS_CALLBACK_FAILED",
            "BACKGROUND_REMOVAL_FAILED",
            "CALLBACK_FAILED",
            "IMAGE_SEARCH_FAILED",
            "MODERATION_CALLBACK_FAILED",
            "SEARCH_RANKING_CALLBACK_FAILED",
            "VIDEO_CALLBACK_FAILED",
        ):
            with self.subTest(code=code):
                self.assertEqual(http_status_for_code(code), 502)

    def test_fail_sets_mapped_status(self):
        response = fail("Map failed.", error="MAP_SERVICE_ERROR")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "MAP_SERVICE_ERROR")
        self.assertNotIn("code", response)
        self.assertEqual(frappe.local.response.get("http_status_code"), 503)

    def test_explicit_http_status_override_wins(self):
        response = fail("Custom.", error="VALIDATION_ERROR", http_status=400)
        self.assertFalse(response["ok"])
        self.assertEqual(frappe.local.response.get("http_status_code"), 400)

    def test_unknown_error_defaults_to_bad_request(self):
        response = fail("Unknown.", error="SOME_UNKNOWN_CODE")
        self.assertFalse(response["ok"])
        self.assertEqual(frappe.local.response.get("http_status_code"), 400)

    def test_all_literal_api_error_codes_are_mapped(self):
        used_codes = self._literal_fail_errors_used_by_app()
        missing = sorted(code for code in used_codes if code not in DEFAULT_HTTP_STATUS_MAP)
        self.assertFalse(missing, f"Unmapped API error values: {', '.join(missing)}")

    @staticmethod
    def _literal_fail_errors_used_by_app() -> set[str]:
        app_root = Path(__file__).resolve().parents[1]
        codes: set[str] = set()

        for path in app_root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "error":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        codes.add(value.value)

        return codes
