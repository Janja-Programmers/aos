"""Public-safe Ads exceptions with stable machine codes."""

from __future__ import annotations


class AdsError(Exception):
    code = "ADS_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = str(code or self.code)
        self.http_status = int(http_status or self.http_status)


class AdsValidationError(AdsError, ValueError):
    code = "INVALID_AD"
    http_status = 422


class AdsNotFoundError(AdsError, FileNotFoundError):
    code = "AD_NOT_FOUND"
    http_status = 404


class AdsPermissionError(AdsError, PermissionError):
    code = "AD_ACCESS_DENIED"
    http_status = 403


class AdsConflictError(AdsError):
    code = "INVALID_AD_STATE"
    http_status = 409


_PUBLIC_MESSAGES = {
    "AD_NOT_FOUND": "Ad not found.",
    "AD_ACCESS_DENIED": "Ad not found.",
    "INVALID_AD": "Invalid ad.",
    "INVALID_AD_INPUT": "Invalid ad input.",
    "VALIDATION_ERROR": "Invalid request.",
    "INVALID_AD_STATE": "The ad is not in a valid state for this action.",
    "INVALID_AD_CURSOR": "Invalid pagination cursor.",
    "DUPLICATE_AD_ATTRIBUTE": "Duplicate ad attribute.",
    "DUPLICATE_AD_MEDIA": "Duplicate ad media.",
    "AD_MEDIA_REQUIRED": "At least one ad image is required.",
    "AD_PRIMARY_IMAGE_REQUIRED": "Exactly one primary ad image is required.",
    "AD_PRICE_REQUIRED": "A valid price is required.",
    "AD_SELLER_INACTIVE": "Seller account is not active.",
}


def public_ads_message(error: AdsError, *, fallback: str = "Ads request failed.") -> str:
    return _PUBLIC_MESSAGES.get(str(error.code or ""), fallback)
