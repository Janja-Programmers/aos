"""Public AOS API v1 wrappers for verification.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.verification.*.
Implementation stays in aos.api.verification implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.shared.transport import execute_endpoint as _execute_endpoint

from aos.api.verification.submit_verification import (
    submit_verification_impl as _submit_verification_impl,
)
from aos.api.verification.get_my_verification import (
    get_my_verification_impl as _get_my_verification_impl,
)

@frappe.whitelist(methods=["POST"])
def submit_verification(**kwargs):
    """Submit or resubmit an account verification request."""
    return _execute_endpoint(_submit_verification_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def get_my_verification(**kwargs):
    """Get logged-in user's verification status."""
    return _execute_endpoint(_get_my_verification_impl, kwargs)
