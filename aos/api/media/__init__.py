"""MinIO-backed AOS media endpoints.

These endpoints are the new AOS-owned media layer. Existing feature endpoints
will gradually migrate from Frappe File URLs to media_id references.
"""

from __future__ import annotations

import frappe

from .background import remove_background_impl
from .delete import delete_media_impl
from .upload import confirm_upload_impl, init_upload_impl
from .urls import get_media_url_impl


@frappe.whitelist(methods=["POST"])
def init_upload(**kwargs):
    return init_upload_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def confirm_upload(**kwargs):
    return confirm_upload_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_media_url(**kwargs):
    return get_media_url_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_media(**kwargs):
    return delete_media_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_background(**kwargs):
    return remove_background_impl(**kwargs)
