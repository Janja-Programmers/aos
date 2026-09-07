from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.verification.get_my_verification import get_my_verification_impl
from aos.api.verification.submit_verification import submit_verification_impl
from aos.services.media.media_service import MediaPermissionError, MediaService
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestVerificationDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """Database-backed Verification behavior on a migrated Frappe test site."""

    def setUp(self):
        self.prefix = self.make_prefix("verification")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.owner = self.make_user("owner")
        self.other = self.make_user("other")
        self.media = self.make_media(
            owner=self.owner,
            purpose="verification_document",
            content_type="application/pdf",
            filename="identity.pdf",
            visibility="Private",
        )
        frappe.set_user(self.owner)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    @staticmethod
    def _individual(media_id: str, **overrides):
        payload = {
            "verification_type": "Individual",
            "legal_name": "  Jane   Doe  ",
            "phone_number": "+254700000001",
            "idempotency_key": "verification-test-key",
            "verification_documents": [
                {
                    "document_type": "Identity Document",
                    "document_number": "ID-123456789",
                    "media_id": media_id,
                }
            ],
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def _without_submit_limit():
        return patch("aos.api.verification.submit_verification.rate_limit", return_value=None)

    @staticmethod
    def _without_get_limit():
        return patch("aos.api.verification.get_my_verification.rate_limit", return_value=None)

    def _submit_individual(self, media_id: str | None = None, **overrides):
        with self._without_submit_limit():
            return submit_verification_impl(**self._individual(media_id or self.media.name, **overrides))

    def _review(self, status: str, *, reason: str | None = None):
        frappe.set_user("Administrator")
        request_name = frappe.db.get_value("AOS Verification Request", {"user": self.owner}, "name")
        doc = frappe.get_doc("AOS Verification Request", request_name)
        doc.status = status
        if reason is not None:
            doc.rejection_reason = reason
        with patch("aos.services.notification_service.NotificationService._deliver"):
            doc.save(ignore_permissions=True)
        return doc

    def test_submission_is_server_owned_idempotent_and_attaches_private_media(self):
        first = self._submit_individual()
        repeated = self._submit_individual()

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(first["data"]["id"], repeated["data"]["id"])
        self.assertEqual(first["data"]["status"], "Pending")
        self.assertNotIn("document_number", str(first["data"]))

        request = frappe.get_doc("AOS Verification Request", first["data"]["id"])
        self.assertEqual(request.user, self.owner)
        self.assertEqual(request.legal_name, "Jane Doe")
        self.assertEqual(request.status, "Pending")
        self.assertFalse(request.verified_by)
        self.assertFalse(request.verified_on)
        self.assertEqual(len(request.verification_documents), 1)

        media = frappe.get_doc("AOS Media Object", self.media.name)
        self.assertEqual(media.visibility, "Private")
        self.assertEqual(media.status, "Attached")
        self.assertEqual(media.attached_doctype, "AOS Verification Request")
        self.assertEqual(media.attached_name, request.name)

    def test_distinct_duplicate_pending_submission_is_conflict(self):
        first = self._submit_individual()
        self.assertTrue(first.get("ok"), first)
        duplicate = self._submit_individual(idempotency_key="different-key")
        self.assertFalse(duplicate.get("ok"), duplicate)
        self.assertEqual(duplicate.get("error"), "VERIFICATION_IN_PROGRESS")
        self.assertEqual(
            frappe.db.count("AOS Verification Request", {"user": self.owner}),
            1,
        )

    def test_cross_user_private_media_is_rejected_without_idor_detail(self):
        other_media = self.make_media(
            owner=self.other,
            purpose="verification_document",
            content_type="application/pdf",
            filename="other.pdf",
            visibility="Private",
        )
        frappe.set_user(self.owner)
        response = self._submit_individual(other_media.name)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "VERIFICATION_INVALID_DOCUMENT")
        self.assertNotIn(self.other, str(response))
        self.assertFalse(frappe.db.exists("AOS Verification Request", {"user": self.owner}))

    def test_owner_status_response_masks_pii_and_never_exposes_reviewer_identity(self):
        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        with self._without_get_limit():
            response = get_my_verification_impl()
        self.assertTrue(response.get("ok"), response)
        data = response.get("data", {})
        self.assertRegex(data.get("verification", {}).get("account_id") or "", r"^ACC-")
        self.assertNotIn("verified_by", data)
        self.assertNotIn(self.owner, str(data.get("verification", {})))
        document = data["verification"]["documents"][0]
        self.assertNotEqual(document.get("document_number"), "ID-123456789")
        self.assertTrue(str(document.get("document_number") or "").endswith("6789"))
        self.assertNotIn("media_object", document)
        self.assertNotIn("url", document)

    def test_normal_account_cannot_self_approve_even_with_ignore_permissions(self):
        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        request = frappe.get_doc("AOS Verification Request", submitted["data"]["id"])
        request.status = "Approved"
        with self.assertRaises(frappe.ValidationError):
            request.save(ignore_permissions=True)
        request.reload()
        self.assertEqual(request.status, "Pending")
        self.assertFalse(int(frappe.db.get_value("AOS Profile", {"user": self.owner}, "is_verified") or 0))

    def test_approval_projects_profile_and_notification_without_private_review_data(self):
        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        approved = self._review("Approved")
        self.assertEqual(approved.status, "Approved")
        self.assertTrue(approved.verified_by)
        self.assertTrue(approved.verified_on)
        self.assertTrue(int(frappe.db.get_value("AOS Profile", {"user": self.owner}, "is_verified") or 0))

        notification = frappe.db.get_value(
            "AOS Notification",
            {"user": self.owner, "type": "verification_approved"},
            ["payload", "dedupe_key"],
            order_by="creation desc",
            as_dict=True,
        )
        self.assertTrue(notification)
        payload = json.loads(notification.payload) if isinstance(notification.payload, str) else notification.payload
        self.assertEqual(payload.get("verification_id"), approved.name)
        self.assertRegex(payload.get("account_id") or "", r"^ACC-")
        self.assertNotIn("rejection_reason", payload)
        self.assertNotIn("verified_by", payload)
        self.assertIn(approved.name, notification.dedupe_key)

    def test_reject_resubmit_releases_old_evidence_and_allows_new_decision_notification(self):
        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        rejected = self._review("Rejected", reason="Image was not readable.")
        first_dedupe = frappe.db.get_value(
            "AOS Notification",
            {"user": self.owner, "type": "verification_rejected"},
            "dedupe_key",
            order_by="creation desc",
        )

        replacement = self.make_media(
            owner=self.owner,
            purpose="verification_document",
            content_type="application/pdf",
            filename="replacement.pdf",
            visibility="Private",
        )
        frappe.set_user(self.owner)
        resubmitted = self._submit_individual(
            replacement.name,
            idempotency_key="verification-test-key-2",
            verification_documents=[
                {
                    "document_type": "Identity Document",
                    "document_number": "ID-987654321",
                    "media_id": replacement.name,
                }
            ],
        )
        self.assertTrue(resubmitted.get("ok"), resubmitted)
        self.assertEqual(resubmitted["data"]["id"], rejected.name)
        self.assertEqual(resubmitted["data"]["status"], "Pending")

        old_media = frappe.get_doc("AOS Media Object", self.media.name)
        self.assertEqual(old_media.status, "Orphaned")
        with self.assertRaises(MediaPermissionError):
            MediaService().assert_user_can_read(old_media, self.owner)
        new_media = frappe.get_doc("AOS Media Object", replacement.name)
        self.assertEqual(new_media.status, "Attached")

        rejected_again = self._review("Rejected", reason="Replacement is still unreadable.")
        second_dedupe = frappe.db.get_value(
            "AOS Notification",
            {"user": self.owner, "type": "verification_rejected"},
            "dedupe_key",
            order_by="creation desc",
        )
        self.assertNotEqual(first_dedupe, second_dedupe)
        self.assertEqual(
            frappe.db.count("AOS Notification", {"user": self.owner, "type": "verification_rejected"}),
            2,
        )
        self.assertEqual(rejected_again.status, "Rejected")

    def test_reviewer_cannot_rewrite_submitted_identity_or_evidence_in_place(self):
        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        request = frappe.get_doc("AOS Verification Request", submitted["data"]["id"])
        request.legal_name = "Different Person"
        with self.assertRaises(frappe.ValidationError):
            request.save(ignore_permissions=True)
        request.reload()
        self.assertEqual(request.legal_name, "Jane Doe")

    def test_account_deletion_evidence_cleanup_releases_media_and_retains_request_record(self):
        from aos.services.account_deletion_service import (
            _cleanup_verification_documents,
            _revoke_verification_requests,
        )
        from frappe.utils import now_datetime

        submitted = self._submit_individual()
        self.assertTrue(submitted.get("ok"), submitted)
        request_id = submitted["data"]["id"]
        frappe.set_user("Administrator")
        summary = _cleanup_verification_documents(user=self.owner)
        revoked = _revoke_verification_requests(user=self.owner, now=now_datetime())

        self.assertEqual(summary.get("verification_documents_released"), 1)
        self.assertEqual(summary.get("verification_document_rows_removed"), 1)
        self.assertEqual(revoked, 1)
        self.assertTrue(frappe.db.exists("AOS Verification Request", request_id))
        request = frappe.get_doc("AOS Verification Request", request_id)
        self.assertEqual(request.status, "Revoked")
        self.assertEqual(len(request.verification_documents), 0)
        media = frappe.get_doc("AOS Media Object", self.media.name)
        self.assertEqual(media.status, "Orphaned")
        self.assertFalse(media.attached_name)

    def test_suspended_account_cannot_submit(self):
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Profile", {"user": self.owner}, "account_status", "Suspended")
        frappe.set_user(self.owner)
        response = self._submit_individual()
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "ACCOUNT_SUSPENDED")
        self.assertFalse(frappe.db.exists("AOS Verification Request", {"user": self.owner}))

    def test_business_approval_reuses_seller_projection_without_activating_seller(self):
        frappe.set_user("Administrator")
        seller = self.make_seller(self.owner)
        frappe.set_user(self.owner)
        with self._without_submit_limit():
            submitted = submit_verification_impl(
                verification_type="Business",
                business_name="AOS Shop",
                business_type="Limited Company",
                business_category="Retail",
                business_phone_number="+254700000001",
                business_email="SHOP@example.com",
                business_website="https://example.com",
                business_address="Nairobi",
                idempotency_key="business-verification-key",
                verification_documents=[
                    {"document_type": "Registration", "media_id": self.media.name}
                ],
            )
        self.assertTrue(submitted.get("ok"), submitted)
        self._review("Approved")
        seller.reload()
        self.assertEqual(seller.status, "Active")
        self.assertEqual(seller.seller_type, "Business")
        self.assertEqual(seller.business_category, "Retail")


if __name__ == "__main__":
    import unittest

    unittest.main()
