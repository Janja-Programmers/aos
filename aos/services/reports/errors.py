"""Internal Report-domain exceptions with safe public messages."""

from __future__ import annotations


class ReportError(Exception):
    code = "VALIDATION_ERROR"
    http_status = 422

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)


class ReportValidationError(ReportError, ValueError):
    pass


class ReportPermissionError(ReportError, PermissionError):
    code = "PERMISSION_DENIED"
    http_status = 403


class ReportNotFoundError(ReportError, FileNotFoundError):
    code = "NOT_FOUND"
    http_status = 404


class ReportConflictError(ReportError):
    code = "DUPLICATE"
    http_status = 409
