"""Public AOS API v1 wrappers for reports.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.reports.*.
Implementation stays in aos.api.reports implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.shared.transport import client_kwargs

from aos.api.reports.reasons import (
    list_report_reasons_impl as _list_report_reasons_impl,
)
from aos.api.reports.report_ad import (
    report_ad_impl as _report_ad_impl,
)
from aos.api.reports.report_user import (
    report_user_impl as _report_user_impl,
)
from aos.api.reports.report_short import (
    report_short_impl as _report_short_impl,
)

@frappe.whitelist()
def list_report_reasons(**kwargs):
    """List available report reasons."""
    return _list_report_reasons_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_ad(**kwargs):
    """Report an Ad."""
    return _report_ad_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_user(**kwargs):
    """Report a User."""
    return _report_user_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_short(**kwargs):
    """Report a Short."""
    return _report_short_impl(**client_kwargs(kwargs))
