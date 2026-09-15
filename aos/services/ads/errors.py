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
    "INVALID_WISHLIST_CURSOR": "Invalid wishlist pagination cursor.",
    "INVALID_WISHLIST_REQUEST": "Invalid wishlist request.",
    "OWN_AD_WISHLIST_FORBIDDEN": "You cannot add your own ad to your wishlist.",
    "DUPLICATE_AD_ATTRIBUTE": "Duplicate ad attribute.",
    "DUPLICATE_AD_MEDIA": "Duplicate ad media.",
    "AD_MEDIA_REQUIRED": "At least one ad image is required.",
    "AD_PRIMARY_IMAGE_REQUIRED": "Exactly one primary ad image is required.",
    "AD_PRICE_REQUIRED": "A valid price is required.",
    "AD_SELLER_INACTIVE": "Seller account is not active.",
    "AD_CONFLICT": "The ad changed since it was loaded.",
    "SEARCH_INVALID_FILTERS": "Invalid search filters.",
    "SEARCH_UNAVAILABLE": "Search is temporarily unavailable.",
    "IMAGE_SEARCH_UNAVAILABLE": "Image search is temporarily unavailable.",
    "SAVED_SEARCH_NOT_FOUND": "Saved search not found.",
    "SAVED_SEARCH_CONFLICT": "Saved search changed or already exists.",
    "SAVED_SEARCH_LIMIT_REACHED": "Saved search limit reached.",
    "FX_RATE_UNAVAILABLE": "Exchange-rate data is unavailable.",
}


def public_ads_message(error: AdsError, *, fallback: str = "Ads request failed.") -> str:
    return _PUBLIC_MESSAGES.get(str(error.code or ""), fallback)
