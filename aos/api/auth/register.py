import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.market_context import (
    resolve_market_country,
    resolve_market_currency,
)
from aos.api.shared.validators import resolve_language
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import REGISTER_LIMIT_PER_HOUR_PER_IP
from .validators import normalize_email, normalize_name, validate_registration_inputs
from .verification import (
    compute_expiry,
    generate_otp,
    ensure_ver_doc,
    otp_hash,
    send_otp_email,
)


def register_impl(**kwargs):
    email = normalize_email(kwargs.get("email") or "")
    full_name = normalize_name(kwargs.get("full_name") or "")
    password = kwargs.get("password") or ""

    country = kwargs.get("country")
    language = kwargs.get("language")
    currency = kwargs.get("currency")

    # Rate limit by IP
    rl = rate_limit(
        key=f"aos:reg:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=REGISTER_LIMIT_PER_HOUR_PER_IP,
        message="Too many registration attempts. Please try again later.",
    )
    if rl:
        return rl

    # Validate inputs
    err = validate_registration_inputs(email, password, full_name)
    if err:
        return err

    # Prevent duplicate accounts
    if frappe.db.exists("User", {"email": email}):
        return fail("An account with this email already exists.", code="ALREADY_EXISTS")

    # Resolve preference values
    country_name, err = resolve_market_country(country)
    if err:
        return err

    currency_code, err = resolve_market_currency(currency)
    if err:
        return err

    if language:
        language_name, err = resolve_language(language)
        if err:
            return err
    else:
        settings = get_aos_settings_snapshot()
        if not settings.default_language:
            return fail(
                "Default language not configured.",
                code="CONFIG_ERROR",
            )
        language_name = settings.default_language

    try:
        # Create disabled user
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = full_name
        user.enabled = 0
        user.user_type = "Website User"
        user.send_welcome_email = 0
        user.insert(ignore_permissions=True)

        # Set password
        user.new_password = password
        user.flags.ignore_password_policy = True
        user.save(ignore_permissions=True)

        # Create User Preference
        pref = frappe.new_doc("AOS User Preference")
        pref.user = user.name
        pref.country = country_name
        pref.language = language_name
        pref.currency = currency_code
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
        return fail(
            "Registration failed. Please try again.",
            code="REGISTER_FAILED",
        )