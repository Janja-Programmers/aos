"""Race-safe wishlist state transitions."""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import getdate, nowdate

from aos.services.ads.errors import AdsNotFoundError, AdsValidationError

from .constants import WISHLIST_STATUS_ACTIVE, WISHLIST_STATUS_REMOVED


@dataclass(frozen=True, slots=True)
class WishlistMutationResult:
    ad_id: str
    wishlisted: bool
    changed: bool
    wishlist_count: int | None


class WishlistService:
    """Own wishlist eligibility and mutation invariants.

    The unique ``(user, ad)`` database index is the final race guard. Existing
    logical rows are locked before state changes, while first-time concurrent
    inserts recover from ``DuplicateEntryError`` and converge on the requested
    state.
    """

    def set_state(
        self,
        *,
        user: str,
        ad_id: str,
        requested: bool | None,
    ) -> WishlistMutationResult:
        user = str(user or "").strip()
        ad_id = str(ad_id or "").strip()
        if not user or user == "Guest":
            raise AdsValidationError("Invalid wishlist owner.", code="INVALID_WISHLIST_REQUEST")
        if not ad_id:
            raise AdsValidationError("Ad is required.", code="INVALID_WISHLIST_REQUEST")

        rows = frappe.db.sql(
            """
            SELECT name, status
            FROM `tabAOS Wishlist`
            WHERE user = %s AND ad = %s
            ORDER BY creation ASC, name ASC
            LIMIT 1
            FOR UPDATE
            """,
            (user, ad_id),
            as_dict=True,
        )
        doc = frappe.get_doc("AOS Wishlist", rows[0].name) if rows else None
        current = bool(doc and doc.status == WISHLIST_STATUS_ACTIVE)
        desired = (not current) if requested is None else bool(requested)

        # Removal remains idempotent even when an Ad has since expired, been
        # moderated, or become blocked. This prevents stale private rows from
        # becoming impossible for their owner to clear.
        if desired:
            self._assert_add_allowed(user=user, ad_id=ad_id)

        changed = False
        if doc:
            if current != desired:
                doc.status = WISHLIST_STATUS_ACTIVE if desired else WISHLIST_STATUS_REMOVED
                doc.save(ignore_permissions=True)
                changed = True
        elif desired:
            changed = self._insert_or_restore(user=user, ad_id=ad_id)

        wishlist_count = None
        if desired:
            count_value = frappe.db.get_value("AOS Ad", ad_id, "wishlist_count")
            wishlist_count = int(count_value or 0) if count_value is not None else None
        return WishlistMutationResult(
            ad_id=ad_id,
            wishlisted=desired,
            changed=changed,
            wishlist_count=wishlist_count,
        )

    def _insert_or_restore(self, *, user: str, ad_id: str) -> bool:
        doc = frappe.get_doc(
            {
                "doctype": "AOS Wishlist",
                "user": user,
                "ad": ad_id,
                "status": WISHLIST_STATUS_ACTIVE,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
            return True
        except frappe.DuplicateEntryError:
            existing = frappe.db.get_value(
                "AOS Wishlist",
                {"user": user, "ad": ad_id},
                "name",
            )
            if not existing:
                raise

            doc = frappe.get_doc("AOS Wishlist", existing)
            if doc.status == WISHLIST_STATUS_ACTIVE:
                return False

            doc.status = WISHLIST_STATUS_ACTIVE
            doc.save(ignore_permissions=True)
            return True

    @staticmethod
    def _assert_add_allowed(*, user: str, ad_id: str) -> None:
        ad = frappe.db.get_value(
            "AOS Ad",
            ad_id,
            ["name", "status", "seller", "expires_on"],
            as_dict=True,
        )
        today = getdate(nowdate())
        if not ad or ad.status != "Active" or (ad.expires_on and getdate(ad.expires_on) < today):
            raise AdsNotFoundError("Ad not found.")

        seller = frappe.db.get_value(
            "AOS Seller",
            ad.seller,
            ["user", "status"],
            as_dict=True,
        )
        if not seller or seller.status != "Active":
            raise AdsNotFoundError("Ad not found.")
        if seller.user == user:
            raise AdsValidationError(
                "You cannot add your own ad to your wishlist.",
                code="OWN_AD_WISHLIST_FORBIDDEN",
            )

        blocked = frappe.db.sql(
            """
            SELECT 1
            FROM `tabAOS User Block`
            WHERE status = 'Active'
              AND ((blocker_user = %s AND blocked_user = %s)
                OR (blocker_user = %s AND blocked_user = %s))
            LIMIT 1
            """,
            (user, seller.user, seller.user, user),
        )
        if blocked:
            raise AdsNotFoundError("Ad not found.")
