"""Reviews identifier boundary: opaque public IDs, internal Frappe names."""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.marketplace_discovery.ids import new_public_id

from .errors import ReviewNotFoundError


def ensure_review_public_id(doc: Any) -> None:
    if not str(getattr(doc, "public_id", "") or "").strip():
        doc.public_id = new_public_id("AOS Review")


def resolve_review_name(public_id: Any) -> str:
    value = str(public_id or "").strip()
    if not value:
        raise ReviewNotFoundError("Review not found.")
    name = frappe.db.get_value("AOS Review", {"public_id": value}, "name")
    if not name:
        raise ReviewNotFoundError("Review not found.")
    return str(name)


def review_public_id(name: Any) -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    return str(frappe.db.get_value("AOS Review", value, "public_id") or "")
