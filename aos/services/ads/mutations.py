"""Transactional Ads document mutation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import frappe
from frappe.utils import add_days, now_datetime, today

from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import (
    ACTION_DELETE,
    ACTION_MARK_AVAILABLE,
    ACTION_MARK_SOLD,
    ACTION_RENEW,
    STATUS_ACTIVE,
    STATUS_DELETED,
    STATUS_EXPIRED,
    STATUS_REVIEWING,
    STATUS_SOLD,
)
from .lifecycle import Transition


def replace_child_table(doc: object, fieldname: str, rows: Iterable[Mapping[str, Any]]) -> None:
    doc.set(fieldname, [])
    for row in rows:
        child = doc.append(fieldname, {})
        for key, value in row.items():
            setattr(child, key, value)


def apply_ad_values(doc: object, values: Mapping[str, Any], *, include_market: bool = False) -> None:
    scalar_fields = (
        "title",
        "description",
        "category",
        "location",
        "price_type",
        "price",
        "price_unit",
        "offer_price",
        "offer_start_date",
        "offer_end_date",
        "video_media",
        "video",
    )
    for field in scalar_fields:
        if field in values:
            setattr(doc, field, values.get(field))
    if include_market:
        for field in ("country", "currency", "seller"):
            if field in values:
                setattr(doc, field, values.get(field))
    if "details" in values:
        replace_child_table(doc, "details", values.get("details") or [])
    if "images" in values:
        replace_child_table(doc, "images", values.get("images") or [])


def apply_transition(doc: object, transition: Transition) -> None:
    doc.flags.aos_status_action = transition.action
    if not transition.changed:
        return

    now = now_datetime()
    doc.status = transition.new_status
    if doc.meta.has_field("status_changed_on"):
        doc.status_changed_on = now

    if transition.action == ACTION_RENEW:
        settings = get_aos_settings_snapshot()
        doc.expires_on = add_days(today(), settings.ad_expiry_days)
        if doc.meta.has_field("renewed_on"):
            doc.renewed_on = now
    elif transition.action == ACTION_MARK_SOLD:
        if doc.meta.has_field("sold_on"):
            doc.sold_on = now
    elif transition.action == ACTION_MARK_AVAILABLE:
        if doc.meta.has_field("sold_on"):
            doc.sold_on = None
    elif transition.action == ACTION_DELETE:
        if doc.meta.has_field("deleted_on"):
            doc.deleted_on = now

    if transition.new_status == STATUS_ACTIVE and doc.meta.has_field("published_on") and not doc.published_on:
        doc.published_on = now
    if transition.new_status == STATUS_EXPIRED and doc.meta.has_field("expired_on"):
        doc.expired_on = now
    if transition.new_status == STATUS_DELETED and doc.meta.has_field("deleted_on"):
        doc.deleted_on = now
    if transition.new_status == STATUS_REVIEWING:
        doc.reviewed_by = None
        doc.reviewed_on = None
    if transition.new_status == STATUS_SOLD and doc.meta.has_field("sold_on"):
        doc.sold_on = now


def lock_ad(ad_id: str) -> None:
    frappe.db.sql("SELECT name FROM `tabAOS Ad` WHERE name = %s FOR UPDATE", (ad_id,))
