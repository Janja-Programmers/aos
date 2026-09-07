"""Apple OIDC login endpoint implementation."""

from __future__ import annotations

from aos.utils.aos_settings import get_apple_oauth_client_ids

from .apple_jwt import verify_apple_id_token
from .constants import APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .contracts import reject_unknown_fields
from .social_login import social_login_impl


def apple_login_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"id_token", "client_type", "country", "currency", "language"})
    if unknown:
        return unknown
    return social_login_impl(
        provider="apple",
        verify_token=verify_apple_id_token,
        audiences=get_apple_oauth_client_ids(),
        request_kwargs=kwargs,
        ip_limit=APPLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
    )
