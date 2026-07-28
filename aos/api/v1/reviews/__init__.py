"""Stable public AOS API v1 wrappers for Reviews."""

from __future__ import annotations

import frappe

from aos.api.reviews.create import create_review_impl as _create_review_impl
from aos.api.reviews.delete import delete_review_impl as _delete_review_impl
from aos.api.reviews.detail import get_review_impl as _get_review_impl
from aos.api.reviews.list import list_reviews_impl as _list_reviews_impl
from aos.api.reviews.my import (
    list_my_reviews_impl as _list_my_reviews_impl,
    list_reviews_received_impl as _list_reviews_received_impl,
)
from aos.api.reviews.report import report_review_impl as _report_review_impl
from aos.api.reviews.toggle import toggle_reaction_impl as _toggle_reaction_impl
from aos.api.reviews.update import update_review_impl as _update_review_impl
from aos.api.reviews.viewer_state import get_review_viewer_state_impl as _get_review_viewer_state_impl
from aos.api.v1._transport import client_kwargs


def _legacy_v1_response(response):
    """Preserve machine codes consumed by the original v1 review clients.

    The canonical hardened code is retained in ``data.canonical_error`` so new
    clients can migrate without requiring a second endpoint version. This
    adapter is intentionally used only by the four review methods that existed
    before this hardening phase.
    """

    if not isinstance(response, dict) or response.get("ok") is not False:
        return response
    canonical = str(response.get("error") or "")
    legacy = {
        "REVIEW_ALREADY_EXISTS": "ALREADY_REVIEWED",
        "REVIEW_SELF_NOT_ALLOWED": "OWN_AD",
        "TRANSACTION_NOT_ELIGIBLE": "CONTACT_SELLER_REQUIRED",
        "REVIEW_TARGET_INVALID": "NOT_FOUND",
        "REVIEW_NOT_FOUND": "NOT_FOUND",
        "REVIEW_SELF_VOTE_NOT_ALLOWED": "VALIDATION_ERROR",
    }.get(canonical)
    if not legacy and canonical.startswith("INVALID_"):
        legacy = "VALIDATION_ERROR"
    if not legacy:
        return response
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    data.setdefault("canonical_error", canonical)
    response["data"] = data
    response["error"] = legacy
    return response


@frappe.whitelist(methods=["POST"])
def create_review(**kwargs):
    return _legacy_v1_response(_create_review_impl(**client_kwargs(kwargs)))


@frappe.whitelist(methods=["POST"])
def update_review(**kwargs):
    return _update_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def delete_review(**kwargs):
    """Soft-withdraw a review; repeated requests are idempotent."""
    return _delete_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_review(**kwargs):
    return _get_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_reviews(**kwargs):
    return _legacy_v1_response(_list_reviews_impl(**client_kwargs(kwargs)))


@frappe.whitelist(methods=["GET"])
def list_my_reviews(**kwargs):
    return _list_my_reviews_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_reviews_received(**kwargs):
    return _list_reviews_received_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_review_viewer_state(**kwargs):
    return _legacy_v1_response(_get_review_viewer_state_impl(**client_kwargs(kwargs)))


@frappe.whitelist(methods=["POST"])
def toggle_reaction(**kwargs):
    return _legacy_v1_response(_toggle_reaction_impl(**client_kwargs(kwargs)))


@frappe.whitelist(methods=["POST"])
def report_review(**kwargs):
    return _report_review_impl(**client_kwargs(kwargs))
