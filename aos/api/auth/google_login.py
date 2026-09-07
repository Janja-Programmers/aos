"""Google OIDC login endpoint implementation."""

from __future__ import annotations

from aos.utils.aos_settings import get_google_oauth_client_ids

from .constants import GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP
from .contracts import reject_unknown_fields
from .google_jwt import verify_google_id_token
from .social_login import social_login_impl


def google_login_impl(**kwargs):
    unknown = reject_unknown_fields(kwargs, {"id_token", "client_type", "country", "currency", "language"})
    if unknown:
        return unknown
    return social_login_impl(
        provider="google",
        verify_token=verify_google_id_token,
        audiences=get_google_oauth_client_ids(),
        request_kwargs=kwargs,
        ip_limit=GOOGLE_LOGIN_LIMIT_PER_HOUR_PER_IP,
    )
