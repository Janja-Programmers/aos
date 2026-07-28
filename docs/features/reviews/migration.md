# Migration

Patch: `aos.patches.v1_0.harden_reviews_subsystem`.

The patch reloads Review, Review Image, Review Reaction and Review Report schemas; backfills lifecycle and moderation metadata; adds deterministic query indexes and database uniqueness; and rebuilds ad/seller aggregates.

Legacy duplicate reviews are not silently deleted. The oldest review is canonical. Later duplicates are marked `Withdrawn`, retain their records and receive a null review key. Duplicate reaction/report rows are user-action duplicates and are collapsed before their composite unique indexes are added.

The patch is idempotent, contains no commit, safely no-ops when tables/columns are unavailable, and is registered after existing production, Media, Accounts, Catalog, Ads and Wishlist patches.
