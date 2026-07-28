"""Legacy Accounts compatibility services.

Seller creation is owned by the Seller domain. This wrapper remains for
existing Ads and Verification imports until their next versioned migration.
"""

from aos.services.sellers.policy import get_or_create_seller

__all__ = ["get_or_create_seller"]
