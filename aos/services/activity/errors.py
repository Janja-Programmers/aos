"""Activity Center domain errors used by the public API boundary."""

from __future__ import annotations


class ActivityError(Exception):
    def __init__(self, message: str, *, code: str = "VALIDATION_ERROR", http_status: int = 422):
        super().__init__(message)
        self.message = str(message or "Invalid activity request.")
        self.code = code
        self.http_status = int(http_status)


class ActivityValidationError(ActivityError):
    pass


class ActivityNotFoundError(ActivityError):
    def __init__(self, message: str = "Activity not found."):
        super().__init__(message, code="NOT_FOUND", http_status=404)
