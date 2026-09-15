"""Opaque public identifiers for Marketplace Discovery resources."""
from __future__ import annotations

import secrets
from typing import Any

import frappe

from aos.services.ads.errors import AdsNotFoundError

_PREFIX = {
    "AOS Ad": "ad",
    "AOS Ad Draft": "draft",
    "AOS Saved Search": "search",
}


def new_public_id(doctype: str) -> str:
    prefix = _PREFIX.get(str(doctype or "").strip())
    if not prefix:
        raise ValueError("Unsupported public-id doctype")
    return f"{prefix}_{secrets.token_urlsafe(18).rstrip('=')}"


def ensure_public_id(doc: Any) -> None:
    if getattr(doc, "meta", None) and doc.meta.has_field("public_id") and not getattr(doc, "public_id", None):
        doc.public_id = new_public_id(doc.doctype)


def resolve_ad_name(public_id: Any) -> str:
    value = str(public_id or "").strip()
    if not value:
        raise AdsNotFoundError("Ad not found.")
    name = frappe.db.get_value("AOS Ad", {"public_id": value}, "name")
    if not name:
        raise AdsNotFoundError("Ad not found.")
    return str(name)


def ad_public_id(name: Any) -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    return str(frappe.db.get_value("AOS Ad", value, "public_id") or "")


def resolve_public_name(doctype: str, public_id: Any) -> str:
    value = str(public_id or "").strip()
    if not value or doctype not in _PREFIX:
        return ""
    return str(frappe.db.get_value(doctype, {"public_id": value}, "name") or "")
