"""Google ID-token verification."""

from __future__ import annotations

from .oidc import verify_rs256_token

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_google_id_token(id_token: str, allowed_audiences: list[str]):
    claims = verify_rs256_token(
        id_token,
        audiences=allowed_audiences,
        issuer=GOOGLE_ISSUERS,
        jwks_url=GOOGLE_JWKS_URL,
        cache_key="aos:auth:oidc:google:jwks",
    )
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        from .oidc import OIDCTokenError
        raise OIDCTokenError("EMAIL_MISSING")
    if str(claims.get("email_verified")).lower() not in {"true", "1"}:
        from .oidc import OIDCTokenError
        raise OIDCTokenError("EMAIL_NOT_VERIFIED")
    return claims
