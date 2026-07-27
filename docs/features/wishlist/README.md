# Wishlist

Wishlist is a private authenticated marketplace feature. Each logical
`(user, ad)` pair has one durable `AOS Wishlist` row whose status is either
`Active` or `Removed`. Re-adding restores the same row instead of creating a
second logical action.

## Production invariants

- The session user is always the owner; callers cannot supply another owner.
- Guests, disabled, deleted, suspended, or preference-less accounts cannot use
  Wishlist APIs.
- Sellers cannot wishlist their own ads.
- Adds require an active, unexpired public ad, an active seller, and no active
  block relationship.
- Explicit removal is idempotent and remains available after an ad becomes
  sold, expired, suspended, blocked, or otherwise unavailable.
- Database uniqueness on `(user, ad)` is the final concurrency guard.
- `saved_on` and `removed_on` capture lifecycle transitions.
- `AOS Ad.wishlist_count` is updated atomically by DocType hooks and repaired by
  migration/account-lifecycle reconciliation.
- Public discovery ranking refresh is queued after actual state transitions.

## Visibility

`list_wishlist` returns only ads that are currently public and viewable by the
current user. Unavailable items are omitted rather than exposing moderation,
seller-status, block, or deletion details. The private Wishlist row remains so
an explicit remove request can still clear it.

See [API](api.md), [architecture](architecture.md), [migration](migration.md),
[security](security.md), and [testing](testing.md).
