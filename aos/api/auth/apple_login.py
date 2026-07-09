import frappe

from aos.api.shared.account_status import ensure_account_active, get_account_state
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok, fail
from aos.utils.aos_settings import get_apple_bundle_id

from .constants import APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .account_helpers import ensure_aos_profile, ensure_user_preference, safe_log_auth_event
from .serializers import serialize_auth_payload
from .validators import validate_client_type
from .apple_jwt import verify_apple_id_token


def _include_sid(client_type: str) -> bool:
    return client_type == "mobile"


def _get_apple_bundle_id():
    """Read Apple Bundle ID from the AOS Settings snapshot."""
    return get_apple_bundle_id()


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
        return fail("Apple ID token is required.", error="VALIDATION_ERROR")

    client_type, client_type_err = validate_client_type(kwargs.get("client_type"))
    if client_type_err:
        return client_type_err

    # Rate limit by IP
    rl = rate_limit(
        key=rate_limit_key("auth", "apple", "ip", request_ip()),
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
            error="CONFIG_ERROR",
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
            return fail("Apple token expired.", error="TOKEN_EXPIRED", http_status=401)

        if code in {"AUD_INVALID", "ISS_INVALID"}:
            return fail("Apple token not allowed.", error="TOKEN_INVALID", http_status=401)

        return fail("Invalid Apple token.", error="TOKEN_INVALID", http_status=401)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Token Verify Failed")
        return fail("Could not verify Apple token.", error="TOKEN_VERIFY_FAILED", http_status=500)

    email = (claims.get("email") or "").strip().lower()
    apple_sub = claims.get("sub")

    if not email:
        return fail("Email not provided by Apple.", error="EMAIL_MISSING", http_status=401)

    if not apple_sub:
        return fail("Apple subject missing.", error="TOKEN_INVALID", http_status=401)

    # Find or create user
    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if user_name:
        state = get_account_state(user_name)
        if state.get("is_deleted"):
            return fail(
                "This account was previously deleted. Please restore it instead.",
                error="ACCOUNT_DELETED_RESTORABLE",
                data={"can_restore": bool(state.get("can_restore"))},
                http_status=403,
            )

        active_err = ensure_account_active(user_name)
        if active_err:
            return active_err

        enabled = frappe.db.get_value("User", user_name, "enabled")
        if int(enabled or 0) != 1:
            safe_log_auth_event("AOS Social Login Disabled User", identifier=email, user=user_name, reason="disabled")
            return fail("Account disabled.", error="ACCOUNT_DISABLED", http_status=403)

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

            ensure_aos_profile(user_name)

        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Apple User Create Failed")

            return fail(
                "Could not create account.",
                error="USER_CREATE_FAILED",
                http_status=500,
            )

    # Ensure preference exists; repairs required AOS identity rows after token verification.
    pref, pref_err = ensure_user_preference(
        user_name,
        country=kwargs.get("country"),
        currency=kwargs.get("currency"),
        language=kwargs.get("language"),
    )
    if pref_err:
        return pref_err

    # Create session
    try:
        lm = frappe.local.login_manager
        lm.login_as(user_name)

        sid = getattr(frappe.session, "sid", None)

        if not sid:
            return fail("Login failed.", error="LOGIN_FAILED", http_status=401)

        return ok(
            "Login successful.",
            data=serialize_auth_payload(user_name, sid=sid, include_sid=_include_sid(client_type)),
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple Login Failed")

        return fail(
            "Login failed.",
            error="LOGIN_FAILED",
            http_status=401,
        )
