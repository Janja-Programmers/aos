"""Canonical public AOS API v1 wrappers for Reviews."""

from __future__ import annotations

import frappe

from aos.api.reviews.create import create_review_impl as _create_review_impl
from aos.api.reviews.delete import delete_review_impl as _delete_review_impl
from aos.api.reviews.detail import get_review_impl as _get_review_impl
from aos.api.reviews.list import list_reviews_impl as _list_reviews_impl
from aos.api.reviews.my import list_my_reviews_impl as _list_my_reviews_impl, list_reviews_received_impl as _list_reviews_received_impl
from aos.api.reviews.reaction import (
    dislike_review_impl as _dislike_review_impl,
    like_review_impl as _like_review_impl,
    undislike_review_impl as _undislike_review_impl,
    unlike_review_impl as _unlike_review_impl,
)
from aos.api.reviews.update import update_review_impl as _update_review_impl
from aos.api.reviews.viewer_state import get_review_viewer_state_impl as _get_review_viewer_state_impl
from aos.api.v1._transport import client_kwargs


@frappe.whitelist(methods=["POST"])
def create_review(**kwargs):
    return _create_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def update_review(**kwargs):
    return _update_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def delete_review(**kwargs):
    return _delete_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_review(**kwargs):
    return _get_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_reviews(**kwargs):
    return _list_reviews_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_my_reviews(**kwargs):
    return _list_my_reviews_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_reviews_received(**kwargs):
    return _list_reviews_received_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_review_viewer_state(**kwargs):
    return _get_review_viewer_state_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def like_review(**kwargs):
    return _like_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def unlike_review(**kwargs):
    return _unlike_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def dislike_review(**kwargs):
    return _dislike_review_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def undislike_review(**kwargs):
    return _undislike_review_impl(**client_kwargs(kwargs))
