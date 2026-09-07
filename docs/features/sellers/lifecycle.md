# Seller lifecycle

Statuses are `Active`, `Suspended`, and `Deleted`.

- **Active:** publicly discoverable; may post ads, update the storefront, and manage location.
- **Suspended:** hidden from public Seller discovery and cannot post ads or mutate the storefront. Historical legitimate reviews remain intact.
- **Deleted:** not public and cannot be silently reactivated.

Every transition uses `set_seller_status()` under a row lock and records `status_changed_at`, bounded `status_reason_code`, and `status_source`. Direct status mutation is rejected by the DocType controller unless invoked by the lifecycle service or an authorised administrator.

Allowed transitions:

- Active → Suspended or Deleted
- Suspended → Active or Deleted
- Deleted → no normal transition

Recoverable account deletion no longer changes the Seller row. Accounts hides the storefront through the owning User/Profile tombstone, so an `Active` seller remains stored as `Active` and a `Suspended` seller remains stored as `Suspended`. Restoring within 30 days therefore requires no Seller status rewrite. Manual/moderation Seller deletion remains a separate Seller lifecycle and is never reactivated by account restore.
