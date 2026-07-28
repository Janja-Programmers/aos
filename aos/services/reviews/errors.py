"""Public-safe Reviews exceptions with stable machine codes."""

from __future__ import annotations


class ReviewError(Exception):
    code = "REVIEW_ERROR"
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


class ReviewValidationError(ReviewError, ValueError):
    code = "INVALID_REVIEW_REQUEST"
    http_status = 422


class ReviewNotFoundError(ReviewError, FileNotFoundError):
    code = "REVIEW_NOT_FOUND"
    http_status = 404


class ReviewPermissionError(ReviewError, PermissionError):
    code = "REVIEW_NOT_ALLOWED"
    http_status = 403


class ReviewConflictError(ReviewError):
    code = "REVIEW_ALREADY_EXISTS"
    http_status = 409


class ReviewStateError(ReviewError):
    code = "REVIEW_UPDATE_NOT_ALLOWED"
    http_status = 409
