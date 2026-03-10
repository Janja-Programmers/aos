"""File endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .delete import delete_file_impl
from .remove_background import remove_background_impl


@frappe.whitelist(methods=["POST"])
def delete_file(**kwargs):
    """Delete an uploaded file."""
    return delete_file_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_background(**kwargs):
    """Remove image background."""
    return remove_background_impl(**kwargs)
