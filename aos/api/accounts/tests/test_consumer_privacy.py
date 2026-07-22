from __future__ import annotations

from frappe.tests import IntegrationTestCase

from aos.api.shared.user_display import normalize_user_display


class AccountsConsumerPrivacyTests(IntegrationTestCase):
    def test_email_shaped_fallback_is_masked(self):
        payload = normalize_user_display(user="user@example.com", full_name="", fallback_to_user=True)
        self.assertEqual(payload["display_name"], "AOS User")
        self.assertTrue(payload["user"].startswith("ACC-"))
