from __future__ import annotations

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.auth.user_controller import mark_aos_managed_website_user_creation


class TestAOSMediaObject(FrappeTestCase):
    def setUp(self):
        self.user = self._make_user()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.delete("AOS Media Object", {"owner_user": self.user})
        frappe.db.delete("AOS Profile", {"user": self.user})
        frappe.db.delete("User", {"name": self.user})
        frappe.db.commit()

    def _make_user(self) -> str:
        email = f"media-doctype-{uuid.uuid4().hex[:10]}@example.com"
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Media",
                "last_name": "Object",
                "enabled": 1,
                "user_type": "Website User",
                "send_welcome_email": 0,
            }
        )
        mark_aos_managed_website_user_creation(user)
        user.insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "AOS Profile",
                "user": email,
                "account_status": "Active",
                "is_deleted": 0,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
        return email

    def _new_media(self, **overrides):
        values = {
            "doctype": "AOS Media Object",
            "owner_user": self.user,
            "purpose": "profile_image",
            "status": "Uploaded",
            "visibility": "Public",
            "bucket": "aos-public",
            "object_key": f"profiles/images/test/{uuid.uuid4().hex}.png",
            "original_filename": "avatar.png",
            "content_type": "image/png",
            "expected_size_bytes": 128,
            "size_bytes": 128,
            "checksum": "a" * 64,
        }
        values.update(overrides)
        return frappe.get_doc(values)

    def test_valid_media_object_persists_canonical_metadata(self):
        doc = self._new_media(original_filename="  avatar.png  ")
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.original_filename, "avatar.png")
        self.assertEqual(doc.visibility, "Public")
        self.assertEqual(doc.status, "Uploaded")

    def test_private_media_rejects_persisted_public_url(self):
        doc = self._new_media(
            purpose="verification_document",
            visibility="Private",
            bucket="aos-private",
            object_key=f"verification/documents/test/{uuid.uuid4().hex}.pdf",
            original_filename="identity.pdf",
            content_type="application/pdf",
            public_url="https://files.example.test/aos-private/identity.pdf",
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_storage_identity_rejects_traversal(self):
        doc = self._new_media(object_key="profiles/images/../secret.png")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_unknown_purpose_is_rejected(self):
        doc = self._new_media(purpose="unknown_purpose")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_attached_state_requires_resource_identity(self):
        doc = self._new_media(status="Attached")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_invalid_checksum_is_rejected(self):
        doc = self._new_media(checksum="not-a-sha256")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)
