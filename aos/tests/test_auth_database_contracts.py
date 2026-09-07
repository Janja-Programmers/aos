from __future__ import annotations

import inspect
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.locking import lock_user
from aos.aos.doctype.aos_email_verification.aos_email_verification import verification_name


class TestAuthDatabaseContracts(FrappeTestCase):
    def test_verification_primary_key_is_deterministic_user_purpose_uniqueness(self):
        name = verification_name("person@example.com", "password_reset")
        self.assertTrue(name.startswith("authv-"))
        self.assertEqual(len(name), 70)
        meta = frappe.get_meta("AOS Email Verification")
        self.assertTrue(meta.has_field("otp_password_hash"))
        self.assertFalse(meta.has_field("otp_hash"))
        self.assertGreaterEqual(int(meta.get_field("otp_password_hash").length or 0), 255)
        self.assertEqual(int(meta.get_field("reset_token_hash").length or 0), 64)
        self.assertTrue(meta.get_field("user").search_index)

        from aos.aos.doctype.aos_email_verification.aos_email_verification import verification_name as controller_name
        self.assertEqual(controller_name("person@example.com", "password_reset"), name)
        self.assertFalse(bool(meta.index_web_pages_for_search))

    def test_social_identity_has_per_provider_user_uniqueness(self):
        meta = frappe.get_meta("AOS Auth Identity")
        field = meta.get_field("user_provider_key")
        self.assertTrue(field.unique)
        self.assertTrue(meta.get_field("user").search_index)

    def test_security_state_doctypes_are_not_desk_mutable_and_indexes_match_hot_paths(self):
        verification = frappe.get_meta("AOS Email Verification")
        identity = frappe.get_meta("AOS Auth Identity")
        self.assertFalse(bool(verification.get_field("email").search_index))
        for meta in (verification, identity):
            permissions = [row for row in meta.permissions if row.role == "System Manager"]
            self.assertEqual(len(permissions), 1)
            row = permissions[0]
            self.assertTrue(bool(row.read))
            self.assertTrue(bool(row.report))
            for field in ("create", "write", "delete", "share", "export", "print", "email"):
                self.assertFalse(bool(getattr(row, field, 0)), f"{meta.name} unexpectedly grants {field}")

    def test_security_mutations_use_cross_node_database_row_locks(self):
        source = inspect.getsource(lock_user)
        self.assertIn("FOR UPDATE", source)
        from aos.api.auth import password_change, password_reset, session
        for function in (session.login_impl, password_change.change_password_impl, password_reset.forgot_password_reset_impl):
            self.assertIn("lock_user", inspect.getsource(function))

    def test_auth_patch_is_removed_for_fresh_site(self):
        root = Path(__file__).resolve().parents[2]
        patches = (root / "aos" / "patches.txt").read_text()
        self.assertNotIn("add_auth_indexes", patches)
        self.assertFalse((root / "aos" / "patches" / "v1_0" / "add_auth_indexes.py").exists())

    def test_password_and_otp_implementations_do_not_use_fast_otp_sha256_or_manual_commits(self):
        root = Path(__file__).resolve().parents[2] / "aos" / "api" / "auth"
        otp_source = (root / "otp_service.py").read_text()
        verification = (root / "verification.py").read_text()
        self.assertIn("passlibctx", verification)
        self.assertNotIn("sha256_hash", verification)
        for name in ("register.py", "password_reset.py", "password_change.py", "otp.py"):
            self.assertNotIn("frappe.db.commit()", (root / name).read_text())

    def test_frappe_parallel_public_auth_paths_are_guarded(self):
        from aos import hooks

        required = {
            "frappe.core.doctype.user.user.sign_up",
            "frappe.core.doctype.user.user.reset_password",
            "frappe.core.doctype.user.user.update_password",
            "frappe.core.doctype.user.user.change_password",
            "frappe.core.doctype.user.user.verify_password",
        }
        self.assertTrue(required.issubset(hooks.override_whitelisted_methods))
        self.assertEqual(hooks.after_install, "aos.install.after_install")
