import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.validators import resolve_language
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .users import get_user_payload
from .google_jwt import verify_google_id_token


def _get_google_client_ids():
    """
    Read Google OAuth Client IDs from AOS Settings.
    Required for audience validation.
    """
    try:
        settings = frappe.get_single("AOS Settings")
    except Exception:
        return []

    raw = getattr(settings, "google_oauth_client_ids", "") or ""
    text = raw.strip()

    client_ids = []
    for line in text.replace(",", "\n").splitlines():
        v = (line or "").strip()
        if v:
            client_ids.append(v)

    return client_ids


def google_login_impl(**kwargs):
    """
    Login/Register using Google Sign-In (ID Token).

    - Validates audience using settings client IDs
    - Creates AOS Profile for new users
    - Creates AOS User Preference if missing
    - Country + Currency fallback to AOS defaults
    - Language fallback to AOS default
    """

    id_token = (kwargs.get("id_token") or "").strip()

    if not id_token:
        return fail("Google ID token is required.", code="VALIDATION_ERROR")

    # Rate limit by IP
    rl = rate_limit(
        key=f"aos:google:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
        message="Too many attempts. Please try again later.",
    )
    if rl:
        return rl

    # Load allowed audiences
    allowed_audiences = _get_google_client_ids()
    if not allowed_audiences:
        return fail(
            "Google OAuth client IDs not configured.",
            code="CONFIG_ERROR",
            http_status=500,
        )

    # Verify Google Token
    try:
        claims = verify_google_id_token(
            id_token=id_token,
            allowed_audiences=allowed_audiences,
        )

    except ValueError as e:
        code = str(e) or "TOKEN_INVALID"

        if code == "TOKEN_EXPIRED":
            return fail("Google token expired.", code="TOKEN_EXPIRED", http_status=401)

        if code in {"AUD_INVALID", "ISS_INVALID"}:
            return fail("Google token not allowed.", code="TOKEN_INVALID", http_status=401)

        if code == "EMAIL_NOT_VERIFIED":
            return fail("Google email not verified.", code="EMAIL_NOT_VERIFIED", http_status=401)

        if code == "AUDIENCE_NOT_CONFIGURED":
            return fail("Google audience not configured.", code="CONFIG_ERROR", http_status=500)

        return fail("Invalid Google token.", code="TOKEN_INVALID", http_status=401)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Google Token Verify Failed")
        return fail("Could not verify Google token.", code="TOKEN_VERIFY_FAILED", http_status=500)

    email = (claims.get("email") or "").strip().lower()
    full_name = (claims.get("name") or claims.get("given_name") or "").strip()

    if not email:
        return fail("Google account email missing.", code="TOKEN_INVALID")

    # Find or create user
    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if user_name:
        enabled = frappe.db.get_value("User", user_name, "enabled")
        if int(enabled or 0) != 1:
            frappe.db.set_value("User", user_name, "enabled", 1)

    else:
        try:
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = full_name or email.split("@")[0]
            user.enabled = 1
            user.user_type = "Website User"
            user.send_welcome_email = 0
            user.insert(ignore_permissions=True)

            user_name = user.name

            profile = frappe.new_doc("AOS Profile")
            profile.user = user_name
            profile.insert(ignore_permissions=True)

        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Google User Create Failed")
            return fail("Could not create account.", code="USER_CREATE_FAILED", http_status=500)

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
            frappe.log_error(frappe.get_traceback(), "AOS Google Pref Create Failed")
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
        frappe.log_error(frappe.get_traceback(), "AOS Google Login Failed")
        return fail("Login failed.", code="LOGIN_FAILED", http_status=401)
