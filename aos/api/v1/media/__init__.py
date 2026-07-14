"""Public AOS API v1 wrappers for media.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.media.*.
Implementation stays in aos.api.media implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.media.upload import (
    init_upload_impl as _init_upload_impl,
    confirm_upload_impl as _confirm_upload_impl,
)
from aos.api.media.urls import (
    get_media_url_impl as _get_media_url_impl,
)
from aos.api.media.delete import (
    delete_media_impl as _delete_media_impl,
)
from aos.api.media.background import (
    remove_background_impl as _remove_background_impl,
)

@frappe.whitelist(methods=["POST"])
def init_upload(**kwargs):
    """Execute the v1 media.init_upload endpoint."""
    return _init_upload_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def confirm_upload(**kwargs):
    """Execute the v1 media.confirm_upload endpoint."""
    return _confirm_upload_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_media_url(**kwargs):
    """Execute the v1 media.get_media_url endpoint."""
    return _get_media_url_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_media(**kwargs):
    """Execute the v1 media.delete_media endpoint."""
    return _delete_media_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_background(**kwargs):
    """Execute the v1 media.remove_background endpoint."""
    return _remove_background_impl(**kwargs)
