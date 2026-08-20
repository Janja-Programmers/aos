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
    def test_category_icon_upload_requires_category_write_permission(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with patch(
            "aos.services.media.resource_authorization.has_doctype_permission",
            return_value=False,
        ) as has_permission:
            with self.assertRaises(ResourceAuthorizationError):
                assert_purpose_upload_allowed(user="seller@example.com", policy=policy)

        has_permission.assert_called_once_with(
            user="seller@example.com",
            doctype="AOS Category",
            ptype="write",
        )

    def test_role_permission_grant_allows_category_icon_upload(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with patch(
            "aos.services.media.resource_authorization.has_doctype_permission",
            return_value=True,
        ):
            assert_purpose_upload_allowed(user="moderator@example.com", policy=policy)

    def test_user_without_category_write_cannot_attach_media_to_category(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with (
            patch("aos.services.media.resource_authorization.frappe.db.exists", return_value=True),
            patch(
                "aos.services.media.resource_authorization.has_doctype_permission",
                return_value=False,
            ),
        ):
            with self.assertRaises(ResourceAuthorizationError):
                assert_attachment_target_allowed(
                    user="seller@example.com",
                    policy=policy,
                    attached_doctype="AOS Category",
                    attached_name="Beauty",
                )

    def test_role_permission_grant_allows_category_attachment(self):
        policy = MEDIA_PURPOSES["category_icon"]
        with (
            patch("aos.services.media.resource_authorization.frappe.db.exists", return_value=True),
            patch(
                "aos.services.media.resource_authorization.has_doctype_permission",
                return_value=True,
            ) as has_permission,
        ):
            assert_attachment_target_allowed(
                user="moderator@example.com",
                policy=policy,
                attached_doctype="AOS Category",
                attached_name="Beauty",
            )

        has_permission.assert_called_once_with(
            user="moderator@example.com",
            doctype="AOS Category",
            ptype="write",
            docname="Beauty",
        )
