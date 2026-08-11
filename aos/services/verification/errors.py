"""Public-safe Verification domain exceptions."""

from __future__ import annotations


class VerificationError(Exception):
    code = "VERIFICATION_ERROR"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        data: dict | None = None,
    ):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)
        self.data = dict(data or {})


class VerificationValidationError(VerificationError, ValueError):
    code = "VERIFICATION_INVALID_REQUEST"
    http_status = 422


class VerificationPermissionError(VerificationError, PermissionError):
    code = "VERIFICATION_ACCESS_DENIED"
    http_status = 403


class VerificationNotFoundError(VerificationError, FileNotFoundError):
    code = "VERIFICATION_NOT_FOUND"
    http_status = 404


class VerificationConflictError(VerificationError):
    code = "VERIFICATION_CONFLICT"
    http_status = 409
