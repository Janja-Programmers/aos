"""Public-safe Social exceptions with stable machine codes."""

from __future__ import annotations


class SocialError(Exception):
    code = "SOCIAL_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None, data: dict | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)
        self.data = dict(data or {})


class SocialValidationError(SocialError, ValueError):
    code = "SOCIAL_INVALID_REQUEST"
    http_status = 422


class SocialNotFoundError(SocialError, FileNotFoundError):
    code = "SOCIAL_PROFILE_UNAVAILABLE"
    http_status = 404


class SocialPermissionError(SocialError, PermissionError):
    code = "SOCIAL_ACTION_NOT_ALLOWED"
    http_status = 403


class SocialConflictError(SocialError):
    code = "SOCIAL_CONFLICT"
    http_status = 409


class SocialCursorError(SocialValidationError):
    code = "SOCIAL_INVALID_CURSOR"
