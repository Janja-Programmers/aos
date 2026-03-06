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

from .password_reset import (
    forgot_password_request_impl,
    forgot_password_verify_otp_impl,
    forgot_password_reset_impl,
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def register(**kwargs):
    return register_impl(
        email=kwargs.get("email"),
        password=kwargs.get("password"),
        full_name=kwargs.get("full_name"),
        country=kwargs.get("country"),
        language=kwargs.get("language"),
        currency=kwargs.get("currency"),
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_email_otp(email: str, otp: str):
    return verify_email_otp_impl(email=email, otp=otp)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resend_email_otp(email: str):
    return resend_email_otp_impl(email=email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login(email: str, password: str):
    return login_impl(email=email, password=password)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def google_login(id_token: str):
    return google_login_impl(id_token=id_token)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def apple_login(id_token: str):
    return apple_login_impl(id_token=id_token)


@frappe.whitelist(methods=["GET"])
def me():
    return me_impl()


@frappe.whitelist(methods=["POST"])
def logout():
    return logout_impl()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_request(email: str):
    return forgot_password_request_impl(email=email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_verify_otp(email: str, otp: str):
    return forgot_password_verify_otp_impl(email=email, otp=otp)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def forgot_password_reset(
    email: str,
    reset_token: str,
    new_password: str,
    confirm_password: str,
):
    return forgot_password_reset_impl(
        email=email,
        reset_token=reset_token,
        new_password=new_password,
        confirm_password=confirm_password,
    )


@frappe.whitelist(methods=["POST"])
def change_password(current_password: str, new_password: str, confirm_password: str):
    return change_password_impl(
        current_password=current_password,
        new_password=new_password,
        confirm_password=confirm_password,
    )

