# Maps architecture

## Ownership

Maps owns coordinate validation, Kenya coverage, provider selection, reverse
geocoding, routing, viewport queries, clustering, cache keys and location
concurrency. Seller owns seller status, public Seller IDs and permission to
publish a storefront location.

The API implementation is split into thin endpoint modules, a safe API
boundary, strict validation, a Maps service and a repository. Seller location
modules are import-only compatibility wrappers.

## Providers

Nominatim is the production default because it is part of the maintained AOS
Compose stack. Photon is disabled unless `maps_photon_enabled` is explicitly
set and an approved immutable Photon image has been deployed. Valhalla is the
routing source of truth. Provider URLs must resolve to loopback, private IPs or
single-label internal service names.

## Transactions

Read operations do not create savepoints. Mutations lock the Seller row with
`FOR UPDATE`, use `location_version` optimistic concurrency and rely on the
outer Frappe request transaction for the final commit. Handled failures roll
back only the Maps operation and restore in-memory transaction callbacks.
