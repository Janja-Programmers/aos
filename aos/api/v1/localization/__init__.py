"""Canonical public AOS API v1 endpoints for Localization."""

from __future__ import annotations

import frappe

from aos.api.shared.transport import client_kwargs
from aos.api.localization.bundle import get_locale_bundle_impl as _get_locale_bundle_impl
from aos.api.localization.context import resolve_locale_context_impl as _resolve_locale_context_impl
from aos.api.localization.locations import get_locations_impl as _get_locations_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locale_bundle(**kwargs):
	return _get_locale_bundle_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_locations(**kwargs):
	return _get_locations_impl(**client_kwargs(kwargs))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def resolve_locale_context(**kwargs):
	return _resolve_locale_context_impl(**client_kwargs(kwargs))
