"""Stable public v1 endpoints for User, Ad, Short, and Review reports."""

from __future__ import annotations

import frappe

from aos.api.reports.reasons import get_report_reasons_impl as _get_report_reasons_impl
from aos.api.reports.report_ad import report_ad_impl as _report_ad_impl
from aos.api.reports.report_short import report_short_impl as _report_short_impl
from aos.api.reports.report_review import report_review_impl as _report_review_impl
from aos.api.reports.report_user import report_user_impl as _report_user_impl
from aos.api.shared.transport import client_kwargs


@frappe.whitelist(methods=["GET"])
def get_report_reasons(**kwargs):
    """Return enabled reasons valid for exactly one requested target type."""
    return _get_report_reasons_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_ad(**kwargs):
    return _report_ad_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_user(**kwargs):
    return _report_user_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_short(**kwargs):
    return _report_short_impl(**client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def report_review(**kwargs):
    return _report_review_impl(**client_kwargs(kwargs))
