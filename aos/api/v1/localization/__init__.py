"""Public AOS API v1 wrappers for localization.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.localization.*.
Implementation stays in aos.api.localization implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.localization.bundle import (
    get_locale_bundle_impl as _get_locale_bundle_impl,
)
from aos.api.localization.locations import (
    get_locations_impl as _get_locations_impl,
)

@frappe.whitelist(allow_guest=True)
def get_locale_bundle(**kwargs):
    """Execute the v1 localization.get_locale_bundle endpoint."""
    return _get_locale_bundle_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_locations(**kwargs):
    """Execute the v1 localization.get_locations endpoint."""
    return _get_locations_impl(**kwargs)
