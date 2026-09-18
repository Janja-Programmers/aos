from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase

from aos.services.reviews.errors import ReviewConflictError
from aos.services.reviews.service import ReviewService


class ReviewsConcurrencyContracts(IntegrationTestCase):
    """Race-path contracts exercised with the same duplicate/lock branches used in production."""

    def test_duplicate_review_insert_race_returns_database_winner(self):
        service = ReviewService()
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")
        winner = SimpleNamespace(public_id="review_winner", status="Pending")

        with (
            patch(
                "aos.services.reviews.service.enforce_review_eligibility",
                return_value={
                    "_ad_name": "AD-INTERNAL",
                    "eligibility_basis": "communication",
                    "_eligibility_reference": "CONVERSATION-1",
                },
            ),
            patch.object(service, "_validate_media", return_value=[]),
            patch("aos.services.reviews.service.frappe.new_doc", return_value=candidate),
            patch("aos.services.reviews.service.is_duplicate_entry_error", return_value=True),
            patch("aos.services.reviews.service.frappe.db.get_value", return_value=winner),
        ):
            with self.assertRaises(ReviewConflictError) as raised:
                service.create(
                    user="buyer@example.test",
                    payload={
                        "ad_id": "ad_public",
                        "rating": 5,
                        "title": "Excellent seller",
                        "comment": "Everything matched the listing and conversation.",
                        "media": [],
                    },
                    moderation_enqueue=lambda *_args, **_kwargs: None,
                )

        self.assertEqual(raised.exception.code, "REVIEW_ALREADY_EXISTS")
        self.assertEqual(raised.exception.data["review_id"], "review_winner")
        candidate.insert.assert_called_once_with(ignore_permissions=True)

    def test_concurrent_same_reaction_insert_converges_to_database_winner(self):
        service = ReviewService()
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")
        public_review = SimpleNamespace(
            name="REVIEW-INTERNAL",
            public_id="review_public",
            reviewer="author@example.test",
        )

        with (
            patch.object(service, "_public_review_row", return_value=public_review),
            patch("aos.services.reviews.service.is_blocked_between", return_value=False),
            patch(
                "aos.services.reviews.service.frappe.db.sql",
                side_effect=[[], [SimpleNamespace(name="RREACT-WINNER", reaction="Like")]],
            ),
            patch("aos.services.reviews.service.frappe.new_doc", return_value=candidate),
            patch("aos.services.reviews.service.is_duplicate_entry_error", return_value=True),
            patch(
                "aos.services.reviews.service.frappe.db.get_value",
                return_value=SimpleNamespace(like_count=1, dislike_count=0),
            ),
        ):
            result = service.like(user="viewer@example.test", payload={"review_id": "review_public"})

        self.assertEqual((result["reaction"], result["changed"]), ("Like", False))
        self.assertEqual((result["like_count"], result["dislike_count"]), (1, 0))
        candidate.insert.assert_called_once_with(ignore_permissions=True)

    def test_concurrent_opposing_reaction_insert_switches_database_winner(self):
        service = ReviewService()
        candidate = MagicMock()
        candidate.insert.side_effect = RuntimeError("simulated duplicate race")
        winner = MagicMock()
        winner.reaction = "Dislike"
        public_review = SimpleNamespace(
            name="REVIEW-INTERNAL",
            public_id="review_public",
            reviewer="author@example.test",
        )

        with (
            patch.object(service, "_public_review_row", return_value=public_review),
            patch("aos.services.reviews.service.is_blocked_between", return_value=False),
            patch(
                "aos.services.reviews.service.frappe.db.sql",
                side_effect=[[], [SimpleNamespace(name="RREACT-WINNER", reaction="Dislike")]],
            ),
            patch("aos.services.reviews.service.frappe.new_doc", return_value=candidate),
            patch("aos.services.reviews.service.is_duplicate_entry_error", return_value=True),
            patch("aos.services.reviews.service.frappe.get_doc", return_value=winner),
            patch(
                "aos.services.reviews.service.frappe.db.get_value",
                return_value=SimpleNamespace(like_count=1, dislike_count=0),
            ),
        ):
            result = service.like(user="viewer@example.test", payload={"review_id": "review_public"})

        self.assertEqual((result["reaction"], result["changed"]), ("Like", True))
        self.assertEqual(winner.reaction, "Like")
        winner.save.assert_called_once_with(ignore_permissions=True)
