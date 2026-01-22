"""Authentication & session endpoints.

Public functions in this module are whitelisted and form the API surface.
Implementation details live in sibling modules to keep things maintainable.
"""

import frappe

from .register import register_impl
from .otp import verify_email_otp_impl, resend_email_otp_impl
from .session import login_impl, me_impl, logout_impl


@frappe.whitelist(allow_guest=True, methods=["POST"])
def register(email: str, password: str, full_name: str):
    return register_impl(email=email, password=password, full_name=full_name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email_otp(email: str, otp: str):
    return verify_email_otp_impl(email=email, otp=otp)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(email: str):
    return resend_email_otp_impl(email=email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(email: str, password: str):
    return login_impl(email=email, password=password)


@frappe.whitelist(methods=["GET"])
def me():
    return me_impl()


@frappe.whitelist(methods=["POST"])
def logout():
    return logout_impl()
