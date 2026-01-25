import frappe

from .constants import GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .rate_limit import rate_limit, request_ip
from .responses import fail, ok
from .users import get_user_payload

from .google_jwt import verify_google_id_token


def _get_google_settings():
    """Read Google Sign-In settings from AOS Settings (Single)."""
    try:
        settings = frappe.get_single("AOS Settings")
    except Exception:
        return {
            "enabled": False,
            "client_ids": [],
            "auto_enable": True,
        }

    enabled = int(getattr(settings, "enable_google_sign_in", 0) or 0) == 1

    raw = getattr(settings, "google_oauth_client_ids", None) or getattr(
        settings, "google_oauth_client_ids", ""
    )
    # You said you stored as lines; support comma too.
    text = (raw or "").strip()
    client_ids = []
    for line in text.replace(",", "\n").splitlines():
        v = (line or "").strip()
        if v:
            client_ids.append(v)

    auto_enable = int(getattr(settings, "auto_enable_google_users", 1) or 0) == 1

    return {
        "enabled": enabled,
        "client_ids": client_ids,
        "auto_enable": auto_enable,
    }


def google_login_impl(id_token: str):
    """Login/Register using Google Sign-In (ID Token)."""
    # Rate limit by IP
    rl = rate_limit(
        key=f"aos:google:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    st = _get_google_settings()
    if not st["enabled"]:
        return fail("Google Sign-In is not enabled.", code="NOT_ALLOWED", http_status=403)

    # Verify token
    try:
        claims = verify_google_id_token(id_token=id_token, allowed_audiences=st["client_ids"])
    except ValueError as e:
        code = str(e) or "TOKEN_INVALID"
        # map to friendly errors
        if code in {"TOKEN_EXPIRED"}:
            return fail("Google token expired. Please try again.", code="TOKEN_EXPIRED", http_status=401)
        if code in {"AUD_INVALID", "ISS_INVALID"}:
            return fail("Google token not allowed.", code="TOKEN_INVALID", http_status=401)
        if code in {"EMAIL_NOT_VERIFIED"}:
            return fail("Google email not verified.", code="EMAIL_NOT_VERIFIED", http_status=401)
        return fail("Invalid Google token.", code="TOKEN_INVALID", http_status=401)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Google Token Verify Failed")
        return fail("Could not verify Google token.", code="TOKEN_VERIFY_FAILED", http_status=500)

    email = (claims.get("email") or "").strip().lower()
    full_name = (claims.get("name") or claims.get("given_name") or "").strip()

    # Find or create user
    user_name = frappe.db.get_value("User", {"email": email}, "name")
    if user_name:
        enabled = frappe.db.get_value("User", user_name, "enabled")
        if int(enabled or 0) != 1:
            if st["auto_enable"]:
                frappe.db.set_value("User", user_name, "enabled", 1)
            else:
                return fail("Account disabled.", code="ACCOUNT_DISABLED", http_status=403)
    else:
        try:
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": full_name or email.split("@")[0],
                    "enabled": 1 if st["auto_enable"] else 0,
                    "user_type": "Website User",
                    "send_welcome_email": 0,
                }
            )
            user.insert(ignore_permissions=True)
            user_name = user.name
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Google User Create Failed")
            return fail("Could not create account.", code="USER_CREATE_FAILED", http_status=500)

    # Create session
    try:
        lm = frappe.local.login_manager
        # login_as triggers post_login internally in most Frappe versions
        lm.login_as(user_name)
        sid = getattr(frappe.session, "sid", None)
        if not sid:
            return fail("Login failed. Please try again.", code="LOGIN_FAILED", http_status=401)

        return ok(
            "Login successful.",
            data={
                "sid": sid,
                "user": get_user_payload(user_name),
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Google Login Failed")
        return fail("Login failed. Please try again.", code="LOGIN_FAILED", http_status=401)
