"""File endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .delete import delete_file_impl


@frappe.whitelist(methods=["POST"])
def delete_file(**kwargs):
    """Delete an uploaded file."""
    return delete_file_impl(**kwargs)