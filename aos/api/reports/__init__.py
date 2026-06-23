"""Reports endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .reasons import list_report_reasons_impl
from .report_ad import report_ad_impl
from .report_user import report_user_impl

@frappe.whitelist()
def list_report_reasons(**kwargs):
    """List available report reasons."""
    return list_report_reasons_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def report_ad(**kwargs):
    """Report an Ad."""
    return report_ad_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def report_user(**kwargs):
    """Report a User."""
    return report_user_impl(**kwargs)
