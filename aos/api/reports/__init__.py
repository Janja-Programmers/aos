"""Reports endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .reasons import list_report_reasons_impl
from .create import create_report_impl

@frappe.whitelist()
def list_report_reasons(**kwargs):
    """List available report reasons."""
    return list_report_reasons_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def report_ad(**kwargs):
    """Report an Ad."""
    return create_report_impl(**kwargs)
