"""Public AOS API v1 wrappers for media.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.media.*.
Implementation stays in aos.api.media implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.shared.transport import execute_endpoint as _execute_endpoint

from aos.api.media.upload import (
    abort_multipart_upload_impl as _abort_multipart_upload_impl,
    complete_multipart_upload_impl as _complete_multipart_upload_impl,
    confirm_upload_impl as _confirm_upload_impl,
    init_upload_impl as _init_upload_impl,
    multipart_part_urls_impl as _multipart_part_urls_impl,
    multipart_status_impl as _multipart_status_impl,
)
from aos.api.media.urls import (
    get_media_url_impl as _get_media_url_impl,
)
from aos.api.media.delete import (
    delete_media_impl as _delete_media_impl,
)
from aos.api.media.background import (
    processing_status_impl as _processing_status_impl,
    remove_background_impl as _remove_background_impl,
)

@frappe.whitelist(methods=["POST"])
def init_upload(**kwargs):
    """Execute the v1 media.init_upload endpoint."""
    return _execute_endpoint(_init_upload_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def confirm_upload(**kwargs):
    """Execute the v1 media.confirm_upload endpoint."""
    return _execute_endpoint(_confirm_upload_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def multipart_part_urls(**kwargs):
    """Issue a bounded batch of resumable multipart part upload URLs."""
    return _execute_endpoint(_multipart_part_urls_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def multipart_status(**kwargs):
    """Return authoritative object-storage multipart progress for resume."""
    return _execute_endpoint(_multipart_status_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def complete_multipart_upload(**kwargs):
    """Assemble and confirm a resumable multipart upload."""
    return _execute_endpoint(_complete_multipart_upload_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def abort_multipart_upload(**kwargs):
    """Abort an unfinished resumable multipart upload."""
    return _execute_endpoint(_abort_multipart_upload_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_media_url(**kwargs):
    """Execute the v1 media.get_media_url endpoint."""
    return _execute_endpoint(_get_media_url_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def delete_media(**kwargs):
    """Execute the v1 media.delete_media endpoint."""
    return _execute_endpoint(_delete_media_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def remove_background(**kwargs):
    """Execute the v1 media.remove_background endpoint."""
    return _execute_endpoint(_remove_background_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def processing_status(**kwargs):
    """Return status/result for asynchronous Media processing."""
    return _execute_endpoint(_processing_status_impl, kwargs)
