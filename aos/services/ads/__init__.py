"""Ads/Listings domain services.

The Ads package owns listing lifecycle, input normalization, authorization, and
persistence-facing invariants. It deliberately delegates identity, seller
eligibility, Catalog schema, Media, localization, currency conversion,
notifications, and companion dispatch to their canonical shared services.
"""

from .errors import AdsError, AdsConflictError, AdsNotFoundError, AdsPermissionError, AdsValidationError
from .lifecycle import transition_for_action, validate_status_transition

__all__ = [
    "AdsConflictError",
    "AdsError",
    "AdsNotFoundError",
    "AdsPermissionError",
    "AdsValidationError",
    "transition_for_action",
    "validate_status_transition",
]
