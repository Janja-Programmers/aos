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

    def test_non_ip_rate_limit_dimensions_do_not_expose_identifiers_in_redis_keys(self):
        cache = Mock()
        cache.make_key.side_effect = lambda key: key
        cache.eval.return_value = 1
        subject = "provider-subject-123456"
        with patch("frappe.cache", return_value=cache):
            response = auth_rate_limit(
                operation="google_login",
                dimension="subject",
                value=subject,
                limit=2,
                message="limited",
            )
        self.assertIsNone(response)
        redis_key = str(cache.make_key.call_args.args[0])
        self.assertNotIn(subject, redis_key)
        self.assertIn("hmac256", redis_key)

    def test_oidc_unknown_kid_waiting_on_another_node_refresh_is_dependency_failure(self):
        from aos.api.auth.oidc import OIDCDependencyError, verify_rs256_token

        cache = Mock()
        cache.make_key.return_value = b"site-aos:google-jwks:refresh-guard"
        cache.set.return_value = False
        stale_jwks = {"keys": [{"kid": "old-provider-key"}]}
        with patch("aos.api.auth.oidc.jwt.get_unverified_header", return_value={"alg": "RS256", "kid": "rotated-key"}), patch(
            "aos.api.auth.oidc._load_jwks", return_value=stale_jwks
        ) as load_jwks, patch("frappe.cache", return_value=cache):
            with self.assertRaises(OIDCDependencyError):
                verify_rs256_token(
                    "opaque-id-token",
                    audiences=["client-id"],
                    issuer="https://issuer.example",
                    jwks_url="https://issuer.example/jwks",
                    cache_key="google-jwks",
                )

        self.assertEqual(load_jwks.call_count, 2)
        self.assertFalse(any(call.kwargs.get("force_refresh") for call in load_jwks.call_args_list))

    def test_managed_website_user_creation_bypasses_only_framework_global_throttle(self):
        from aos.api.auth.user_controller import mark_aos_managed_website_user_creation

        def simulated_framework_throttle():
            if not frappe.flags.in_import:
                frappe.throw("Throttled")

        spoofed = frappe.new_doc("User")
        spoofed.user_type = "Website User"
        spoofed.flags.aos_managed_website_user_creation = True
        with patch(
            "frappe.core.doctype.user.user.throttle_user_creation",
            side_effect=simulated_framework_throttle,
        ):
            with self.assertRaises(frappe.ValidationError):
                spoofed.before_insert()

        managed = frappe.new_doc("User")
        managed.user_type = "Website User"
        mark_aos_managed_website_user_creation(managed)
        previous = frappe.flags.get("in_import", False)
        with patch(
            "frappe.core.doctype.user.user.throttle_user_creation",
            side_effect=simulated_framework_throttle,
        ):
            managed.before_insert()
        self.assertEqual(frappe.flags.get("in_import", False), previous)

        system_user = frappe.new_doc("User")
        system_user.user_type = "System User"
        with self.assertRaises(ValueError):
            mark_aos_managed_website_user_creation(system_user)

    def test_framework_generic_login_is_blocked_for_aos_website_users(self):
        user = self.make_user("website")
        manager = SimpleNamespace(user=user)
        previous = frappe.flags.get("aos_auth_session_creation", None)
        try:
            frappe.flags.aos_auth_session_creation = False
            with self.assertRaises(frappe.AuthenticationError):
                enforce_aos_website_login(manager)
            frappe.flags.aos_auth_session_creation = True
            enforce_aos_website_login(manager)
        finally:
            if previous is None:
                frappe.flags.pop("aos_auth_session_creation", None)
            else:
                frappe.flags.aos_auth_session_creation = previous


    def test_framework_auth_bypass_guards_and_fresh_install_signup_default(self):
        from aos import hooks
        from aos.install import after_install

        expected = {
            "frappe.core.doctype.user.user.sign_up",
            "frappe.core.doctype.user.user.reset_password",
            "frappe.core.doctype.user.user.update_password",
            "frappe.core.doctype.user.user.verify_password",
        }
        self.assertTrue(expected.issubset(set(hooks.override_whitelisted_methods)))
        self.assertIn("aos.api.auth.user_controller.AOSAuthUserMixin", hooks.extend_doctype_class.get("User", []))
        with patch("frappe.db.set_single_value") as set_single, patch(
            "aos.services.reports.catalog.install_canonical_report_reasons"
        ) as install_reasons:
            after_install()
        set_single.assert_called_once_with("Website Settings", "disable_signup", 1)
        install_reasons.assert_called_once_with()


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

    def test_public_v1_wrappers_use_canonical_transport_boundary_with_auth_policy(self):
        from aos.api.v1 import auth as public_auth

        source = inspect.getsource(public_auth)
        self.assertEqual(source.count("_execute_endpoint("), 16)
        self.assertEqual(source.count("on_unexpected_exception=_auth_exception_policy"), 16)
        self.assertNotIn("execute_auth_endpoint", source)
        self.assertNotIn("return _login_impl(**kwargs)", source)
        self.assertNotIn("return _register_impl(**kwargs)", source)

    def test_public_boundary_strips_only_frappe_cmd_transport_field(self):
        from aos.api.shared.transport import execute_endpoint

        captured = {}

        def handler(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        response = execute_endpoint(
            handler,
            {"cmd": "aos.api.v1.auth.login", "identifier": "x@example.com"},
        )
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
