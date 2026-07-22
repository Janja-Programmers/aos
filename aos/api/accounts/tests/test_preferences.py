from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

from aos.services.user_preference_service import update_user_preference


class AccountPreferenceIntegrationTests(IntegrationTestCase):
    @patch("aos.services.user_preference_service.get_user_preference_for_update")
    def test_partial_update_preserves_unrelated_fields(self, get_locked):
        get_locked.return_value = MagicMock(name="PREF-1", country="Kenya", currency="KES", language="en", get=lambda key: "")
        doc = MagicMock(country="Kenya", currency="KES", language="en", location="")
        with patch("aos.services.user_preference_service.validate_language", return_value=("sw", None)), patch("frappe.get_doc", return_value=doc):
            updated, error = update_user_preference("user@example.com", language="sw")
        self.assertIsNone(error)
        self.assertEqual(updated.country, "Kenya")
        self.assertEqual(updated.currency, "KES")
        self.assertEqual(updated.language, "sw")
