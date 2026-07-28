# Maps integration boundary

The existing endpoints remain available under Seller for client compatibility:

- `list_seller_map_points`
- `set_my_seller_location`
- `remove_my_seller_location`
- `get_seller_location`

They return opaque Seller IDs and respect Seller activity/account visibility. The Maps subsystem remains responsible for coordinate validation, coverage, reverse geocoding, viewport queries, route calculation, map precision, and clustering.

A later Maps hardening phase may introduce canonical Maps endpoints. Existing Seller endpoints should remain thin compatibility wrappers until all web/mobile clients migrate.
