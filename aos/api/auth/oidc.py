"""Shared OIDC/JWKS verification using PyJWT from the Frappe runtime."""

from __future__ import annotations

import json
from typing import Any

import frappe
import jwt
import requests
from jwt.algorithms import RSAAlgorithm


class OIDCTokenError(ValueError):
    def __init__(self, code: str):
        self.code = str(code or "TOKEN_INVALID")
        super().__init__(self.code)


class OIDCDependencyError(RuntimeError):
    pass


def _load_jwks(*, cache_key: str, url: str, force_refresh: bool = False) -> dict[str, Any]:
    cache = frappe.cache()
    if not force_refresh:
        cached = cache.get_value(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("keys"), list):
            return cached
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        jwks = response.json()
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ValueError("invalid JWKS payload")
        cache.set_value(cache_key, jwks, expires_in_sec=3600)
        return jwks
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS OIDC JWKS Fetch Failed", exc, operation="oidc_jwks_fetch")
        raise OIDCDependencyError("OIDC keys unavailable") from exc


def verify_rs256_token(
    token: str,
    *,
    audiences: list[str],
    issuer: str | set[str],
    jwks_url: str,
    cache_key: str,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise OIDCTokenError("TOKEN_MISSING")
    allowed = [value.strip() for value in audiences if isinstance(value, str) and value.strip()]
    if not allowed:
        raise OIDCDependencyError("AUDIENCE_NOT_CONFIGURED")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise OIDCTokenError("TOKEN_HEADER_INVALID")
    except OIDCTokenError:
        raise
    except Exception as exc:
        raise OIDCTokenError("TOKEN_INVALID") from exc

    def find_key(jwks: dict[str, Any]):
        return next((item for item in jwks.get("keys", []) if item.get("kid") == header["kid"]), None)

    jwks = _load_jwks(cache_key=cache_key, url=jwks_url)
    jwk = find_key(jwks)
    if not jwk:
        # Unknown-kid tokens must not turn into an outbound-JWKS refresh storm.
        # One application node per site/provider may refresh within this short
        # window; all other nodes re-read the shared cache.
        cache = frappe.cache()
        try:
            refresh_key = cache.make_key(f"{cache_key}:refresh-guard")
            may_refresh = bool(cache.set(refresh_key, "1", ex=30, nx=True))
        except Exception as exc:
            raise OIDCDependencyError("OIDC cache unavailable") from exc
        if may_refresh:
            jwks = _load_jwks(cache_key=cache_key, url=jwks_url, force_refresh=True)
        else:
            jwks = _load_jwks(cache_key=cache_key, url=jwks_url)
        jwk = find_key(jwks)
    if not jwk:
        raise OIDCTokenError("KID_NOT_FOUND")
    try:
        key = RSAAlgorithm.from_jwk(json.dumps(jwk))
    except Exception as exc:
        raise OIDCDependencyError("OIDC key invalid") from exc

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=allowed,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise OIDCTokenError("TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise OIDCTokenError("TOKEN_INVALID") from exc

    issuers = {issuer} if isinstance(issuer, str) else set(issuer)
    if str(claims.get("iss") or "") not in issuers:
        raise OIDCTokenError("TOKEN_INVALID")
    if not str(claims.get("sub") or "").strip():
        raise OIDCTokenError("TOKEN_INVALID")
    return claims
