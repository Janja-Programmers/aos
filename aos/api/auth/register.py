import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import resolve_country, resolve_language, resolve_currency
from .constants import REGISTER_LIMIT_PER_HOUR_PER_IP
from .validators import normalize_email, normalize_name, validate_registration_inputs
from .verification import compute_expiry, generate_otp, ensure_ver_doc, otp_hash, send_otp_email


def register_impl(email: str, password: str, full_name: str, country: str, language: str, currency: str):
    email = normalize_email(email)
    full_name = normalize_name(full_name)
    password = password or ""

    # Rate limit by IP
    rl = rate_limit(
        key=f"aos:reg:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=REGISTER_LIMIT_PER_HOUR_PER_IP,
        message="Too many registration attempts. Please try again later.",
    )
    if rl:
        return rl

    err = validate_registration_inputs(email, password, full_name)
    if err:
        return err

    if frappe.db.exists("User", {"email": email}):
        return fail("An account with this email already exists.", code="ALREADY_EXISTS")

    # Resolve preference values
    country_name, err = resolve_country(country)
    if err:
        return err

    language_name, err = resolve_language(language)
    if err:
        return err

    currency_code, err = resolve_currency(currency)
    if err:
        return err

    try:
        # Create disabled user
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": full_name,
                "enabled": 0,
                "user_type": "Website User",
                "send_welcome_email": 0,
            }
        )
        user.insert(ignore_permissions=True)

        # Set password
        user.new_password = password
        user.flags.ignore_password_policy = True
        user.save(ignore_permissions=True)

        # Create User Preference
        pref = frappe.get_doc(
            {
                "doctype": "AOS User Preference",
                "user": user.name,
                "country": country_name,
                "language": language_name,
                "currency": currency_code,
            }
        )
        pref.insert(ignore_permissions=True)

        # OTP record
        otp = generate_otp()
        expires_at = compute_expiry()

        ver = ensure_ver_doc(user.name, email=email, purpose="email_verification")
        ver.otp_hash = otp_hash(otp)
        ver.expires_at = expires_at
        ver.is_used = 0
        ver.attempts = 0
        ver.last_sent_at = frappe.utils.now_datetime()
        ver.reset_token_hash = ""
        ver.reset_token_expires_at = None
        ver.save(ignore_permissions=True)

        send_otp_email(
            email=email,
            otp=otp,
            full_name=full_name,
            purpose="email_verification",
        )

        frappe.db.commit()

        return ok("OTP sent to email. Please verify to activate account.")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Register Failed")
        frappe.db.rollback()
        return fail("Registration failed. Please try again.", code="REGISTER_FAILED")
