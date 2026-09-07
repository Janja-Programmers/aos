"""Apple identity-token verification."""

from __future__ import annotations

from .oidc import verify_rs256_token

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


def verify_apple_id_token(id_token: str, audiences: list[str]):
    claims = verify_rs256_token(
        id_token,
        audiences=audiences,
        issuer=APPLE_ISSUER,
        jwks_url=APPLE_JWKS_URL,
        cache_key="aos:auth:oidc:apple:jwks",
    )
    # Apple may omit email on later authorizations; when it is present, only a
    # provider-verified email may be used to create/link an AOS account.
    if claims.get("email") and str(claims.get("email_verified")).lower() not in {"true", "1"}:
        from .oidc import OIDCTokenError

        raise OIDCTokenError("EMAIL_NOT_VERIFIED")
    return claims
