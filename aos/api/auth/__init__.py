"""Authentication & session endpoints.

Public functions in this module are whitelisted and form the API surface.
Implementation details live in sibling modules to keep things maintainable.
"""

import frappe

from .register import register_impl
from .otp import verify_email_otp_impl, resend_email_otp_impl
from .session import login_impl, me_impl, logout_impl
from .password_change import change_password_impl
from .google_login import google_login_impl
from .apple_login import apple_login_impl

from .delete_account import (
    delete_account_impl,
    request_restore_account_impl,
    restore_account_impl,
)

from .password_reset import (
    forgot_password_request_impl,
    forgot_password_verify_otp_impl,
    forgot_password_reset_impl,
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def register(**kwargs):
    return register_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email_otp(**kwargs):
    return verify_email_otp_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(**kwargs):
    return resend_email_otp_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(**kwargs):
    return login_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def google_login(**kwargs):
    return google_login_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def apple_login(**kwargs):
    return apple_login_impl(**kwargs)


@frappe.whitelist()
def me(**kwargs):
    return me_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def logout(**kwargs):
    return logout_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_request(**kwargs):
    return forgot_password_request_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_verify_otp(**kwargs):
    return forgot_password_verify_otp_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_reset(**kwargs):
    return forgot_password_reset_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def change_password(**kwargs):
    return change_password_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_account(**kwargs):
    return delete_account_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_restore_account(**kwargs):
    return request_restore_account_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def restore_account(**kwargs):
    return restore_account_impl(**kwargs)
