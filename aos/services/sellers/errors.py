"""Public-safe Seller exceptions with stable machine codes."""

from __future__ import annotations


class SellerError(Exception):
    code = "SELLER_ERROR"
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


class SellerValidationError(SellerError, ValueError):
    code = "INVALID_SELLER_REQUEST"
    http_status = 422


class SellerNotFoundError(SellerError, FileNotFoundError):
    code = "SELLER_NOT_FOUND"
    http_status = 404


class SellerPermissionError(SellerError, PermissionError):
    code = "SELLER_ACCESS_DENIED"
    http_status = 403


class SellerStateError(SellerError):
    code = "SELLER_INACTIVE"
    http_status = 403


class SellerConflictError(SellerError):
    code = "SELLER_VERSION_CONFLICT"
    http_status = 409
