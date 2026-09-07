from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

from aos.services.accounts.lifecycle_service import AccountLifecycleService


class AccountsLifecycleIntegrationTests(IntegrationTestCase):
    @patch("aos.services.accounts.lifecycle_service.revoke_account_access", return_value={})
    def test_duplicate_delete_is_idempotent(self, _revoke):
        service = AccountLifecycleService(media=MagicMock())
        profile = MagicMock(account_status="Deleted", restore_deadline="2099-01-01 00:00:00")
        with patch.object(service, "_lock_profile", return_value=profile):
            result = service.delete(user="user@example.com")
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["status"], "Deleted")
