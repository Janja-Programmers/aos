# Maps

Maps is the canonical AOS domain for geocoding, reverse geocoding, routing and
seller map publication. Seller remains the owner of storefront identity and
lifecycle. The existing Seller location endpoints remain stable compatibility
contracts and delegate to Maps.

## Public API surface

- `aos.api.v1.maps.autocomplete_places`
- `aos.api.v1.maps.search_places`
- `aos.api.v1.maps.reverse_geocode`
- `aos.api.v1.maps.get_route`
- `aos.api.v1.maps.refresh_route`
- `aos.api.v1.sellers.list_seller_map_points`
- `aos.api.v1.sellers.get_seller_location`
- `aos.api.v1.sellers.set_my_seller_location`
- `aos.api.v1.sellers.remove_my_seller_location`

Advanced storefront editing remains outside this phase. Maps supplies the
location contract consumed by future web and mobile storefront editors.
