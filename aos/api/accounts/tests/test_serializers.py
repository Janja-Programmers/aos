from __future__ import annotations

from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from aos.services.accounts.serializers import serialize_public_profile


class AccountsSerializerIntegrationTests(IntegrationTestCase):
    @patch("aos.services.accounts.serializers._friends_count", return_value=0)
    @patch("aos.services.accounts.serializers.seller_summary", return_value={"is_seller": False, "seller_id": None, "status": None})
    @patch("aos.services.accounts.serializers.public_account_id_for_user", return_value="ACC-AAAAAAAAAAAAAAAAAAAA")
    @patch("aos.services.accounts.serializers._user")
    @patch("aos.services.accounts.serializers._profile")
    def test_public_profile_uses_canonical_keys_and_hides_private_fields(self, profile, user, *_):
        profile.return_value = {"display_name": "Dan", "bio": "Bio", "account_status": "Active"}
        user.return_value = {"email": "dan@example.com", "full_name": "Dan", "enabled": 1}
        payload = serialize_public_profile("dan@example.com")
        self.assertNotIn("email", payload)
        self.assertNotIn("phone", payload)
        self.assertNotIn("roles", payload)
        self.assertNotIn("verified_by", payload)
        self.assertNotIn("user", payload)
        self.assertNotIn("full_name", payload)
        self.assertNotIn("user_image", payload)
        self.assertNotIn("total_followers", payload)
        self.assertEqual(payload["account_id"], "ACC-AAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(payload["display_name"], "Dan")
