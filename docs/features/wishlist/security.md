# Wishlist security

Wishlist endpoints require an active authenticated account with a configured
AOS preference. Rate-limit cache keys hash unsafe identifiers so user email
addresses are not stored in plaintext infrastructure keys.

Adds recheck current Ad status, expiry, seller status, self-ownership, and both
directions of active block relationships. Unavailable and blocked Ads use the
same public `AD_NOT_FOUND` response to avoid existence or moderation leaks.

Removal is owner-scoped and idempotent, including for stale unavailable Ads.
The API never accepts a user/owner field. DocType validation prevents ownership
changes and Guest ownership even through internal or Desk writes.

List queries allowlist every filter and sort, parameterize values, escape LIKE
wildcards, bound page sizes and offsets, validate opaque cursors, and use only
fixed SQL fragments selected by validated enums.
