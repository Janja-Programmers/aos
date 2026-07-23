from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.services.media.media_purposes import MEDIA_PURPOSES
from aos.services.media.resource_authorization import (
    ResourceAuthorizationError,
    assert_attachment_target_allowed,
    assert_purpose_upload_allowed,
)


class TestCatalogSecurityBoundaries(TestCase):
    def test_category_icon_upload_requires_system_manager(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with patch("aos.services.media.resource_authorization.frappe.get_roles", return_value=["Seller"]):
            with self.assertRaises(ResourceAuthorizationError):
                assert_purpose_upload_allowed(user="seller@example.com", policy=policy)

    def test_non_admin_cannot_attach_media_to_category(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with (
            patch("aos.services.media.resource_authorization.frappe.db.exists", return_value=True),
            patch("aos.services.media.resource_authorization.frappe.get_roles", return_value=["Seller"]),
        ):
            with self.assertRaises(ResourceAuthorizationError):
                assert_attachment_target_allowed(
                    user="seller@example.com",
                    policy=policy,
                    attached_doctype="AOS Category",
                    attached_name="Beauty",
                )

    def test_system_manager_can_attach_media_to_category(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with (
            patch("aos.services.media.resource_authorization.frappe.db.exists", return_value=True),
            patch("aos.services.media.resource_authorization.frappe.get_roles", return_value=["System Manager"]),
        ):
            assert_attachment_target_allowed(
                user="administrator@example.com",
                policy=policy,
                attached_doctype="AOS Category",
                attached_name="Beauty",
            )
