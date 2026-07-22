from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

from aos.services.accounts.profile_service import AccountProfileService


class AccountsProfileIntegrationTests(IntegrationTestCase):
    def test_cross_user_media_is_rejected_by_media_service(self):
        media = MagicMock()
        media.attach_media.side_effect = PermissionError("denied")
        service = AccountProfileService(media=media)
        with patch.object(service, "_lock_profile") as lock, patch("frappe.get_doc"):
            lock.return_value = MagicMock(profile_image_media="")
            with self.assertRaises(Exception):
                service.update_profile(user="owner@example.com", payload={"avatar_media_id": "MEDIA-ABCDEF"})
