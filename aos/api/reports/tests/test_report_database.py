from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.reports.reasons import get_report_reasons_impl
from aos.api.reports.report_ad import report_ad_impl
from aos.api.reports.report_short import report_short_impl
from aos.api.reports.report_user import report_user_impl
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.account_deletion_service import _cleanup_report_account_data
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestReportDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """DB-backed contracts for the final User/Ad/Short Reports subsystem."""

    def setUp(self):
        self.prefix = self.make_prefix("reports")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.reporter = self.make_user("reporter")
        self.target = self.make_user("target")
        self.other = self.make_user("other")
        self.seller_owner = self.make_user("seller")
        self.short_owner = self.make_user("short-owner")
        self.shared_reason = self.make_report_reason(
            targets=("User", "Ad", "Short"), key_suffix="shared"
        )
        self.user_reason = self.make_report_reason(targets=("User",), key_suffix="user_only")
        self.ad_reason = self.make_report_reason(targets=("Ad",), key_suffix="ad_only")
        self.short_reason = self.make_report_reason(targets=("Short",), key_suffix="short_only")
        self.disabled_reason = self.make_report_reason(
            targets=("User", "Ad", "Short"), enabled=False, key_suffix="disabled"
        )
        self.ad = self.make_ad(seller_user=self.seller_owner)
        self.short = self.make_short(owner=self.short_owner)
        # The shared Short fixture can be created while Administrator is active;
        # pin ownership explicitly for Report self/visibility tests.
        frappe.db.set_value("AOS Short", self.short.name, "owner", self.short_owner, update_modified=False)
        self.short.reload()
        frappe.set_user(self.reporter)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _report_user(self, **overrides):
        payload = {
            "account_id": ensure_public_account_id(self.target),
            "reason_id": self.shared_reason,
            "details": "Threatening messages",
        }
        payload.update(overrides)
        with patch("aos.api.reports.report_user.limit_report_submission", return_value=None):
            return report_user_impl(**payload)

    def _report_ad(self, **overrides):
        payload = {
            "ad_id": self.ad.public_id,
            "reason_id": self.shared_reason,
            "details": "Misleading listing",
        }
        payload.update(overrides)
        with patch("aos.api.reports.report_ad.limit_report_submission", return_value=None):
            return report_ad_impl(**payload)

    def _report_short(self, **overrides):
        payload = {
            "short_id": self.short.name,
            "reason_id": self.shared_reason,
            "details": "Unsafe content",
        }
        payload.update(overrides)
        with patch("aos.api.reports.report_short.limit_report_submission", return_value=None):
            return report_short_impl(**payload)

    def _reasons(self, target_type: str, **overrides):
        payload = {"target_type": target_type}
        payload.update(overrides)
        with patch("aos.api.reports.reasons.rate_limit", return_value=None):
            return get_report_reasons_impl(**payload)

    def test_report_domain_indexes_exist_after_migrate(self):
        from aos.patches.v1_0.install_report_indexes import INDEX_DEFINITIONS

        for doctype, index_name, _columns, _unique in INDEX_DEFINITIONS:
            rows = frappe.db.sql(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                (f"tab{doctype}", index_name),
            )
            self.assertTrue(rows, f"missing Report domain index {index_name}")

    def test_reason_listing_requires_auth_rejects_unknown_target_and_is_target_scoped(self):
        frappe.set_user("Guest")
        unauthorized = self._reasons("user")
        self.assertFalse(unauthorized.get("ok"), unauthorized)
        self.assertEqual(unauthorized.get("error"), "AUTH_REQUIRED")

        frappe.set_user(self.reporter)
        invalid = self._reasons("seller")
        unknown = self._reasons("user", extra="x")
        self.assertFalse(invalid.get("ok"), invalid)
        self.assertEqual(invalid.get("error"), "REPORT_INVALID_REQUEST")
        self.assertFalse(unknown.get("ok"), unknown)
        self.assertEqual(unknown.get("error"), "REPORT_INVALID_REQUEST")

        user = self._reasons("user")
        ad = self._reasons("ad")
        short = self._reasons("short")
        self.assertTrue(user.get("ok") and ad.get("ok") and short.get("ok"))
        user_ids = [row["id"] for row in user["data"]["reasons"]]
        ad_ids = [row["id"] for row in ad["data"]["reasons"]]
        short_ids = [row["id"] for row in short["data"]["reasons"]]
        self.assertIn(self.user_reason, user_ids)
        self.assertNotIn(self.user_reason, ad_ids)
        self.assertNotIn(self.user_reason, short_ids)
        self.assertIn(self.ad_reason, ad_ids)
        self.assertIn(self.short_reason, short_ids)
        self.assertIn(self.shared_reason, user_ids)
        self.assertIn(self.shared_reason, ad_ids)
        self.assertIn(self.shared_reason, short_ids)
        self.assertNotIn(self.disabled_reason, user_ids + ad_ids + short_ids)
        # Query ordering includes name as the final deterministic tie breaker.
        ordering = {}
        for reason_id in user_ids:
            row = frappe.db.get_value(
                "AOS Report Reason", reason_id, ["sort_order", "label"], as_dict=True
            )
            ordering[reason_id] = (int(row.sort_order or 0), str(row.label), reason_id)
        self.assertEqual(user_ids, sorted(user_ids, key=ordering.__getitem__))

    def test_user_report_uses_acc_identity_session_reporter_and_idempotent_active_duplicate(self):
        first = self._report_user(reason_id=self.user_reason)
        repeated = self._report_user(reason_id=self.shared_reason, details="different retry payload")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(first["data"]["report_id"], repeated["data"]["report_id"])
        self.assertFalse(first["data"]["idempotent_replay"])
        self.assertTrue(repeated["data"]["idempotent_replay"])
        self.assertEqual(first["data"]["target_id"], ensure_public_account_id(self.target))
        self.assertTrue(first["data"]["report_id"].startswith("URPT-"))
        report = frappe.get_doc("AOS User Report", first["data"]["report_id"])
        self.assertEqual(report.reported_by, self.reporter)
        self.assertEqual(report.reported_user, self.target)
        self.assertEqual(report.reason, self.user_reason)
        self.assertEqual(report.status, "Reviewing")
        self.assertTrue(report.active_key)
        self.assertEqual(
            frappe.db.count("AOS User Report", {"reported_by": self.reporter, "reported_user": self.target}),
            1,
        )

    def test_user_report_rejects_self_nonexistent_malformed_and_forged_fields(self):
        self_report = self._report_user(account_id=ensure_public_account_id(self.reporter))
        missing = self._report_user(account_id="ACC-AAAAAAAAAAAAAAAAAAAA")
        malformed = self._report_user(account_id=self.target)
        forged_reporter = self._report_user(reported_by=self.other)
        forged_status = self._report_user(status="Resolved")
        for response, code in (
            (self_report, "REPORT_SELF_NOT_ALLOWED"),
            (missing, "REPORT_INVALID_TARGET"),
            (malformed, "REPORT_INVALID_TARGET"),
            (forged_reporter, "REPORT_INVALID_REQUEST"),
            (forged_status, "REPORT_INVALID_REQUEST"),
        ):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), code)

    def test_user_report_does_not_require_or_create_a_social_block(self):
        # Existing blocks do not prevent a user from submitting a complaint.
        frappe.set_user("Administrator")
        block = frappe.get_doc(
            {
                "doctype": "AOS User Block",
                "blocker_user": self.reporter,
                "blocked_user": self.target,
                "status": "Active",
                "reason": "test",
            }
        )
        block.insert(ignore_permissions=True)
        frappe.set_user(self.reporter)
        response = self._report_user(reason_id=self.user_reason)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(
            frappe.db.count(
                "AOS User Block",
                {"blocker_user": self.reporter, "blocked_user": self.target, "status": "Active"},
            ),
            1,
        )

    def test_reason_target_mismatch_and_disabled_reason_are_rejected_for_all_targets(self):
        user_mismatch = self._report_user(reason_id=self.ad_reason)
        ad_mismatch = self._report_ad(reason_id=self.user_reason)
        short_mismatch = self._report_short(reason_id=self.user_reason)
        disabled = self._report_user(reason_id=self.disabled_reason)
        for response in (user_mismatch, ad_mismatch, short_mismatch):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "REPORT_REASON_NOT_ALLOWED")
        self.assertFalse(disabled.get("ok"), disabled)
        self.assertEqual(disabled.get("error"), "REPORT_INVALID_REASON")

    def test_ad_report_uses_public_ad_id_visibility_and_idempotent_duplicate(self):
        first = self._report_ad(reason_id=self.ad_reason)
        repeated = self._report_ad(reason_id=self.shared_reason)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(first["data"]["report_id"], repeated["data"]["report_id"])
        self.assertTrue(first["data"]["report_id"].startswith("ARPT-"))
        self.assertEqual(first["data"]["target_id"], self.ad.public_id)
        report = frappe.get_doc("AOS Ad Report", first["data"]["report_id"])
        self.assertEqual(report.ad, self.ad.name)
        self.assertEqual(report.reported_by, self.reporter)
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "total_reports") or 0), 1)

        frappe.set_user(self.seller_owner)
        own = self._report_ad(reason_id=self.ad_reason)
        self.assertFalse(own.get("ok"), own)
        self.assertEqual(own.get("error"), "REPORT_SELF_NOT_ALLOWED")

    def test_unavailable_or_internal_ad_identity_is_not_reportable(self):
        internal = self._report_ad(ad_id=self.ad.name, reason_id=self.ad_reason)
        self.assertFalse(internal.get("ok"), internal)
        self.assertEqual(internal.get("error"), "REPORT_INVALID_TARGET")
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Suspended", update_modified=False)
        frappe.set_user(self.reporter)
        unavailable = self._report_ad(reason_id=self.ad_reason)
        self.assertFalse(unavailable.get("ok"), unavailable)
        self.assertEqual(unavailable.get("error"), "REPORT_INVALID_TARGET")

    def test_short_report_uses_canonical_id_visibility_and_idempotent_duplicate(self):
        first = self._report_short(reason_id=self.short_reason)
        repeated = self._report_short(reason_id=self.shared_reason)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(first["data"]["report_id"], repeated["data"]["report_id"])
        self.assertTrue(first["data"]["report_id"].startswith("SRPT-"))
        self.assertEqual(first["data"]["target_id"], self.short.name)
        frappe.set_user(self.short_owner)
        own = self._report_short(reason_id=self.short_reason)
        self.assertFalse(own.get("ok"), own)
        self.assertEqual(own.get("error"), "REPORT_SELF_NOT_ALLOWED")

    def test_hidden_and_nonexistent_short_are_not_reportable(self):
        missing = self._report_short(short_id="SHR-00000000000000000000000000000000")
        self.assertFalse(missing.get("ok"), missing)
        self.assertEqual(missing.get("error"), "REPORT_INVALID_TARGET")
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Short", self.short.name, "lifecycle_status", "Hidden", update_modified=False)
        frappe.set_user(self.reporter)
        hidden = self._report_short(reason_id=self.short_reason)
        self.assertFalse(hidden.get("ok"), hidden)
        self.assertEqual(hidden.get("error"), "REPORT_INVALID_TARGET")

    def test_details_are_optional_bounded_and_html_is_rejected(self):
        html = self._report_user(details="<script>alert(1)</script>")
        too_long = self._report_user(details="x" * 1001)
        for response in (html, too_long):
            self.assertFalse(response.get("ok"), response)
            self.assertEqual(response.get("error"), "REPORT_INVALID_REQUEST")
        ok_response = self._report_user(details="")
        self.assertTrue(ok_response.get("ok"), ok_response)

    def test_staff_lifecycle_is_reviewing_to_terminal_and_reporter_cannot_transition(self):
        submitted = self._report_user()
        self.assertTrue(submitted.get("ok"), submitted)
        report_id = submitted["data"]["report_id"]
        doc = frappe.get_doc("AOS User Report", report_id)
        doc.status = "Resolved"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS User Report", report_id)
        doc.status = "Resolved"
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.status, "Resolved")
        self.assertEqual(doc.reviewed_by, "Administrator")
        self.assertTrue(doc.reviewed_on)
        self.assertFalse(doc.active_key)
        doc.reload()
        doc.status = "Reviewing"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_terminal_report_allows_later_new_report_while_reviewing_uniqueness_remains(self):
        first = self._report_ad(reason_id=self.ad_reason)
        self.assertTrue(first.get("ok"), first)
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS Ad Report", first["data"]["report_id"])
        doc.status = "Rejected"
        doc.save(ignore_permissions=True)
        self.assertFalse(doc.active_key)

        frappe.set_user(self.reporter)
        second = self._report_ad(reason_id=self.shared_reason)
        self.assertTrue(second.get("ok"), second)
        self.assertNotEqual(first["data"]["report_id"], second["data"]["report_id"])
        self.assertFalse(second["data"]["idempotent_replay"])

    def test_deactivated_reason_does_not_block_resolution_of_existing_report(self):
        submitted = self._report_user(reason_id=self.user_reason)
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Report Reason", self.user_reason, "is_enabled", 0, update_modified=False)
        doc = frappe.get_doc("AOS User Report", submitted["data"]["report_id"])
        doc.status = "Rejected"
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.status, "Rejected")

    def test_target_becoming_unavailable_does_not_destroy_existing_report_evidence(self):
        submitted = self._report_short(reason_id=self.short_reason)
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Short", self.short.name, "lifecycle_status", "Hidden", update_modified=False)
        doc = frappe.get_doc("AOS Short Report", submitted["data"]["report_id"])
        doc.status = "Resolved"
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.status, "Resolved")

    def test_reason_identity_is_immutable_but_label_and_enabled_state_are_operable(self):
        frappe.set_user("Administrator")
        reason = frappe.get_doc("AOS Report Reason", self.user_reason)
        original_id = reason.reason_id
        reason.label = f"{reason.label} updated"
        reason.is_enabled = 0
        reason.save(ignore_permissions=True)
        self.assertEqual(reason.reason_id, original_id)
        self.assertEqual(int(reason.is_enabled or 0), 0)
        reason.reason_id = f"{original_id}_changed"
        with self.assertRaises(frappe.ValidationError):
            reason.save(ignore_permissions=True)

    def test_account_deletion_cleanup_removes_reporter_private_rows_but_retains_reports_about_account(self):
        user_report = self._report_user()
        ad_report = self._report_ad()
        short_report = self._report_short()
        self.assertTrue(user_report.get("ok") and ad_report.get("ok") and short_report.get("ok"))

        frappe.set_user(self.target)
        with patch("aos.api.reports.report_user.limit_report_submission", return_value=None):
            about_deleted = report_user_impl(
                account_id=ensure_public_account_id(self.reporter),
                reason_id=self.shared_reason,
            )
        self.assertTrue(about_deleted.get("ok"), about_deleted)

        frappe.set_user("Administrator")
        summary = _cleanup_report_account_data(user=self.reporter)
        self.assertEqual(summary["user_reports_removed"], 1)
        self.assertEqual(summary["ad_reports_removed"], 1)
        self.assertEqual(summary["short_reports_removed"], 1)
        self.assertFalse(frappe.db.exists("AOS User Report", user_report["data"]["report_id"]))
        self.assertFalse(frappe.db.exists("AOS Ad Report", ad_report["data"]["report_id"]))
        self.assertFalse(frappe.db.exists("AOS Short Report", short_report["data"]["report_id"]))
        self.assertTrue(frappe.db.exists("AOS User Report", about_deleted["data"]["report_id"]))


if __name__ == "__main__":
    import unittest

    unittest.main()
