"""Explicit resource ownership and Role Permission Manager checks for Media."""

from __future__ import annotations

import frappe

from aos.services.media.media_purposes import MediaPurpose
from aos.utils.doctype_permissions import has_doctype_permission


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
    if policy.required_permission_doctype and not has_doctype_permission(
        user=clean_user,
        doctype=policy.required_permission_doctype,
        ptype=policy.required_permission_type,
    ):
        raise ResourceAuthorizationError(
            f"{policy.required_permission_type.title()} permission for "
            f"{policy.required_permission_doctype} is required"
        )


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
    if not _user_can_manage_resource(user=user, doctype=doctype, name=name):
        raise ResourceAuthorizationError("You cannot attach media to this resource")


def _user_can_manage_resource(*, user: str, doctype: str, name: str) -> bool:
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        return False

    # Administrative access is defined by Frappe's permission engine so Custom
    # DocPerm / Role Permissions Manager grants are honored without role names.
    if has_doctype_permission(
        user=clean_user,
        doctype=doctype,
        ptype="write",
        docname=name,
    ):
        return True

    # Product/API ownership remains independent from Desk role permissions.
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
    if doctype == "AOS Conversation":
        return bool(
            frappe.db.exists(
                "AOS Conversation Participant",
                {
                    "conversation": name,
                    "user": clean_user,
                    "status": "active",
                    "role": ["in", ["owner", "admin"]],
                },
            )
        )
    if doctype == "AOS Short":
        owner = frappe.db.get_value(doctype, name, "owner")
        if owner == clean_user:
            return True
        seller = frappe.db.get_value(doctype, name, "seller")
        return bool(seller and frappe.db.get_value("AOS Seller", seller, "user") == clean_user)
    if doctype == "AOS Sound":
        return frappe.db.get_value(doctype, name, "owner") == clean_user
    return False
