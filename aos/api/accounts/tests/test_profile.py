from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.accounts.profile import get_profile_impl, update_profile_impl
from aos.services.accounts.errors import AccountError, AccountNotFoundError, AccountValidationError
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.accounts.profile_service import AccountProfileService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class AccountsProfileIntegrationTests(AOSFeatureTestMixin, IntegrationTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("accounts-profile")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_authenticated_self_profile_uses_public_account_id_and_hides_internal_user(self):
        user = self.make_user("self")
        frappe.set_user(user)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            response = get_profile_impl()
        self.assertTrue(response.get("ok"), response)
        data = response["data"]
        self.assertEqual(data["account_id"], ensure_public_account_id(user))
        self.assertEqual(data["email"], user)
        self.assertNotIn("internal_user", data)
        self.assertNotIn("user", data)

    def test_public_profile_requires_canonical_account_id_and_is_privacy_bounded(self):
        viewer = self.make_user("viewer")
        target = self.make_user("target")
        frappe.set_user(viewer)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            response = get_profile_impl(account_id=ensure_public_account_id(target))
        self.assertTrue(response.get("ok"), response)
        data = response["data"]
        for private in (
            "email",
            "phone",
            "legal_name",
            "date_of_birth",
            "roles",
            "enabled",
            "profile_image_media",
            "internal_user",
        ):
            self.assertNotIn(private, data)
        self.assertEqual(data["account_id"], ensure_public_account_id(target))

    def test_public_profile_rejects_email_alias_and_unknown_target_alias(self):
        viewer = self.make_user("viewer-strict")
        target = self.make_user("target-strict")
        frappe.set_user(viewer)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            email_response = get_profile_impl(account_id=target)
            alias_response = get_profile_impl(target_user=ensure_public_account_id(target))
        self.assertEqual(email_response.get("error"), "INVALID_ACCOUNT_ID")
        self.assertEqual(alias_response.get("error"), "INVALID_PROFILE_FIELD")

    def test_profile_update_rejects_mass_assignment_and_immutable_fields(self):
        user = self.make_user("mass-assignment")
        frappe.set_user(user)
        for field, value in (
            ("account_id", "ACC-AAAAAAAAAAAAAAAAAAAA"),
            ("email", "other@example.com"),
            ("account_status", "Active"),
            ("is_verified", 1),
            ("roles", ["System Manager"]),
            ("seller", {"status": "Active"}),
        ):
            with self.subTest(field=field), patch("aos.api.accounts.profile.rate_limit", return_value=None):
                response = update_profile_impl(**{field: value})
            self.assertEqual(response.get("error"), "INVALID_PROFILE_FIELD")

    def test_profile_update_normalizes_allowed_fields(self):
        user = self.make_user("update")
        frappe.set_user(user)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            response = update_profile_impl(display_name="  New   Name  ", bio="  hello   world  ")
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["display_name"], "New Name")
        self.assertEqual(response["data"]["bio"], "hello world")

    def test_cross_user_profile_media_is_rejected(self):
        owner = self.make_user("media-owner")
        other = self.make_user("media-other")
        media = self.make_media(owner=other, purpose="profile_image")
        frappe.set_user(owner)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            response = update_profile_impl(avatar_media_id=media.name)
        self.assertEqual(response.get("error"), "MEDIA_ACCESS_DENIED")
        self.assertFalse(frappe.db.get_value("AOS Profile", {"user": owner}, "profile_image_media"))

    def test_wrong_media_purpose_is_rejected(self):
        owner = self.make_user("purpose")
        media = self.make_media(owner=owner, purpose="ad_image")
        frappe.set_user(owner)
        with patch("aos.api.accounts.profile.rate_limit", return_value=None):
            response = update_profile_impl(avatar_media_id=media.name)
        self.assertEqual(response.get("error"), "INVALID_MEDIA_PURPOSE")
        self.assertFalse(frappe.db.get_value("AOS Profile", {"user": owner}, "profile_image_media"))

    def test_public_profile_hides_suspended_and_deleted_accounts(self):
        viewer = self.make_user("state-viewer")
        target = self.make_user("state-target")
        service = AccountProfileService()
        account_id = ensure_public_account_id(target)
        for status in ("Suspended", "Deleted"):
            frappe.db.set_value("AOS Profile", account_id, "account_status", status, update_modified=False)
            with self.subTest(status=status), self.assertRaises(AccountNotFoundError):
                service.get_public_profile(reference=account_id, viewer=viewer)
            frappe.db.set_value("AOS Profile", account_id, "account_status", "Active", update_modified=False)

    def test_profile_mutation_locks_profile_row(self):
        repository = MagicMock()
        repository.lock_profile.return_value = None
        service = AccountProfileService(repository=repository)
        with self.assertRaises(AccountNotFoundError):
            service.update_profile(user="owner@example.com", payload={"bio": "hello"})
        repository.lock_profile.assert_called_once_with("owner@example.com")

    def test_public_blocked_relationship_is_not_exposed_as_profile(self):
        row = frappe._dict(
            user="target@example.com",
            account_id="ACC-AAAAAAAAAAAAAAAAAAAA",
            enabled=1,
            account_status="Active",
        )
        repository = MagicMock()
        repository.account_by_public_id.return_value = row
        service = AccountProfileService(repository=repository)
        with (
            patch(
                "aos.services.accounts.profile_service.build_relationship_status",
                return_value={"is_blocked_by_me": True, "has_blocked_me": False},
            ),
            self.assertRaises(AccountError) as caught,
        ):
            service.get_public_profile(reference=row.account_id, viewer="viewer@example.com")
        self.assertEqual(caught.exception.code, "PROFILE_UNAVAILABLE")

    def test_invalid_profile_id_fails_before_repository_lookup(self):
        repository = MagicMock()
        service = AccountProfileService(repository=repository)
        with self.assertRaises(AccountValidationError):
            service.get_public_profile(reference="target@example.com", viewer="viewer@example.com")
        repository.account_by_public_id.assert_not_called()
