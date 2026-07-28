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

Deleted-account restoration requires an explicit trusted path and must never occur as a side effect of Seller lookup or creation.
