from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.moderation_service import _apply_review_decision
from aos.services.reviews.errors import ReviewConflictError, ReviewNotFoundError, ReviewPermissionError, ReviewValidationError
from aos.services.reviews.moderation import review_review
from aos.services.reviews.service import ReviewService
from aos.aos.doctype.aos_review_reaction.aos_review_reaction import review_reaction_name
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestReviewsDatabase(AOSFeatureTestMixin, FrappeTestCase):
    """DB-backed acceptance coverage for the canonical Reviews domain."""

    def setUp(self):
        self.prefix = self.make_prefix("reviews")
        self.created_users: list[str] = []
        self.created_ad_names: list[str] = []
        frappe.set_user("Administrator")
        self.seller_user = self.make_user("seller")
        self.author = self.make_user("author")
        self.viewer = self.make_user("viewer")
        self.other = self.make_user("other")
        self.moderator = self.make_system_user("moderator")
        self.ad = self.make_ad(seller_user=self.seller_user)
        self.make_conversation(self.author, self.seller_user, with_message=True)
        frappe.set_user(self.author)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _create(self, *, user: str | None = None, ad=None, **overrides):
        user = user or self.author
        ad = ad or self.ad
        if user != self.author:
            self.make_conversation(user, self.seller_user, with_message=True)
        frappe.set_user(user)
        payload = {
            "ad_id": ad.public_id,
            "rating": 5,
            "title": "Excellent seller",
            "comment": "The listing matched the conversation and the transaction went well.",
            "media": [],
        }
        payload.update(overrides)
        return ReviewService().create(
            user=user,
            payload=payload,
            moderation_enqueue=lambda *args, **kwargs: SimpleNamespace(name="test-job"),
        )

    def _doc(self, public_id: str):
        name = frappe.db.get_value("AOS Review", {"public_id": public_id}, "name")
        self.assertTrue(name)
        return frappe.get_doc("AOS Review", name)

    def _approve(self, public_id: str):
        doc = self._doc(public_id)
        frappe.set_user(self.moderator)
        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            review_review(
                review_id=public_id,
                decision="approve",
                reason="",
                version=str(doc.modified),
                reviewer=self.moderator,
            )
        return self._doc(public_id)

    def test_create_is_strict_unique_and_uses_public_ids(self):
        created = self._create()
        review = created["review"]
        self.assertTrue(review["id"].startswith("review_"))
        self.assertEqual(review["ad_id"], self.ad.public_id)
        self.assertEqual(review["status"], "Pending")
        self.assertNotEqual(review["id"], self._doc(review["id"]).name)

        with self.assertRaises(ReviewConflictError):
            self._create()
        with self.assertRaises(ReviewValidationError):
            self._create(owner=self.author)
        with self.assertRaises((ReviewNotFoundError, ReviewValidationError)):
            self._create(ad_id=self.ad.name)

    def test_update_requires_owner_version_and_requeues_approved_review(self):
        public_id = self._create()["review"]["id"]
        approved = self._approve(public_id)
        frappe.set_user(self.other)
        with self.assertRaises(ReviewPermissionError):
            ReviewService().update(
                user=self.other,
                payload={"review_id": public_id, "version": str(approved.modified), "rating": 4},
                moderation_enqueue=lambda *args, **kwargs: None,
            )

        frappe.set_user(self.author)
        changed = ReviewService().update(
            user=self.author,
            payload={"review_id": public_id, "version": str(approved.modified), "rating": 4},
            moderation_enqueue=lambda *args, **kwargs: SimpleNamespace(name="update-job"),
        )
        self.assertTrue(changed["changed"])
        self.assertEqual(changed["review"]["status"], "Pending")
        self.assertEqual(changed["review"]["rating"], 4)
        with self.assertRaises(ReviewConflictError):
            ReviewService().update(
                user=self.author,
                payload={"review_id": public_id, "version": str(approved.modified), "rating": 3},
                moderation_enqueue=lambda *args, **kwargs: None,
            )

    def test_rejected_review_owner_edit_resubmits_and_requeues(self):
        public_id = self._create()["review"]["id"]
        approved = self._approve(public_id)

        frappe.set_user(self.moderator)
        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            rejected = review_review(
                review_id=public_id,
                decision="reject",
                reason="Contains disallowed content.",
                version=str(approved.modified),
                reviewer=self.moderator,
            )
        self.assertEqual(rejected.status, "Rejected")
        prior_generation = int(rejected.moderation_generation or 0)

        queued: list[tuple[str, str]] = []
        frappe.set_user(self.author)
        result = ReviewService().update(
            user=self.author,
            payload={
                "review_id": public_id,
                "version": str(rejected.modified),
                "title": "Updated after moderation feedback",
            },
            moderation_enqueue=lambda name, source: queued.append((name, source)) or SimpleNamespace(name="resubmit-job"),
        )

        fresh = self._doc(public_id)
        self.assertTrue(result["changed"])
        self.assertEqual(fresh.status, "Pending")
        self.assertEqual(fresh.review_notes, "")
        self.assertEqual(int(fresh.moderation_generation or 0), prior_generation + 1)
        self.assertEqual(queued, [(fresh.name, "review_update")])

    def test_withdraw_is_idempotent_and_removes_public_aggregate(self):
        public_id = self._create()["review"]["id"]
        approved = self._approve(public_id)
        ad_metrics = frappe.db.get_value("AOS Ad", self.ad.name, ["review_rating_sum", "total_reviews", "average_rating"], as_dict=True)
        self.assertEqual((ad_metrics.review_rating_sum, ad_metrics.total_reviews, float(ad_metrics.average_rating)), (5, 1, 5.0))

        frappe.set_user(self.author)
        first = ReviewService().withdraw(user=self.author, payload={"review_id": public_id, "version": str(approved.modified)})
        second = ReviewService().withdraw(user=self.author, payload={"review_id": public_id, "version": str(approved.modified)})
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["status"], "Withdrawn")
        ad_metrics = frappe.db.get_value("AOS Ad", self.ad.name, ["review_rating_sum", "total_reviews", "average_rating"], as_dict=True)
        self.assertEqual((ad_metrics.review_rating_sum, ad_metrics.total_reviews, float(ad_metrics.average_rating)), (0, 0, 0.0))

    def test_media_uses_hardened_media_ownership_and_lifecycle(self):
        media = self.make_media(owner=self.author, purpose="review_image")
        foreign = self.make_media(owner=self.other, purpose="review_image")
        result = self._create(media=[media.name])
        public_id = result["review"]["id"]
        doc = self._doc(public_id)
        media.reload()
        self.assertEqual(media.status, "Attached")
        self.assertEqual(media.attached_doctype, "AOS Review")
        self.assertEqual(media.attached_name, doc.name)
        self.assertEqual([row.media for row in doc.review_images], [media.name])

        other_ad = self.make_ad(seller_user=self.seller_user)
        with self.assertRaises(Exception) as ctx:
            self._create(user=self.author, ad=other_ad, media=[foreign.name])
        self.assertEqual(type(ctx.exception).__name__, "MediaPermissionError")

        frappe.set_user(self.author)
        ReviewService().withdraw(user=self.author, payload={"review_id": public_id, "version": str(doc.modified)})
        media.reload()
        self.assertEqual(media.status, "Orphaned")
        self.assertFalse(media.attached_name)

    def test_withdraw_action_only_allows_server_owned_media_detachment(self):
        media = self.make_media(owner=self.author, purpose="review_image")
        public_id = self._create(media=[media.name])["review"]["id"]
        doc = self._doc(public_id)

        frappe.set_user(self.author)
        doc.flags.aos_review_action = "owner_withdraw"
        doc.status = "Withdrawn"
        doc.title = "Changed during withdrawal"
        doc.set("review_images", [])
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)

    def test_moderation_action_cannot_mutate_review_media_content(self):
        media = self.make_media(owner=self.author, purpose="review_image")
        public_id = self._create(media=[media.name])["review"]["id"]
        doc = self._doc(public_id)

        frappe.set_user("Guest")
        doc.flags.aos_review_action = "moderation_allow"
        doc.status = "Approved"
        doc.set("review_images", [])
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)

    def test_automated_moderation_with_review_image_does_not_require_owner_edit(self):
        media = self.make_media(owner=self.author, purpose="review_image")
        public_id = self._create(media=[media.name])["review"]["id"]
        doc = self._doc(public_id)
        self.assertEqual([row.media for row in doc.review_images], [media.name])
        self.assertEqual(doc.status, "Pending")

        job = SimpleNamespace(
            target_name=doc.name,
            context_json=json.dumps({"moderation_generation": int(doc.moderation_generation or 1)}),
        )
        frappe.set_user("Guest")
        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            _apply_review_decision(job, "review", ["vision uncertainty: weapons meaningfully outranked safe"])

        reviewed = self._doc(public_id)
        self.assertEqual(reviewed.status, "Pending")
        self.assertEqual(reviewed.review_notes, "vision uncertainty: weapons meaningfully outranked safe")
        self.assertEqual([row.media for row in reviewed.review_images], [media.name])

        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            _apply_review_decision(job, "allow", [])
        approved = self._doc(public_id)
        self.assertEqual(approved.status, "Approved")
        self.assertEqual([row.media for row in approved.review_images], [media.name])

    def test_manual_and_automated_moderation_are_generation_safe(self):
        public_id = self._create()["review"]["id"]
        doc = self._doc(public_id)
        self.assertEqual(doc.status, "Pending")

        approved = self._approve(public_id)
        self.assertEqual(approved.status, "Approved")
        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            rejected = review_review(
                review_id=public_id,
                decision="reject",
                reason="Contains disallowed content.",
                version=str(approved.modified),
                reviewer=self.moderator,
            )
        self.assertEqual(rejected.status, "Rejected")
        self.assertEqual(rejected.review_notes, "Contains disallowed content.")

        # A stale automated job must never overwrite the later human decision.
        job = SimpleNamespace(
            target_name=rejected.name,
            context_json=json.dumps({"moderation_generation": int(rejected.moderation_generation or 1)}),
        )
        with patch("aos.services.reviews.moderation.notify_review_decision", return_value=None):
            _apply_review_decision(job, "allow", [])
        self.assertEqual(self._doc(public_id).status, "Rejected")

    def test_reactions_are_explicit_idempotent_and_switch_atomically(self):
        public_id = self._create()["review"]["id"]
        self._approve(public_id)
        frappe.set_user(self.viewer)
        service = ReviewService()

        first = service.like(user=self.viewer, payload={"review_id": public_id})
        again = service.like(user=self.viewer, payload={"review_id": public_id})
        switched = service.dislike(user=self.viewer, payload={"review_id": public_id})
        wrong_remove = service.unlike(user=self.viewer, payload={"review_id": public_id})
        removed = service.undislike(user=self.viewer, payload={"review_id": public_id})
        removed_again = service.undislike(user=self.viewer, payload={"review_id": public_id})

        self.assertEqual((first["reaction"], first["changed"]), ("Like", True))
        self.assertEqual((again["reaction"], again["changed"]), ("Like", False))
        self.assertEqual((switched["reaction"], switched["changed"]), ("Dislike", True))
        self.assertEqual((wrong_remove["reaction"], wrong_remove["changed"]), ("Dislike", False))
        self.assertEqual((removed["reaction"], removed["changed"]), (None, True))
        self.assertEqual((removed_again["reaction"], removed_again["changed"]), (None, False))
        doc = self._doc(public_id)
        self.assertEqual((doc.like_count, doc.dislike_count), (0, 0))
        self.assertEqual(frappe.db.count("AOS Review Reaction", {"review": doc.name, "user": self.viewer}), 0)

    def test_reaction_relationship_derives_user_from_session(self):
        public_id = self._create()["review"]["id"]
        review = self._approve(public_id)
        frappe.set_user(self.viewer)
        reaction = frappe.get_doc(
            {
                "doctype": "AOS Review Reaction",
                "review": review.name,
                "user": self.other,
                "reaction": "Like",
            }
        )
        reaction.insert(ignore_permissions=True)
        self.assertEqual(reaction.user, self.viewer)
        self.assertEqual(reaction.name, review_reaction_name(review=review.name, user=self.viewer))
        self.assertFalse(
            frappe.db.exists(
                "AOS Review Reaction",
                {"review": review.name, "user": self.other},
            )
        )

    def test_public_listing_is_keyset_bound_and_hides_non_public_reviews(self):
        first_id = self._create()["review"]["id"]
        self._approve(first_id)
        second_author = self.make_user("author-two")
        second = self._create(user=second_author, rating=3)
        self._approve(second["review"]["id"])
        third_author = self.make_user("author-three")
        pending_id = self._create(user=third_author, rating=1)["review"]["id"]

        frappe.set_user(self.viewer)
        service = ReviewService()
        page1 = service.list_public(payload={"ad_id": self.ad.public_id, "sort": "newest", "limit": 1}, viewer=self.viewer)
        self.assertEqual(len(page1["reviews"]), 1)
        self.assertTrue(page1["pagination"]["has_more"])
        cursor = page1["pagination"]["next_cursor"]
        page2 = service.list_public(payload={"ad_id": self.ad.public_id, "sort": "newest", "limit": 1, "cursor": cursor}, viewer=self.viewer)
        ids = {page1["reviews"][0]["id"], page2["reviews"][0]["id"]}
        self.assertEqual(ids, {first_id, second["review"]["id"]})
        self.assertNotIn(pending_id, ids)
        with self.assertRaises(ReviewValidationError):
            service.list_public(payload={"ad_id": self.ad.public_id, "sort": "rating_high", "limit": 1, "cursor": cursor}, viewer=self.viewer)
        with self.assertRaises(ReviewValidationError):
            service.list_public(payload={"ad_id": self.ad.public_id, "cursor": "malformed"}, viewer=self.viewer)

        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Reviewing", update_modified=False)
        with self.assertRaises(ReviewNotFoundError):
            service.list_public(payload={"ad_id": self.ad.public_id}, viewer=self.viewer)

    def test_review_indexes_exist_after_migrate(self):
        from aos.patches.v1_0.install_review_indexes import INDEX_DEFINITIONS

        for doctype, index_name, _columns, _unique in INDEX_DEFINITIONS:
            rows = frappe.db.sql(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                (f"tab{doctype}", index_name),
            )
            self.assertTrue(rows, f"missing Reviews index {index_name}")
