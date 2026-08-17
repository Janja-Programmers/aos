# Saved Search

Saved Search owns a user's reusable Ads-search criteria. Searches are private, user-scoped, deduplicated by a server fingerprint, bounded per user, and soft-deleted.

Start with:

- [API](api.md) — save/list/delete contract and pagination.
- [Ads](../ads/README.md) — search criteria are Ads-domain filters; Saved Search stores the user's reusable parameter object rather than duplicating Ads query logic.
