from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestAuthArchitectureContracts(unittest.TestCase):
    def test_profile_has_one_public_identity_and_one_deleted_state(self):
        schema = json.loads(source("aos/aos/doctype/aos_profile/aos_profile.json"))
        fields = {row.get("fieldname") for row in schema.get("fields", [])}
        self.assertEqual(schema.get("autoname"), "prompt")
        self.assertIn("user", fields)
        self.assertNotIn("public_id", fields)
        self.assertNotIn("is_deleted", fields)
        self.assertNotIn("location", fields)
        self.assertNotIn("verified_by", fields)
        self.assertNotIn("verified_on", fields)
        self.assertNotIn("deactivated_at", fields)
        account_status = next(row for row in schema["fields"] if row.get("fieldname") == "account_status")
        self.assertEqual(account_status.get("options"), "Active\nDeleted\nSuspended")
        purge_status = next(row for row in schema["fields"] if row.get("fieldname") == "purge_status")
        self.assertEqual(purge_status.get("default"), "")

    def test_profile_identity_is_acc_primary_key_and_public_resolver_rejects_email(self):
        identity = source("aos/services/accounts/identity.py")
        profile = source("aos/aos/doctype/aos_profile/aos_profile.py")
        self.assertIn('getattr(profile_or_user, "name"', identity)
        self.assertIn('return frappe.db.get_value("AOS Profile", account_id, "user")', identity)
        self.assertNotIn("allow_legacy", identity)
        self.assertNotIn('{"public_id":', identity)
        self.assertIn("generate_public_account_id", profile)
        self.assertIn("Account id is immutable", profile)

    def test_profile_fields_are_not_mirrored_to_frappe_user_except_framework_projections(self):
        service = source("aos/services/accounts/profile_service.py")
        for forbidden in ("user_doc.bio", "user_doc.mobile_no", "user_doc.birth_date", "user_doc.gender", "user_doc.location"):
            self.assertNotIn(forbidden, service)
        self.assertIn("user_doc.first_name", service)
        self.assertIn("user_doc.full_name", service)
        self.assertIn("user_doc.user_image", service)

    def test_aos_serializers_do_not_read_framework_profile_projections(self):
        serializer = source("aos/services/accounts/serializers.py")
        self.assertNotIn("u.full_name", serializer)
        self.assertNotIn("u.first_name", serializer)
        self.assertNotIn("u.user_image", serializer)
        self.assertNotIn('row.user_image', serializer)
        self.assertNotIn('_get(user_row, "full_name")', serializer)

    def test_auth_email_greetings_read_canonical_profile_name(self):
        for relative in (
            "aos/api/auth/otp.py",
            "aos/api/auth/password_reset.py",
            "aos/api/auth/delete_account.py",
            "aos/api/auth/two_factor.py",
        ):
            module = source(relative)
            self.assertNotIn('get_value("User", user_name, "first_name")', module)
        helpers = source("aos/api/auth/account_helpers.py")
        self.assertIn("def profile_display_name", helpers)
        self.assertIn('"AOS Profile"', helpers)

    def test_auth_challenge_is_generic_and_minimal(self):
        schema = json.loads(source("aos/aos/doctype/aos_auth_challenge/aos_auth_challenge.json"))
        fields = {row.get("fieldname") for row in schema.get("fields", [])}
        self.assertEqual(schema.get("name"), "AOS Auth Challenge")
        self.assertNotIn("email", fields)
        self.assertNotIn("reset_token_hash", fields)
        self.assertNotIn("reset_token_expires_at", fields)
        self.assertIn("continuation_token_hash", fields)
        self.assertIn("continuation_expires_at", fields)
        purpose = next(row for row in schema["fields"] if row.get("fieldname") == "purpose")
        self.assertIn("two_factor", purpose.get("options", ""))
        self.assertFalse((ROOT / "aos/aos/doctype/aos_email_verification").exists())

    def test_social_identity_does_not_persist_provider_subject_or_email(self):
        schema = json.loads(source("aos/aos/doctype/aos_auth_identity/aos_auth_identity.json"))
        fields = {row.get("fieldname") for row in schema.get("fields", [])}
        self.assertNotIn("subject", fields)
        self.assertNotIn("email_at_link", fields)
        controller = source("aos/aos/doctype/aos_auth_identity/aos_auth_identity.py")
        self.assertIn("opaque_digest", controller)
        self.assertIn("aos_oidc_subject", controller)

    def test_auth_observability_uses_site_keyed_hmac(self):
        privacy = source("aos/utils/privacy.py")
        helpers = source("aos/api/auth/account_helpers.py")
        self.assertIn("hmac.new", privacy)
        self.assertIn("encryption_key", privacy)
        self.assertIn("opaque_identifier", helpers)
        self.assertNotIn("hashlib.sha256(identifier", helpers)
        self.assertNotIn("hashlib.sha256(user", helpers)

    def test_2fa_has_a_complete_public_continuation_endpoint(self):
        wrapper = source("aos/api/v1/auth/__init__.py")
        implementation = source("aos/api/auth/two_factor.py")
        self.assertIn("def verify_two_factor", wrapper)
        self.assertIn("lock_user(user)", implementation)
        self.assertIn("FOR UPDATE", implementation)
        self.assertIn("verify_public_otp", implementation)
        self.assertIn("continuation_token_hash", implementation)
        self.assertIn("value=token_digest(challenge_token)", implementation)
        self.assertNotIn("value=challenge_token", implementation)
        self.assertIn("consume=False", implementation)
        self.assertIn("ver.is_used = 1", implementation)
        self.assertIn("lm.post_login()", implementation)
        self.assertNotIn("frappe.db.commit", implementation)
        social = source("aos/api/auth/social_login.py")
        self.assertIn("requires_two_factor(user_name)", social)
        self.assertIn("issue_two_factor_challenge(user_name)", social)
        session_control = source("aos/api/auth/session_control.py")
        self.assertIn("def aos_session_creation_scope", session_control)

    def test_deactivation_endpoint_and_state_are_removed(self):
        wrapper = source("aos/api/v1/accounts/__init__.py")
        constants = source("aos/services/accounts/constants.py")
        self.assertNotIn("deactivate_account", wrapper)
        self.assertNotIn("Deactivated", constants)
        self.assertNotIn("ACCOUNT_STATUS_DEACTIVATED", constants)

    def test_aos_website_user_email_is_immutable_at_framework_layer(self):
        controller = source("aos/api/auth/user_controller.py")
        self.assertIn("def before_rename", controller)
        self.assertIn("AOS account email is immutable", controller)
        self.assertIn('== "Website User"', controller)

    def test_auth_session_does_not_emit_placeholder_expiry(self):
        serializer = source("aos/api/auth/serializers.py")
        tree = ast.parse(serializer)
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "serialize_session")
        fn_source = ast.get_source_segment(serializer, fn) or ""
        self.assertNotIn("expires_at", fn_source)


if __name__ == "__main__":
    unittest.main()
