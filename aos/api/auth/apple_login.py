import frappe

from aos.api.shared.account_status import get_account_state
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.validators import resolve_language
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .users import get_user_payload
from .apple_jwt import verify_apple_id_token


def _get_apple_bundle_id():
    """
    Read Apple Bundle ID from AOS Settings.
    Used for JWT audience validation.
    """
    try:
        settings = frappe.get_single("AOS Settings")
    except Exception:
        return ""

    return (getattr(settings, "apple_bundle_id", "") or "").strip()


def apple_login_impl(**kwargs):
    """
    Login/Register using Apple Sign-In.

    - Verifies Apple identity token
    - Creates user if not exists
    - Creates AOS Profile for new users
    - Ensures AOS User Preference exists
    - Creates session
    """

    id_token = (kwargs.get("id_token") or "").strip()

    if not id_token:
        return fail("Apple ID token is required.", code="VALIDATION_ERROR")

    # Rate limit by IP
    rl = rate_limit(
        key=f"aos:apple:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    bundle_id = _get_apple_bundle_id()

    if not bundle_id:
        return fail(
            "Apple Bundle ID not configured.",
            code="CONFIG_ERROR",
            http_status=500,
        )

    # Verify Apple Token
    try:
        claims = verify_apple_id_token(
            id_token=id_token,
            audience=bundle_id,
        )

    except ValueError as e:
        code = str(e) or "TOKEN_INVALID"

        if code == "TOKEN_EXPIRED":
            return fail("Apple token expired.", code="TOKEN_EXPIRED", http_status=401)

        if code in {"AUD_INVALID", "ISS_INVALID"}:
            return fail("Apple token not allowed.", code="TOKEN_INVALID", http_status=401)

        return fail("Invalid Apple token.", code="TOKEN_INVALID", http_status=401)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Token Verify Failed")
        return fail("Could not verify Apple token.", code="TOKEN_VERIFY_FAILED", http_status=500)

    email = (claims.get("email") or "").strip().lower()
    apple_sub = claims.get("sub")

    if not email:
        return fail("Email not provided by Apple.", code="EMAIL_MISSING", http_status=401)

    if not apple_sub:
        return fail("Apple subject missing.", code="TOKEN_INVALID", http_status=401)

    # Find or create user
    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if user_name:
        state = get_account_state(user_name)
        if state.get("is_deleted"):
            return fail(
                "This account was previously deleted. Please restore it instead.",
                code="ACCOUNT_DELETED_RESTORABLE",
                data={"can_restore": bool(state.get("can_restore"))},
                http_status=403,
            )

        enabled = frappe.db.get_value("User", user_name, "enabled")

        if int(enabled or 0) != 1:
            frappe.db.set_value("User", user_name, "enabled", 1)

    else:
        try:
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = email.split("@")[0]
            user.enabled = 1
            user.user_type = "Website User"
            user.send_welcome_email = 0
            user.insert(ignore_permissions=True)

            user_name = user.name

            profile = frappe.new_doc("AOS Profile")
            profile.user = user_name
            profile.account_status = "Active"
            profile.is_deleted = 0
            profile.insert(ignore_permissions=True)

        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Apple User Create Failed")

            return fail(
                "Could not create account.",
                code="USER_CREATE_FAILED",
                http_status=500,
            )

    # Ensure Preference Exists
    pref_exists = frappe.db.exists(
        "AOS User Preference",
        {"user": user_name},
    )

    if not pref_exists:

        # Market Context
        country_name, currency_code, err = resolve_market_context(
            country=kwargs.get("country"),
            currency=kwargs.get("currency"),
        )
        if err:
            return err

        # Language
        if kwargs.get("language"):
            language_name, err = resolve_language(kwargs.get("language"))
            if err:
                return err
        else:
            snap = get_aos_settings_snapshot()

            if not snap.default_language:
                return fail(
                    "Default language not configured.",
                    code="CONFIG_ERROR",
                )

            language_name = snap.default_language

        try:
            pref = frappe.new_doc("AOS User Preference")
            pref.user = user_name
            pref.country = country_name
            pref.currency = currency_code
            pref.language = language_name
            pref.insert(ignore_permissions=True)

        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Apple Pref Create Failed")

            return fail(
                "Failed to initialize user preference.",
                code="PREFERENCE_CREATE_FAILED",
                http_status=500,
            )

    # Create session
    try:
        lm = frappe.local.login_manager
        lm.login_as(user_name)

        sid = getattr(frappe.session, "sid", None)

        if not sid:
            return fail("Login failed.", code="LOGIN_FAILED", http_status=401)

        return ok(
            "Login successful.",
            data={
                "sid": sid,
                "user": get_user_payload(user_name),
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Login Failed")

        return fail(
            "Login failed.",
            code="LOGIN_FAILED",
            http_status=401,
        )
