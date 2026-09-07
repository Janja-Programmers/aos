from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.rate_limits import auth_rate_limit
from aos.api.auth.session_hooks import enforce_aos_website_login
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAuthSecurityContracts(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("auth-security")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_rate_limit_uses_atomic_shared_redis_script_and_fails_closed(self):
        cache = Mock()
        cache.make_key.return_value = b"site-aos:auth:login:ip:test"
        cache.eval.return_value = 1
        with patch("frappe.cache", return_value=cache):
            self.assertIsNone(auth_rate_limit(operation="login", dimension="ip", value="127.0.0.1", limit=2, message="limited"))
        cache.make_key.assert_called_once()
        cache.eval.assert_called_once_with(
            auth_rate_limit.__globals__["_WINDOW_SCRIPT"], 1, b"site-aos:auth:login:ip:test", 3600
        )

        cache.eval.side_effect = RuntimeError("redis unavailable")
        with patch("frappe.cache", return_value=cache), patch("frappe.log_error"):
            response = auth_rate_limit(operation="login", dimension="ip", value="127.0.0.1", limit=2, message="limited")
        self.assertEqual(response.get("error"), "SERVICE_UNAVAILABLE")

    def test_framework_generic_login_is_blocked_for_aos_website_users(self):
        user = self.make_user("website")
        manager = SimpleNamespace(user=user)
        frappe.flags.aos_auth_session_creation = False
        with self.assertRaises(frappe.AuthenticationError):
            enforce_aos_website_login(manager)
        frappe.flags.aos_auth_session_creation = True
        enforce_aos_website_login(manager)


    def test_framework_auth_bypass_guards_and_fresh_install_signup_default(self):
        from aos import hooks
        from aos.install import after_install

        expected = {
            "frappe.core.doctype.user.user.sign_up",
            "frappe.core.doctype.user.user.reset_password",
            "frappe.core.doctype.user.user.update_password",
            "frappe.core.doctype.user.user.change_password",
            "frappe.core.doctype.user.user.verify_password",
        }
        self.assertTrue(expected.issubset(set(hooks.override_whitelisted_methods)))
        with patch("frappe.db.set_single_value") as set_single:
            after_install()
        set_single.assert_called_once_with("Website Settings", "disable_signup", 1)


    def test_framework_recovery_guard_preserves_system_users_but_blocks_website_users(self):
        from aos.api.auth.framework_guards import guard_frappe_reset_password, guard_frappe_update_password

        with patch("frappe.db.get_value", return_value="System User"), patch(
            "aos.api.auth.framework_guards._frappe_reset_password", return_value="system-reset"
        ) as original_reset:
            self.assertEqual(guard_frappe_reset_password("ops@example.com"), "system-reset")
            original_reset.assert_called_once_with("ops@example.com")

        with patch("frappe.db.get_value", return_value="Website User"), patch(
            "aos.api.auth.framework_guards._frappe_reset_password"
        ) as original_reset, patch("frappe.msgprint"):
            self.assertIsNone(guard_frappe_reset_password("customer@example.com"))
            original_reset.assert_not_called()

        with patch("aos.api.auth.framework_guards._system_user_for_reset_key", return_value=False), patch(
            "aos.api.auth.framework_guards._session_user_type", return_value="Website User"
        ):
            with self.assertRaises(frappe.PermissionError):
                guard_frappe_update_password(new_password="StrongPass123!", key="website-reset-key")

    def test_public_v1_wrappers_use_safe_auth_endpoint_boundary(self):
        from aos.api.v1 import auth as public_auth

        source = inspect.getsource(public_auth)
        self.assertEqual(source.count("execute_auth_endpoint("), 15)
        self.assertNotIn("return _login_impl(**kwargs)", source)
        self.assertNotIn("return _register_impl(**kwargs)", source)

    def test_public_boundary_strips_only_frappe_cmd_transport_field(self):
        from aos.api.auth.contracts import execute_auth_endpoint

        captured = {}

        def handler(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        response = execute_auth_endpoint(handler, {"cmd": "aos.api.v1.auth.login", "identifier": "x@example.com"})
        self.assertTrue(response.get("ok"))
        self.assertEqual(captured, {"identifier": "x@example.com"})

    def test_public_boundary_rolls_back_and_returns_safe_dependency_error(self):
        from aos.api.v1 import auth as public_auth

        with patch.object(public_auth, "_login_impl", side_effect=RuntimeError("database secret detail")), patch(
            "frappe.db.rollback"
        ) as rollback, patch("frappe.log_error"):
            response = public_auth.login(
                identifier="person@example.com",
                password="SensitivePassword123!",
                client_type="web",
            )
        rollback.assert_called_once()
        self.assertEqual(response.get("error"), "SERVICE_UNAVAILABLE")
        self.assertNotIn("database secret detail", response.get("message", ""))
        self.assertNotIn("SensitivePassword123!", response.get("message", ""))

    def test_auth_source_has_no_plaintext_secret_logging_or_process_local_security_state(self):
        from aos.api.auth import session, verification
        session_source = inspect.getsource(session)
        verification_source = inspect.getsource(verification)
        self.assertNotIn("password=", session_source.lower())
        self.assertNotIn("otp=", verification_source.lower())
        session_control_source = inspect.getsource(__import__("aos.api.auth.session_control", fromlist=["x"]))
        self.assertNotIn("delete_keys(", session_control_source)
        auth_root = Path(__file__).resolve().parents[1]
        production_sources = "\n".join(path.read_text() for path in auth_root.glob("*.py"))
        self.assertNotIn("frappe.get_traceback()", production_sources)
