# Maps API contract

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `autocomplete_places` | GET/POST | Guest allowed | Client |
| `get_route` | POST | Session required | Client |
| `refresh_route` | POST | Session required | Client |
| `reverse_geocode` | GET/POST | Guest allowed | Client |
| `search_places` | GET/POST | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

All endpoints return the standard AOS `{ok,message,data}` or
`{ok,message,error,data}` envelope. Framework transport fields such as `cmd`
are removed by v1 wrappers before strict validation.

## Geocoding

Search accepts `query`, bounded `limit`, optional language and Kenya country
codes. Autocomplete additionally accepts an optional coordinate bias. Reverse
geocoding accepts one coordinate pair. Structured scalar inputs, conflicting
aliases, unknown fields and out-of-coverage coordinates are rejected.

## Routing

`get_route` accepts two to ten route points, origin/destination coordinates, or
an origin plus canonical public `destination_seller`. `refresh_route` requires
a current coordinate and Seller destination. Routing requires login and
supports `auto`, `bicycle` and `pedestrian` costing.

## Seller locations

Location updates accept latitude, longitude, optional public name and
instructions, plus optional `expected_version`. Address, locality, region and
country are reverse-geocoded server-side. Responses include
`location_version`, `changed` and the normalized location. Identical retries
are idempotent; stale non-identical updates return
`MAP_LOCATION_VERSION_CONFLICT`.

Map points require north/south/east/west bounds and a bounded zoom. Low zooms
or large result sets return deterministic clusters; higher zooms return public
Seller pins.
