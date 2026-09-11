"""Canonical public AOS API v1 endpoints for Accounts."""

from __future__ import annotations

import frappe

from aos.api.accounts.get_my_preference import get_my_preference_impl as _get_my_preference_impl
from aos.api.accounts.profile import get_my_profile_impl as _get_my_profile_impl
from aos.api.accounts.profile import get_profile_impl as _get_profile_impl
from aos.api.accounts.profile import update_my_profile_impl as _update_my_profile_impl
from aos.api.accounts.update_my_preference import update_my_preference_impl as _update_my_preference_impl
from aos.api.shared.transport import execute_endpoint as _execute_endpoint


@frappe.whitelist(methods=["GET"])
def get_my_profile(**kwargs):
    return _execute_endpoint(_get_my_profile_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def get_profile(**kwargs):
    return _execute_endpoint(_get_profile_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_profile(**kwargs):
    return _execute_endpoint(_update_my_profile_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def get_my_preference(**kwargs):
    return _execute_endpoint(_get_my_preference_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_preference(**kwargs):
    return _execute_endpoint(_update_my_preference_impl, kwargs)
