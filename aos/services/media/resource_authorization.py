"""Explicit resource ownership checks for media attachment boundaries."""

from __future__ import annotations

import frappe

from aos.services.media.media_purposes import MediaPurpose


class ResourceAuthorizationError(PermissionError):
    pass


class ResourceNotFoundError(FileNotFoundError):
    pass


def assert_purpose_upload_allowed(*, user: str, policy: MediaPurpose, system: bool = False) -> None:
    if system:
        return
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        raise ResourceAuthorizationError("Authentication is required")
    if not policy.client_upload_allowed:
        raise ResourceAuthorizationError("This media purpose is reserved for internal processing")
    if policy.allowed_roles:
        roles = set(frappe.get_roles(clean_user) or [])
        if not roles.intersection(policy.allowed_roles):
            raise ResourceAuthorizationError("This media purpose requires an administrative role")


def assert_attachment_target_allowed(
    *,
    user: str,
    policy: MediaPurpose,
    attached_doctype: str,
    attached_name: str,
    system: bool = False,
) -> None:
    doctype = str(attached_doctype or "").strip()
    name = str(attached_name or "").strip()
    if not doctype or not name:
        raise ResourceAuthorizationError("Attachment resource is required")
    if doctype not in policy.allowed_attachment_doctypes:
        raise ResourceAuthorizationError("Media purpose cannot be attached to this resource")
    if not frappe.db.exists(doctype, name):
        raise ResourceNotFoundError("Attachment resource was not found")
    if system:
        return
    if not _user_owns_resource(user=user, doctype=doctype, name=name):
        raise ResourceAuthorizationError("You cannot attach media to this resource")


def _user_owns_resource(*, user: str, doctype: str, name: str) -> bool:
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        return False
    if "System Manager" in set(frappe.get_roles(clean_user) or []):
        return True

    if doctype == "AOS Profile":
        return name == clean_user or frappe.db.get_value(doctype, name, "user") == clean_user
    if doctype == "AOS Seller":
        return frappe.db.get_value(doctype, name, "user") == clean_user
    if doctype == "AOS Ad":
        seller = frappe.db.get_value(doctype, name, "seller")
        return bool(seller and frappe.db.get_value("AOS Seller", seller, "user") == clean_user)
    if doctype == "AOS Review":
        return frappe.db.get_value(doctype, name, "reviewer") == clean_user
    if doctype == "AOS Verification Request":
        return frappe.db.get_value(doctype, name, "user") == clean_user
    if doctype == "AOS Live Stream":
        return frappe.db.get_value(doctype, name, "host_user") == clean_user
    if doctype == "AOS Message":
        return frappe.db.get_value(doctype, name, "sender") == clean_user
    if doctype == "AOS Short":
        owner = frappe.db.get_value(doctype, name, "owner")
        if owner == clean_user:
            return True
        seller = frappe.db.get_value(doctype, name, "seller")
        return bool(seller and frappe.db.get_value("AOS Seller", seller, "user") == clean_user)
    if doctype == "AOS Sound":
        return frappe.db.get_value(doctype, name, "owner") == clean_user
    if doctype == "AOS Category":
        return False
    return False
