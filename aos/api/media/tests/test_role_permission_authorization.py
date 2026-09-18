from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import call, patch

from aos.services.media.media_service import MediaPermissionError, MediaService


class TestMediaRolePermissionAuthorization(TestCase):
    def test_category_writer_can_manage_media_uploaded_by_another_admin(self):
        doc = SimpleNamespace(
            name="MEDIA-00000000000000000000000000000006",
            owner_user="original-admin@example.com",
            attached_doctype="AOS Category",
            attached_name="Vehicles",
        )
        service = MediaService(storage=object())

        with patch(
            "aos.services.media.media_service.has_doctype_permission",
            return_value=True,
        ) as has_permission:
            service.assert_user_can_manage(doc, "moderator@example.com")

        has_permission.assert_called_once_with(
            user="moderator@example.com",
            doctype="AOS Category",
            ptype="write",
            docname="Vehicles",
        )

    def test_category_writer_denial_does_not_fall_back_to_role_names(self):
        doc = SimpleNamespace(
            name="MEDIA-00000000000000000000000000000006",
            owner_user="original-admin@example.com",
            attached_doctype="AOS Category",
            attached_name="Vehicles",
        )
        service = MediaService(storage=object())

        with patch(
            "aos.services.media.media_service.has_doctype_permission",
            return_value=False,
        ) as has_permission:
            with self.assertRaises(MediaPermissionError) as raised:
                service.assert_user_can_manage(doc, "moderator@example.com")

        self.assertEqual(raised.exception.code, "MEDIA_OWNERSHIP_REQUIRED")
        self.assertEqual(
            has_permission.call_args_list,
            [
                call(
                    user="moderator@example.com",
                    doctype="AOS Category",
                    ptype="write",
                    docname="Vehicles",
                ),
                call(
                    user="moderator@example.com",
                    doctype="AOS Media Object",
                    ptype="write",
                    doc=doc,
                ),
            ],
        )

    def test_verification_reviewer_read_uses_request_permission(self):
        doc = SimpleNamespace(
            name="MEDIA-00000000000000000000000000000007",
            purpose="verification_document",
            visibility="Private",
            owner_user="owner@example.com",
            status="Attached",
            attached_doctype="AOS Verification Request",
            attached_name="VERIFY-0001",
        )
        service = MediaService(storage=object())

        with patch(
            "aos.services.media.media_service.has_doctype_permission",
            return_value=True,
        ) as has_permission:
            service.assert_user_can_read(doc, "moderator@example.com")

        has_permission.assert_called_once_with(
            user="moderator@example.com",
            doctype="AOS Verification Request",
            ptype="read",
            docname="VERIFY-0001",
        )
