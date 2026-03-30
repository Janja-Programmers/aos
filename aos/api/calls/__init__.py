"""
Call endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

# Call lifecycle
from .call import (
    initiate_call_impl,
    accept_call_impl,
    reject_call_impl,
    end_call_impl,
)

# Token
from .token import (
    get_call_token_impl,
)

# History
from .history import (
    list_calls_impl,
)


# Call APIs
@frappe.whitelist(methods=["POST"])
def initiate_call(**kwargs):
    return initiate_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def accept_call(**kwargs):
    return accept_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def reject_call(**kwargs):
    return reject_call_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def end_call(**kwargs):
    return end_call_impl(**kwargs)


# Token
@frappe.whitelist(methods=["POST"])
def get_call_token(**kwargs):
    return get_call_token_impl(**kwargs)


# History
@frappe.whitelist()
def list_calls(**kwargs):
    return list_calls_impl(**kwargs)
