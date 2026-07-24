"""Ads ownership and seller eligibility boundaries."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import AD_DOCTYPE
from .errors import AdsNotFoundError, AdsPermissionError


def get_seller_for_user(user: str, *, require_active: bool = False) -> object | None:
    row = frappe.db.get_value(
        "AOS Seller",
        {"user": user},
        ["name", "user", "status"],
        as_dict=True,
    )
    if not row:
        return None
    if require_active and str(row.get("status") or "") != "Active":
        raise AdsPermissionError("Seller account is not active.", code="AD_SELLER_INACTIVE")
    return row


def require_active_seller(user: str) -> object:
    seller = get_seller_for_user(user, require_active=True)
    if not seller:
        raise AdsPermissionError("Seller profile is required.", code="SELLER_REQUIRED")
    return seller


def get_owned_ad_row(user: str, ad_id: Any, *, fields: list[str] | None = None) -> object:
    clean_id = str(ad_id or "").strip()
    if not clean_id:
        raise AdsNotFoundError("Ad not found.")
    selected = list(dict.fromkeys(["name", "seller", "status", *(fields or [])]))
    row = frappe.db.get_value(AD_DOCTYPE, clean_id, selected, as_dict=True)
    if not row:
        raise AdsNotFoundError("Ad not found.")
    seller_user = frappe.db.get_value("AOS Seller", row.seller, "user")
    if seller_user != user:
        # Avoid confirming that another seller's private ad exists.
        raise AdsNotFoundError("Ad not found.")
    return row


def seller_user(seller_id: Any) -> str:
    return str(frappe.db.get_value("AOS Seller", str(seller_id or "").strip(), "user") or "").strip()
