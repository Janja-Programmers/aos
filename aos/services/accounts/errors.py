"""Public-safe Accounts exceptions with stable machine codes."""

from __future__ import annotations


class AccountError(Exception):
    code = "ACCOUNT_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)


class AccountValidationError(AccountError, ValueError):
    code = "INVALID_PROFILE_FIELD"
    http_status = 422


class AccountNotFoundError(AccountError, FileNotFoundError):
    code = "ACCOUNT_NOT_FOUND"
    http_status = 404


class AccountConflictError(AccountError):
    code = "INVALID_STATE"
    http_status = 409
