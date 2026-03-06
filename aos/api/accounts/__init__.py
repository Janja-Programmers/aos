"""Account endpoints.

Public functions in this module are whitelisted and form the API surface.
Implementation details live in sibling modules to keep things maintainable.
"""

import frappe

from .profile import get_profile_impl, update_profile_impl
from .get_my_preference import get_my_preference_impl
from .update_my_preference import update_my_preference_impl


@frappe.whitelist()
def get_profile(**kwargs):
    return get_profile_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_profile(**kwargs):
    return update_profile_impl(**kwargs)


@frappe.whitelist()
def get_my_preference(**kwargs):
    return get_my_preference_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_preference(**kwargs):
    return update_my_preference_impl(**kwargs)

