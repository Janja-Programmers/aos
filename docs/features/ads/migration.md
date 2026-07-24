# Ads migration

`aos.patches.v1_0.harden_ads_subsystem` is appended to `aos/patches.txt`.

The patch is additive and repeatable. It adds public-feed, market, seller, location, price, expiry, child-row, draft, wishlist, and report indexes. It adds logical uniqueness for wishlist user/Ad, report reporter/Ad, Media-per-Ad, and attribute-per-Ad relationships.

Before adding unique constraints, exact logical duplicates are processed in bounded batches and the oldest row is retained deterministically. Child duplicates are removed directly from their allowlisted child tables. Parent user-action records use normal Frappe document deletion so hooks remain authoritative. Distinct business records are not merged.

Historical Catalog snapshots are backfilled from the current attribute master where available. Existing Ads receive lifecycle timestamps from their existing creation, modification, and review metadata. The patch tolerates absent tables and columns on partially upgraded sites and uses Frappe index APIs rather than raw schema DDL.

Run `bench --site <site> migrate` before Ads database tests. Always verify a recent restorable backup before applying the migration to a production site.
