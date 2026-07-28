# Storefront updates

The storefront supports a business category, plain-text description, Media-backed banner, and seven-day operating hours.

Validation includes Unicode normalisation, bounded lengths, control/null/invisible-character rejection, unsafe markup rejection, strict operating-day uniqueness, strict booleans, valid times, and `open_time < close_time`.

Updates use a Seller row lock and optional `expected_version`. A stale version returns `SELLER_VERSION_CONFLICT`. Identical retries are idempotent and do not increment `storefront_version`.

The client cannot update Seller status, type, owner, rating, counts, response metrics, verification, or location through this endpoint.
