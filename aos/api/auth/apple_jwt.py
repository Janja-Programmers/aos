import base64
import json
import time
from typing import Any, Dict

import frappe

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
except Exception:  # pragma: no cover
    rsa = None  # type: ignore
    padding = None  # type: ignore
    hashes = None  # type: ignore


APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


def _string_claim(payload: dict, key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _b64url_decode(data: str) -> bytes:
    data = data.strip()
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _get_jwks_cached() -> Dict[str, Any]:
    cache = frappe.cache()
    key = "aos:apple:jwks"

    cached = cache.get_value(key)
    if cached and isinstance(cached, dict) and cached.get("keys"):
        return cached

    if not requests:
        raise ValueError("REQUESTS_NOT_AVAILABLE")

    try:
        resp = requests.get(APPLE_JWKS_URL, timeout=10)
        resp.raise_for_status()

        jwks = resp.json()

        if not isinstance(jwks, dict) or "keys" not in jwks:
            raise ValueError("JWKS_INVALID")

        cache.set_value(key, jwks, expires_in_sec=60 * 60)

        return jwks

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Apple JWKS Fetch Failed")
        raise ValueError("JWKS_FETCH_FAILED")


def _rsa_key_from_jwk(jwk: Dict[str, Any]):
    if rsa is None:
        raise ValueError("CRYPTO_NOT_AVAILABLE")

    n = jwk.get("n")
    e = jwk.get("e")

    if not n or not e:
        raise ValueError("JWK_MISSING_PARAMS")

    n_int = int.from_bytes(_b64url_decode(n), "big")
    e_int = int.from_bytes(_b64url_decode(e), "big")

    public_numbers = rsa.RSAPublicNumbers(e_int, n_int)
    return public_numbers.public_key()


def verify_apple_id_token(id_token: str, audiences: list[str]) -> Dict[str, Any]:
    """
    Verify an Apple identity token (JWT).

    Returns the decoded claims dict on success.

    Raises ValueError with short codes on failure.
    """

    if not isinstance(id_token, str):
        raise ValueError("TOKEN_MISSING")
    id_token = id_token.strip()
    if not id_token:
        raise ValueError("TOKEN_MISSING")

    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("TOKEN_FORMAT_INVALID")

    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        raise ValueError("TOKEN_DECODE_FAILED")

    alg = _string_claim(header, "alg")
    kid = _string_claim(header, "kid")

    if alg != "RS256" or not kid:
        raise ValueError("TOKEN_HEADER_INVALID")

    jwks = _get_jwks_cached()
    keys = jwks.get("keys") or []

    jwk = next((k for k in keys if k.get("kid") == kid), None)

    if not jwk:
        # refresh cache once
        frappe.cache().delete_key("aos:apple:jwks")

        jwks = _get_jwks_cached()
        keys = jwks.get("keys") or []
        jwk = next((k for k in keys if k.get("kid") == kid), None)

        if not jwk:
            raise ValueError("KID_NOT_FOUND")

    # Verify signature
    try:
        public_key = _rsa_key_from_jwk(jwk)

        signing_input = (header_b64 + "." + payload_b64).encode("utf-8")
        signature = _b64url_decode(sig_b64)

        public_key.verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    except ValueError:
        raise
    except Exception:
        raise ValueError("SIGNATURE_INVALID")

    # Validate claims
    iss = _string_claim(payload, "iss")
    aud = payload.get("aud")
    exp = payload.get("exp")

    if iss != APPLE_ISSUER:
        raise ValueError("ISS_INVALID")

    allowed_audiences = {
        item.strip()
        for item in audiences
        if isinstance(item, str) and item.strip()
    }
    if not allowed_audiences:
        raise ValueError("AUDIENCE_NOT_CONFIGURED")

    if aud not in allowed_audiences:
        raise ValueError("AUD_INVALID")

    try:
        now = int(time.time())
        exp_int = int(exp)

        if now >= exp_int:
            raise ValueError("TOKEN_EXPIRED")

    except ValueError:
        raise
    except Exception:
        raise ValueError("EXP_INVALID")

    return payload
