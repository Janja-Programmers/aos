"""Privacy-safe shared-object helpers for Chat serializers and mutations."""

from __future__ import annotations

from typing import Any, Iterable

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.blocking import get_blocked_user_set
from aos.api.shared.user_display import get_user_display_map
from aos.services.accounts.constants import ACCOUNT_STATUS_ACTIVE
from aos.services.sellers.identity import public_seller_id_for_name
from aos.services.media.media_service import MediaService


def _clean_ids(values: Iterable[str]) -> list[str]:
    return sorted({str(value or "").strip() for value in values if str(value or "").strip()})


def _get_ad_meta_fields() -> list[str]:
    fields = ["name", "public_id"]
    try:
        meta = frappe.get_meta("AOS Ad")
    except Exception:
        return fields
    for fieldname in (
        "title",
        "ad_title",
        "name1",
        "price",
        "currency",
        "status",
        "seller",
        "expires_on",
    ):
        if meta.has_field(fieldname):
            fields.append(fieldname)
    return fields


def _fetch_ad_thumbnails(ad_ids: list[str]) -> dict[str, str | None]:
    if not ad_ids:
        return {}
    rows = frappe.db.sql(
        """
        SELECT adi.parent AS ad, adi.media AS media
        FROM `tabAOS Ad Image` adi
        INNER JOIN (
            SELECT ranked.parent, MIN(ranked.rank_key) AS best_rank
            FROM (
                SELECT parent,
                    CONCAT(
                        LPAD(CASE WHEN IFNULL(is_primary, 0) = 1 THEN 0 ELSE 1 END, 2, '0'),
                        '-', LPAD(IFNULL(sort_order, 999999), 8, '0'),
                        '-', LPAD(IFNULL(idx, 999999), 8, '0')
                    ) AS rank_key
                FROM `tabAOS Ad Image`
                WHERE parent IN %(ad_ids)s
                  AND parenttype = 'AOS Ad'
                  AND parentfield = 'images'
                  AND media IS NOT NULL
                  AND media != ''
            ) ranked
            GROUP BY ranked.parent
        ) best
          ON best.parent = adi.parent
         AND CONCAT(
                LPAD(CASE WHEN IFNULL(adi.is_primary, 0) = 1 THEN 0 ELSE 1 END, 2, '0'),
                '-', LPAD(IFNULL(adi.sort_order, 999999), 8, '0'),
                '-', LPAD(IFNULL(adi.idx, 999999), 8, '0')
             ) = best.best_rank
        WHERE adi.parent IN %(ad_ids)s
          AND adi.parenttype = 'AOS Ad'
          AND adi.parentfield = 'images'
        """,
        {"ad_ids": tuple(ad_ids)},
        as_dict=True,
    )
    attachments = [
        (str(row.media or "").strip(), str(row.ad or "").strip())
        for row in rows
        if str(row.media or "").strip() and str(row.ad or "").strip()
    ]
    urls = MediaService().get_public_attachment_url_map(
        attachments,
        purpose="ad_image",
        attached_doctype="AOS Ad",
        attached_field="images",
    ) if attachments else {}
    return {
        ad_id: urls.get((media_id, ad_id))
        for media_id, ad_id in attachments
        if urls.get((media_id, ad_id))
    }


def _eligible_ad_rows(public_ad_ids: list[str]) -> tuple[list[Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return active Ads resolved only through canonical public identifiers."""
    if not public_ad_ids:
        return [], {}, {}, {}

    rows = frappe.get_all(
        "AOS Ad",
        filters={"public_id": ["in", public_ad_ids]},
        fields=_get_ad_meta_fields(),
        limit=max(1, len(public_ad_ids)),
    )
    seller_ids = _clean_ids(row.get("seller") for row in rows)
    seller_rows = (
        frappe.get_all(
            "AOS Seller",
            filters={"name": ["in", seller_ids]},
            fields=["name", "user", "status"],
            limit=max(1, len(seller_ids)),
        )
        if seller_ids
        else []
    )
    sellers = {str(row.name): row for row in seller_rows}
    seller_users = _clean_ids(row.get("user") for row in seller_rows)
    user_rows = (
        frappe.get_all(
            "User",
            filters={"name": ["in", seller_users]},
            fields=["name", "enabled"],
            limit=max(1, len(seller_users)),
        )
        if seller_users
        else []
    )
    users = {str(row.name): row for row in user_rows}
    profile_rows = (
        frappe.get_all(
            "AOS Profile",
            filters={"user": ["in", seller_users]},
            fields=["user", "account_status"],
            limit=max(1, len(seller_users)),
        )
        if seller_users
        else []
    )
    profiles = {str(row.user): row for row in profile_rows}
    return rows, sellers, users, profiles


def _row_is_publicly_available(row: Any, *, sellers: dict[str, Any], users: dict[str, Any], profiles: dict[str, Any]) -> bool:
    if str(row.get("status") or "") != "Active":
        return False
    expires_on = row.get("expires_on")
    if expires_on and getdate(expires_on) < getdate(nowdate()):
        return False

    seller = sellers.get(str(row.get("seller") or ""))
    if not seller or str(seller.get("status") or "") != "Active":
        return False
    seller_user = str(seller.get("user") or "")
    if not seller_user:
        return False
    user_row = users.get(seller_user)
    if not user_row or int(user_row.get("enabled") or 0) != 1:
        return False
    profile = profiles.get(seller_user)
    if profile:
        account_status = str(profile.get("account_status") or ACCOUNT_STATUS_ACTIVE)
        if account_status != ACCOUNT_STATUS_ACTIVE:
            return False
    return True


def fetch_chat_ad_previews(ad_ids: Iterable[str], *, viewer: str | None = None) -> dict[str, dict[str, Any]]:
    """Project public Ads without exposing internal ``AOS Ad.name`` values."""
    public_ids = _clean_ids(ad_ids)
    if not public_ids:
        return {}
    rows, sellers, users, profiles = _eligible_ad_rows(public_ids)
    seller_users = [
        str(sellers[str(row.get("seller"))].get("user"))
        for row in rows
        if str(row.get("seller") or "") in sellers
    ]
    blocked = get_blocked_user_set(viewer, seller_users) if viewer else set()
    internal_ids = [str(row.name) for row in rows]
    thumbnails = _fetch_ad_thumbnails(internal_ids)

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _row_is_publicly_available(row, sellers=sellers, users=users, profiles=profiles):
            continue
        seller = sellers.get(str(row.get("seller") or ""))
        seller_user = str(seller.get("user") or "") if seller else ""
        if seller_user in blocked:
            continue
        public_id = str(row.get("public_id") or "").strip()
        if not public_id:
            continue
        title = row.get("title") or row.get("ad_title") or row.get("name1") or "Ad"
        seller_id = public_seller_id_for_name(row.get("seller"))
        result[public_id] = {
            "id": public_id,
            "title": title,
            "price": row.get("price"),
            "currency": row.get("currency"),
            "status": row.get("status"),
            "seller_id": seller_id,
            "thumbnail": thumbnails.get(str(row.name)),
        }
    return result


def ad_is_shareable_to_users(ad_id: str, *, users: Iterable[str]) -> bool:
    """Check one Ad against public eligibility and every bounded Chat recipient.

    Each relationship check is routed through the canonical blocking helper. The
    caller bounds the audience (Chat forward currently caps it at 20).
    """
    clean_id = str(ad_id or "").strip()
    if not clean_id:
        return False
    rows, sellers, user_rows, profiles = _eligible_ad_rows([clean_id])
    if not rows:
        return False
    row = rows[0]
    if not _row_is_publicly_available(row, sellers=sellers, users=user_rows, profiles=profiles):
        return False
    seller = sellers.get(str(row.get("seller") or ""))
    seller_user = str(seller.get("user") or "") if seller else ""
    if not seller_user:
        return False
    for viewer in _clean_ids(users):
        if seller_user in get_blocked_user_set(viewer, [seller_user]):
            return False
    return True
