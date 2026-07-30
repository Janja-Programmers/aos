# Maps API contract

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
