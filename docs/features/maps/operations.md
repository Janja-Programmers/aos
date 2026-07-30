# Maps operations

Production readiness requires current TileServer styles/tiles, a completed
Nominatim import and a built Valhalla tile set. Health checks treat Nominatim
and Valhalla as required. Photon is skipped unless explicitly enabled.

Monitor provider latency, HTTP failures, fallback frequency, route failures,
Redis availability and seller map-point query latency. Cache failures degrade
to provider calls and must not fail requests. Never log raw searches,
coordinates, resolved addresses or account identifiers.
