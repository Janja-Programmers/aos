from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.services.accounts.serializers import serialize_private_profile_row, serialize_public_profile_row


class AccountsSerializerIntegrationTests(IntegrationTestCase):
    def _row(self):
        return frappe._dict(
            account_id="ACC-AAAAAAAAAAAAAAAAAAAA",
            user="dan@example.com",
            email="dan@example.com",
            display_name="Dan",
            legal_name="Daniel Private",
            bio="Bio",
            phone="+254700000000",
            date_of_birth=None,
            gender="",
            profile_image_media="",
            account_status="Active",
            total_followers=0,
            total_following=0,
            total_friends=0,
            is_verified=1,
            enabled=1,
        )

    def test_public_profile_uses_canonical_keys_and_hides_private_fields(self):
        with patch(
                "aos.services.accounts.serializers.seller_summary",
                return_value={"is_seller": False, "seller_id": None, "status": None},
            ):
            payload = serialize_public_profile_row(
                self._row(),
                relationship={"account_id": "ACC-BBBBBBBBBBBBBBBBBBBB", "can_message": True},
            )
        for field in (
            "email",
            "phone",
            "legal_name",
            "roles",
            "internal_user",
            "user",
            "enabled",
            "profile_image_media",
        ):
            self.assertNotIn(field, payload)
        self.assertEqual(payload["account_id"], "ACC-AAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(payload["display_name"], "Dan")
        self.assertTrue(payload["can_message"])

    def test_private_profile_never_exposes_frappe_user_name(self):
        with (
            patch(
                "aos.services.accounts.serializers.seller_summary",
                return_value={"is_seller": False, "seller_id": None, "status": None},
            ),
            patch("aos.services.accounts.serializers.verification_summary", return_value={"status": None}),
            patch(
                "aos.services.accounts.serializers.get_user_preference",
                return_value=frappe._dict(country="Kenya", currency="KES", language="en", location=""),
            ),
            patch("aos.services.accounts.serializers.frappe.get_roles", return_value=["All"]),
        ):
            payload = serialize_private_profile_row(self._row())
        self.assertNotIn("internal_user", payload)
        self.assertNotIn("user", payload)
        self.assertEqual(payload["email"], "dan@example.com")
        self.assertEqual(set(payload["preferences"]), {"country", "currency", "language", "location"})
