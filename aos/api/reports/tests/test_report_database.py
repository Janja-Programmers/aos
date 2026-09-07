from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.reports.reasons import list_report_reasons_impl
from aos.api.reports.report_ad import report_ad_impl
from aos.api.reports.report_short import report_short_impl
from aos.api.reports.report_user import report_user_impl
from aos.api.reviews.report import report_review_impl
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.account_deletion_service import _cleanup_report_account_data
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestReportDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """DB-backed Report behavior on a migrated Frappe test site."""

    def setUp(self):
        self.prefix = self.make_prefix("reports")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.reporter = self.make_user("reporter")
        self.target = self.make_user("target")
        self.other = self.make_user("other")
        self.reporter_account_id = ensure_public_account_id(self.reporter)
        self.target_account_id = ensure_public_account_id(self.target)
        self.other_account_id = ensure_public_account_id(self.other)
        self.seller_owner = self.make_user("seller")
        self.short_owner = self.make_user("short-owner")
        self.review_author = self.make_user("review-author")
        self.reason = self.make_report_reason()
        self.ad = self.make_ad(seller_user=self.seller_owner)
        self.short = self.make_short(owner=self.short_owner)
        # The shared Short fixture creates the row while Administrator is active;
        # set the canonical creator explicitly so ownership assertions exercise
        # the Report policy rather than the fixture session.
        frappe.db.set_value("AOS Short", self.short.name, "owner", self.short_owner, update_modified=False)
        self.short.reload()
        self.review = self._make_approved_review()
        frappe.set_user(self.reporter)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_report_domain_indexes_exist_after_migrate(self):
        from aos.patches.v1_0.install_report_indexes import INDEX_DEFINITIONS

        for doctype, index_name, _columns, _unique in INDEX_DEFINITIONS:
            rows = frappe.db.sql(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                (f"tab{doctype}", index_name),
            )
            self.assertTrue(rows, f"missing Report domain index {index_name}")

    def _make_approved_review(self):
        frappe.set_user("Administrator")
        review = frappe.get_doc(
            {
                "doctype": "AOS Review",
                "ad": self.ad.name,
                "reviewer": self.review_author,
                "rating": 5,
                "title": "Useful review",
                "comment": "Useful report feature test review.",
                "status": "Approved",
                "eligibility_basis": "communication",
                "moderation_generation": 1,
            }
        )
        review.insert(ignore_permissions=True)
        return review

    def _report_user(self, **overrides):
        payload = {"target_user": self.target_account_id, "reason": self.reason, "details": "Threatening messages"}
        payload.update(overrides)
        with (
            patch("aos.api.reports.report_user.rate_limit", return_value=None),
            patch("aos.api.reports.report_user.record_report_user_activity", return_value=None),
            patch("aos.api.reports.report_user.record_block_user_activity", return_value=None),
        ):
            return report_user_impl(**payload)

    def _report_ad(self, **overrides):
        payload = {"ad_id": self.ad.name, "reason": self.reason, "details": "Misleading listing"}
        payload.update(overrides)
        with (
            patch("aos.api.reports.report_ad.rate_limit", return_value=None),
            patch("aos.api.reports.report_ad.record_ad_report_activity", return_value=None),
        ):
            return report_ad_impl(**payload)

    def _report_short(self, **overrides):
        payload = {"short_id": self.short.name, "reason": self.reason, "details": "Unsafe content"}
        payload.update(overrides)
        with (
            patch("aos.api.reports.report_short.rate_limit", return_value=None),
            patch("aos.api.reports.report_short.record_short_report_activity", return_value=None),
        ):
            return report_short_impl(**payload)

    def _report_review(self, **overrides):
        payload = {"review_id": self.review.name, "reason": self.reason, "details": "Abusive review"}
        payload.update(overrides)
        with patch("aos.api.reviews.report.rate_limit", return_value=None):
            return report_review_impl(**payload)

    def test_report_reasons_require_login_reject_unknown_fields_and_return_active_rows(self):
        frappe.set_user("Guest")
        response = list_report_reasons_impl()
        self.assertFalse(response.get("ok"), response)

        frappe.set_user(self.reporter)
        with patch("aos.api.reports.reasons.rate_limit", return_value=None):
            invalid = list_report_reasons_impl(unexpected="x")
            valid = list_report_reasons_impl(cmd="aos.api.v1.reports.list_report_reasons")
        self.assertFalse(invalid.get("ok"), invalid)
        self.assertEqual(invalid.get("error"), "VALIDATION_ERROR")
        self.assertTrue(valid.get("ok"), valid)
        ids = {row.get("id") for row in valid.get("data", {}).get("reasons", [])}
        self.assertIn(self.reason, ids)

    def test_user_report_accepts_public_account_id_is_server_owned_and_duplicate_safe(self):
        frappe.set_user("Administrator")
        public_id = ensure_public_account_id(self.target)
        frappe.set_user(self.reporter)
        first = self._report_user(target_user=public_id)
        duplicate = self._report_user(target_user=public_id)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(duplicate.get("ok"), duplicate)
        report = frappe.get_doc("AOS User Report", first["data"]["id"])
        self.assertEqual(report.reported_by, self.reporter)
        self.assertEqual(report.reported_user, self.target)
        self.assertEqual(report.status, "Reviewing")
        self.assertTrue(report.active_key)
        self.assertEqual(
            frappe.db.count("AOS User Report", {"reported_by": self.reporter, "reported_user": self.target}),
            1,
        )

    def test_user_report_unknown_and_conflicting_aliases_are_rejected(self):
        unknown = self._report_user(extra_field="not allowed")
        conflict = self._report_user(user=self.other_account_id)
        self.assertFalse(unknown.get("ok"), unknown)
        self.assertEqual(unknown.get("error"), "VALIDATION_ERROR")
        self.assertFalse(conflict.get("ok"), conflict)
        self.assertEqual(conflict.get("error"), "VALIDATION_ERROR")
        self.assertFalse(frappe.db.exists("AOS User Report", {"reported_by": self.reporter}))

    def test_user_cannot_report_self_or_suspended_target(self):
        self_report = self._report_user(target_user=self.reporter_account_id)
        self.assertFalse(self_report.get("ok"), self_report)
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Profile", {"user": self.target}, "account_status", "Suspended")
        frappe.set_user(self.reporter)
        suspended = self._report_user()
        self.assertFalse(suspended.get("ok"), suspended)
        self.assertEqual(suspended.get("error"), "NOT_FOUND")

    def test_report_and_block_is_atomic_without_endpoint_commit(self):
        response = self._report_user(block_user=1)
        self.assertTrue(response.get("ok"), response)
        self.assertTrue(response.get("data", {}).get("block_applied"), response)
        self.assertTrue(
            frappe.db.exists(
                "AOS User Block",
                {"blocker_user": self.reporter, "blocked_user": self.target, "status": "Active"},
            )
        )

    def test_ad_report_enforces_target_visibility_ownership_and_duplicate_integrity(self):
        response = self._report_ad()
        duplicate = self._report_ad()
        self.assertTrue(response.get("ok"), response)
        self.assertFalse(duplicate.get("ok"), duplicate)
        self.assertEqual(duplicate.get("error"), "DUPLICATE")
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "total_reports") or 0), 1)

        frappe.set_user(self.seller_owner)
        own = self._report_ad()
        self.assertFalse(own.get("ok"), own)
        self.assertEqual(own.get("error"), "INVALID_AD_INPUT")

    def test_short_report_enforces_viewability_ownership_and_duplicate_integrity(self):
        response = self._report_short()
        duplicate = self._report_short()
        self.assertTrue(response.get("ok"), response)
        self.assertFalse(duplicate.get("ok"), duplicate)
        frappe.set_user(self.short_owner)
        own = self._report_short()
        self.assertFalse(own.get("ok"), own)

    def test_review_report_is_idempotent_and_rejects_alias_conflict(self):
        first = self._report_review()
        repeated = self._report_review()
        conflict = self._report_review(review=self.ad.name)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertEqual(first["data"]["id"], repeated["data"]["id"])
        self.assertTrue(repeated["data"].get("idempotent"))
        self.assertFalse(conflict.get("ok"), conflict)
        self.assertEqual(conflict.get("error"), "INVALID_REVIEW_REQUEST")

    def test_non_reviewer_cannot_resolve_report_even_with_ignore_permissions(self):
        submitted = self._report_user()
        self.assertTrue(submitted.get("ok"), submitted)
        doc = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Dismiss Report"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        doc.reload()
        self.assertEqual(doc.status, "Reviewing")

    def test_reviewer_cannot_rewrite_submitted_report_details(self):
        submitted = self._report_user()
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        doc.details = "Rewritten evidence"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_stale_desk_save_cannot_overwrite_newer_terminal_decision(self):
        submitted = self._report_user()
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        stale = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        frappe.db.set_value(
            "AOS User Report",
            stale.name,
            {"status": "Resolved", "admin_action": "Dismiss Report"},
            update_modified=False,
        )
        stale.status = "Rejected"
        stale.admin_action = ""
        with self.assertRaises(frappe.ValidationError):
            stale.save(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("AOS User Report", stale.name, "status"), "Resolved")

    def test_report_reason_title_is_immutable_but_reason_can_be_deactivated(self):
        frappe.set_user("Administrator")
        reason = frappe.get_doc("AOS Report Reason", self.reason)
        reason.title = f"{reason.title} changed"
        with self.assertRaises(frappe.ValidationError):
            reason.save(ignore_permissions=True)
        reason.reload()
        reason.is_active = 0
        reason.save(ignore_permissions=True)
        self.assertEqual(int(reason.is_active or 0), 0)

    def test_reason_may_be_deactivated_after_submission_without_blocking_resolution(self):
        submitted = self._report_user()
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        frappe.db.set_value("AOS Report Reason", self.reason, "is_active", 0)
        doc = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        doc.status = "Rejected"
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.status, "Rejected")
        self.assertTrue(doc.reviewed_by)
        self.assertTrue(doc.reviewed_on)

    def test_terminal_report_state_cannot_regress(self):
        submitted = self._report_user()
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Dismiss Report"
        doc.save(ignore_permissions=True)
        doc.reload()
        doc.status = "Reviewing"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_suspend_user_action_uses_account_state_and_revokes_access(self):
        submitted = self._report_user()
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS User Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Suspend User"
        with patch("aos.services.reports.moderation.revoke_account_access", return_value={}):
            doc.save(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("AOS Profile", {"user": self.target}, "account_status"), "Suspended")
        self.assertEqual(int(frappe.db.get_value("User", self.target, "enabled") or 0), 0)

    def test_hide_short_action_is_idempotent_and_updates_discovery_state(self):
        submitted = self._report_short()
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS Short Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Hide Short"
        with patch("aos.services.search_ranking_service.enqueue_short_search_index", return_value=None):
            doc.save(ignore_permissions=True)
        short = frappe.get_doc("AOS Short", self.short.name)
        self.assertEqual(short.visibility_status, "hidden")
        self.assertEqual(short.approval_status, "flagged")

    def test_suspend_ad_action_reuses_ad_lifecycle(self):
        submitted = self._report_ad()
        frappe.set_user("Administrator")
        doc = frappe.get_doc("AOS Ad Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Suspended Ad"
        with patch("aos.services.reports.moderation.enqueue_discovery_refresh", return_value=None):
            doc.save(ignore_permissions=True)
        self.ad.reload()
        self.assertEqual(self.ad.status, "Suspended")

    def test_suspend_seller_action_does_not_resurrect_deleted_seller(self):
        submitted = self._report_ad()
        self.assertTrue(submitted.get("ok"), submitted)
        frappe.set_user("Administrator")
        from aos.services.sellers.policy import set_seller_status

        set_seller_status(
            self.ad.seller,
            status="Deleted",
            reason_code="TEST_DELETED",
            source="report_test",
            actor="Administrator",
        )
        doc = frappe.get_doc("AOS Ad Report", submitted["data"]["id"])
        doc.status = "Resolved"
        doc.admin_action = "Suspended Seller"
        doc.save(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("AOS Seller", self.ad.seller, "status"), "Deleted")
        self.assertEqual(doc.status, "Resolved")

    def test_account_deletion_cleanup_removes_reporter_private_rows_but_retains_reports_about_account(self):
        user_report = self._report_user()
        ad_report = self._report_ad()
        short_report = self._report_short()
        self.assertTrue(user_report.get("ok") and ad_report.get("ok") and short_report.get("ok"))

        frappe.set_user(self.target)
        with (
            patch("aos.api.reports.report_user.rate_limit", return_value=None),
            patch("aos.api.reports.report_user.record_report_user_activity", return_value=None),
        ):
            about_deleted = report_user_impl(target_user=self.reporter_account_id, reason=self.reason)
        self.assertTrue(about_deleted.get("ok"), about_deleted)

        frappe.set_user("Administrator")
        summary = _cleanup_report_account_data(user=self.reporter)
        self.assertEqual(summary["user_reports_removed"], 1)
        self.assertEqual(summary["ad_reports_removed"], 1)
        self.assertEqual(summary["short_reports_removed"], 1)
        self.assertFalse(frappe.db.exists("AOS User Report", user_report["data"]["id"]))
        self.assertFalse(frappe.db.exists("AOS Ad Report", ad_report["data"]["id"]))
        self.assertFalse(frappe.db.exists("AOS Short Report", short_report["data"]["id"]))
        self.assertTrue(frappe.db.exists("AOS User Report", about_deleted["data"]["id"]))
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "total_reports") or 0), 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
