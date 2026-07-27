# Wishlist architecture

The public v1 module contains thin Frappe wrappers only. API modules handle
session validation, safe rate-limit keys, request normalization, and response
mapping. `WishlistService` owns add/remove eligibility and race-safe state
transitions. `AOSWishlist` owns immutable pair identity, lifecycle timestamps,
and counter hooks.

The unique database index on `(user, ad)` prevents duplicate logical actions.
Existing rows are locked with `FOR UPDATE`; concurrent first inserts recover
from the unique-key race and converge on the requested state.

`wishlist_count` is denormalized on `AOS Ad` for ranking/index documents. The
count is adjusted with an atomic SQL delta only when status crosses the Active
boundary. Bulk account deletion uses exact recomputation because direct bulk
SQL intentionally bypasses DocType hooks.

Listing is an authoritative SQL intersection between private active Wishlist
rows and currently public Ads. Explicit sort is primary; geography, seller
verification, saved time, and stable names are deterministic tie-breakers.
Recent cursor pagination uses `(saved_on, wishlist name)`.
