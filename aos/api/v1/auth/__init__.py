"""Public AOS API v1 wrappers for auth.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.auth.*.
Implementation stays in aos.api.auth implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.auth.contracts import handle_unexpected_auth_exception as _auth_exception_policy
from aos.api.shared.transport import execute_endpoint as _execute_endpoint

from aos.api.auth.register import (
    register_impl as _register_impl,
)
from aos.api.auth.otp import (
    verify_email_otp_impl as _verify_email_otp_impl,
    resend_email_otp_impl as _resend_email_otp_impl,
)
from aos.api.auth.session import (
    login_impl as _login_impl,
    me_impl as _me_impl,
    logout_impl as _logout_impl,
)
from aos.api.auth.two_factor import verify_two_factor_impl as _verify_two_factor_impl
from aos.api.auth.google_login import (
    google_login_impl as _google_login_impl,
)
from aos.api.auth.apple_login import (
    apple_login_impl as _apple_login_impl,
)
from aos.api.auth.password_reset import (
    forgot_password_request_impl as _forgot_password_request_impl,
    forgot_password_verify_otp_impl as _forgot_password_verify_otp_impl,
    forgot_password_reset_impl as _forgot_password_reset_impl,
)
from aos.api.auth.password_change import (
    change_password_impl as _change_password_impl,
)
from aos.api.auth.delete_account import (
    delete_account_impl as _delete_account_impl,
    request_restore_account_impl as _request_restore_account_impl,
    restore_account_impl as _restore_account_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def register(**kwargs):
    """Execute the v1 auth.register endpoint."""
    return _execute_endpoint(_register_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email_otp(**kwargs):
    """Execute the v1 auth.verify_email_otp endpoint."""
    return _execute_endpoint(_verify_email_otp_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(**kwargs):
    """Execute the v1 auth.resend_email_otp endpoint."""
    return _execute_endpoint(_resend_email_otp_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(**kwargs):
    """Execute the v1 auth.login endpoint."""
    return _execute_endpoint(_login_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_two_factor(**kwargs):
    """Finish a password login that requires a second factor."""
    return _execute_endpoint(_verify_two_factor_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def google_login(**kwargs):
    """Execute the v1 auth.google_login endpoint."""
    return _execute_endpoint(_google_login_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def apple_login(**kwargs):
    """Execute the v1 auth.apple_login endpoint."""
    return _execute_endpoint(_apple_login_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def me(**kwargs):
    """Execute the v1 auth.me endpoint."""
    return _execute_endpoint(_me_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def logout(**kwargs):
    """Execute the v1 auth.logout endpoint."""
    return _execute_endpoint(_logout_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_request(**kwargs):
    """Execute the v1 auth.forgot_password_request endpoint."""
    return _execute_endpoint(
        _forgot_password_request_impl,
        kwargs,
        on_unexpected_exception=_auth_exception_policy,
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_verify_otp(**kwargs):
    """Execute the v1 auth.forgot_password_verify_otp endpoint."""
    return _execute_endpoint(
        _forgot_password_verify_otp_impl,
        kwargs,
        on_unexpected_exception=_auth_exception_policy,
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_reset(**kwargs):
    """Execute the v1 auth.forgot_password_reset endpoint."""
    return _execute_endpoint(
        _forgot_password_reset_impl,
        kwargs,
        on_unexpected_exception=_auth_exception_policy,
    )


@frappe.whitelist(methods=["POST"])
def change_password(**kwargs):
    """Execute the v1 auth.change_password endpoint."""
    return _execute_endpoint(_change_password_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(methods=["POST"])
def delete_account(**kwargs):
    """Execute the v1 auth.delete_account endpoint."""
    return _execute_endpoint(_delete_account_impl, kwargs, on_unexpected_exception=_auth_exception_policy)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_restore_account(**kwargs):
    """Execute the v1 auth.request_restore_account endpoint."""
    return _execute_endpoint(
        _request_restore_account_impl,
        kwargs,
        on_unexpected_exception=_auth_exception_policy,
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def restore_account(**kwargs):
    """Execute the v1 auth.restore_account endpoint."""
    return _execute_endpoint(_restore_account_impl, kwargs, on_unexpected_exception=_auth_exception_policy)
