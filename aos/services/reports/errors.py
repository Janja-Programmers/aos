"""Internal Reports-domain exceptions with stable public error codes."""

from __future__ import annotations


class ReportError(Exception):
    code = "REPORT_INVALID_REQUEST"
    http_status = 422

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)


class ReportValidationError(ReportError, ValueError):
    pass


class ReportReasonError(ReportValidationError):
    code = "REPORT_INVALID_REASON"


class ReportReasonNotAllowedError(ReportValidationError):
    code = "REPORT_REASON_NOT_ALLOWED"


class ReportSelfError(ReportValidationError):
    code = "REPORT_SELF_NOT_ALLOWED"


class ReportPermissionError(ReportError, PermissionError):
    code = "REPORT_ACCESS_DENIED"
    http_status = 403


class ReportNotFoundError(ReportError, FileNotFoundError):
    code = "REPORT_INVALID_TARGET"
    http_status = 404


class ReportConflictError(ReportError):
    code = "REPORT_CONFLICT"
    http_status = 409
