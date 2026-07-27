# Wishlist migration

`aos.patches.v1_0.harden_wishlist_subsystem` is a post-model-sync, repeatable
migration. It:

1. marks historical own-ad Wishlist rows Removed;
2. backfills `saved_on` and `removed_on`;
3. rebuilds `AOS Ad.wishlist_count` from active authoritative rows; and
4. creates `idx_aos_wishlist_user_status_saved` for owner listing and cursor
   pagination.

The existing Ads hardening migration remains responsible for deduplication and
the unique `(user, ad)` index. Running the Wishlist migration repeatedly does
not create duplicate indexes or change correct lifecycle values.
