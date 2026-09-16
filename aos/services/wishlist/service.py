"""Race-safe, idempotent Wishlist relationship service."""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.visibility import require_public_ad_for_viewer
from aos.services.marketplace_discovery.ids import resolve_ad_name

from .constants import WISHLIST_STATUS_ACTIVE, WISHLIST_STATUS_REMOVED


@dataclass(frozen=True, slots=True)
class WishlistMutationResult:
    public_ad_id: str
    ad_name: str
    wishlisted: bool
    changed: bool
    wishlist_count: int


class WishlistService:
    """Own the authenticated user ↔ Ad Wishlist relationship.

    Public callers supply the canonical opaque Ads public identifier. The
    Wishlist row stores the internal Frappe Ad Link. A database unique index on
    ``(user, ad)`` and deterministic DocType naming are authoritative duplicate
    guards; process-local state is never used for correctness.
    """

    def add(self, *, user: str, public_ad_id: str) -> WishlistMutationResult:
        user = self._normalize_owner(user)
        ad = require_public_ad_for_viewer(public_id=public_ad_id, viewer=user)
        if str(ad.seller_user or "").strip() == user:
            raise AdsValidationError(
                "You cannot add your own ad to your wishlist.",
                code="OWN_AD_WISHLIST_FORBIDDEN",
            )

        changed = self._activate(user=user, ad_name=str(ad.name))
        return self._result(
            public_ad_id=str(ad.public_id),
            ad_name=str(ad.name),
            wishlisted=True,
            changed=changed,
        )

    def remove(self, *, user: str, public_ad_id: str) -> WishlistMutationResult:
        user = self._normalize_owner(user)
        clean_public_id = str(public_ad_id or "").strip()
        ad_name = resolve_ad_name(clean_public_id)

        # A retained relationship is sufficient authority to clear/retry a
        # private save even after the Ad becomes unavailable. If this user has
        # never had the relationship, require the normal Ads visibility boundary
        # so remove cannot become an oracle for hidden/moderated Ad existence.
        existing = self._locked_relationship(user=user, ad_name=ad_name)
        if not existing:
            visible = require_public_ad_for_viewer(public_id=clean_public_id, viewer=user)
            ad_name = str(visible.name)
            # Re-check after visibility validation to converge if another worker
            # created the relationship between the first lookup and this point.
            existing = self._locked_relationship(user=user, ad_name=ad_name)

        changed = False
        if existing and existing.status != WISHLIST_STATUS_REMOVED:
            existing.status = WISHLIST_STATUS_REMOVED
            existing.save(ignore_permissions=True)
            changed = True

        return self._result(
            public_ad_id=clean_public_id,
            ad_name=ad_name,
            wishlisted=False,
            changed=changed,
        )

    @staticmethod
    def _normalize_owner(user: str) -> str:
        clean = str(user or "").strip()
        if not clean or clean == "Guest":
            raise AdsValidationError(
                "Invalid wishlist owner.",
                code="INVALID_WISHLIST_REQUEST",
            )
        return clean

    @staticmethod
    def _relationship_name(*, user: str, ad_name: str) -> str | None:
        value = frappe.db.get_value(
            "AOS Wishlist",
            {"user": user, "ad": ad_name},
            "name",
        )
        return str(value) if value else None

    def _locked_relationship(self, *, user: str, ad_name: str):
        name = self._relationship_name(user=user, ad_name=ad_name)
        if not name:
            return None
        try:
            return frappe.get_doc("AOS Wishlist", name, for_update=True)
        except frappe.DoesNotExistError:
            return None

    def _activate(self, *, user: str, ad_name: str) -> bool:
        existing = self._locked_relationship(user=user, ad_name=ad_name)
        if existing:
            if existing.status == WISHLIST_STATUS_ACTIVE:
                return False
            existing.status = WISHLIST_STATUS_ACTIVE
            existing.save(ignore_permissions=True)
            return True

        savepoint = f"aos_wishlist_insert_{frappe.generate_hash(length=10)}"
        frappe.db.savepoint(savepoint)
        doc = frappe.get_doc(
            {
                "doctype": "AOS Wishlist",
                "user": user,
                "ad": ad_name,
                "status": WISHLIST_STATUS_ACTIVE,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
            return True
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            frappe.db.rollback(save_point=savepoint)

        # Another worker won the first-insert race. Lock that durable row and
        # converge on Active rather than surfacing a harmless retry as failure.
        existing = self._locked_relationship(user=user, ad_name=ad_name)
        if not existing:
            raise frappe.DuplicateEntryError("Wishlist relationship duplicate could not be resolved")
        if existing.status == WISHLIST_STATUS_ACTIVE:
            return False
        existing.status = WISHLIST_STATUS_ACTIVE
        existing.save(ignore_permissions=True)
        return True

    @staticmethod
    def _result(
        *,
        public_ad_id: str,
        ad_name: str,
        wishlisted: bool,
        changed: bool,
    ) -> WishlistMutationResult:
        count = int(frappe.db.get_value("AOS Ad", ad_name, "wishlist_count") or 0)
        return WishlistMutationResult(
            public_ad_id=public_ad_id,
            ad_name=ad_name,
            wishlisted=wishlisted,
            changed=changed,
            wishlist_count=count,
        )
