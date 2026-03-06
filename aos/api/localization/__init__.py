"""Localization endpoints.

  - whitelisted wrappers live in this __init__.py
  - business logic lives in sibling modules
"""

from __future__ import annotations

import frappe

from .bundle import get_locale_bundle_impl
from .locations import get_locations_impl


@frappe.whitelist(allow_guest=True)
def get_locale_bundle(**kwargs):
    return get_locale_bundle_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_locations(**kwargs):
    return get_locations_impl(**kwargs)

