# Maps

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `autocomplete_places` | GET | Guest allowed | Client |
| `get_route` | POST | Session required | Client |
| `refresh_route` | POST | Session required | Client |
| `reverse_geocode` | GET | Guest allowed | Client |
| `search_places` | GET | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Feature overview

Maps owns the provider-neutral global place-search/reverse-geocoding contract, WGS84 coordinate validation, routing boundary, seller-location geospatial primitives already used by the existing product, Maps caching/limits/failure semantics, and the operational basemap/geocoder infrastructure in this repository. It does not own account identity, localization reference data, social relationships/capabilities, seller lifecycle, catalog data, or device geolocation permission state.

The production design is global. No country, marketplace, Localization country, or seller market limits map coverage. Localization may bias language/country ranking only when the client deliberately supplies that context.

## Global map behavior

The basemap covers the world. A client may pan, zoom, inspect and search globally. Search accepts an optional two-letter `country_code` filter, but no country filter is applied by default. Coordinates use WGS84 / EPSG:4326. Named API fields are always `latitude` then `longitude`; GeoJSON/provider geometry follows RFC 7946 order `[longitude, latitude]`. Latitude is `[-90, 90]`, longitude is `[-180, 180]`, and AOS stores explicit seller coordinates at seven decimal places.

The web client is intentionally not changed in this backend phase. Its next Maps phase should use MapLibre GL JS with the versioned PMTiles basemap. Initial camera fallback should be: device location when permission is granted, otherwise existing Localization context when it can provide a useful coarse camera hint, otherwise a neutral global viewport. No Nairobi/Kenya fallback is part of the Maps contract.

## Device location and precise-location privacy

Browser/native geolocation is a client runtime concern. AOS does not request, continuously track, or automatically persist device GPS coordinates. The web phase should request location only from a secure origin and only when needed for current-location UX.

Precise coordinates remain client-side for simple centering/recentering. They are sent to AOS only when the user invokes a server operation that needs them: optional autocomplete bias, explicit reverse geocoding, routing, or an explicit seller-location mutation. Autocomplete/search/reverse/routing request coordinates are not persisted by Maps. They are excluded from Maps structured logs; cache keys use one-way SHA-256 digests rather than embedding queries or coordinates.

The existing explicit seller-location workflow persists the selected coordinates and normalized address snapshot on `AOS Seller`. That data is Seller-owned, requires the existing Seller authorization path, and remains until replaced, removed, or the owning Seller record is deleted. Maps does not silently copy current device GPS into Accounts or user profile data.

## Architecture

### Rendering

The intended web renderer is MapLibre GL JS. Rendering stays client-side and does not depend on Photon/OpenSearch internals.

### Basemap

The production path is:

`dated OpenStreetMap planet PBF -> offline Planetiler build -> versioned PMTiles -> S3-compatible object storage -> CDN -> MapLibre clients`

`infra/maps/scripts/download-planet.sh` validates a pinned snapshot SHA-256. `build-world-pmtiles.sh` runs Planetiler as an offline resource-isolated job and atomically publishes the local artifact only after PMTiles validation. `publish-basemap.py` uploads a versioned immutable object first and writes `basemap/current.json` last, so publication is atomic from the client/CDN perspective. Old object versions provide rollback.

AOS request/API containers never generate planet tiles. The supplied Nginx Maps origin only exposes `/basemap/*`, supports byte ranges needed by PMTiles, and proxies the private MinIO/object-storage endpoint. That object-origin location deliberately does not inherit the generic application proxy-header snippet: it sends one loopback MinIO `Host`, strips client `Authorization`, does not forward the browser-facing `X-Forwarded-*` chain, and publishes one deterministic `Accept-Ranges: bytes` response header. This prevents malformed duplicate-host S3 requests while preserving PMTiles range semantics. Production should put a globally distributed CDN in front of that origin. Public OpenStreetMap community tile servers are not an AOS production dependency.

Planetiler/OpenMapTiles-compatible vector content is chosen because world basemap traffic becomes static and CDN-cacheable. Martin/PostGIS are deliberately not introduced for the static basemap. They should be added only if future AOS-owned dynamic spatial layers require server-generated vector tiles/spatial queries.

### Geocoding/search

The canonical geocoder is self-hosted Photon. AOS owns the public API and normalizes Photon GeoJSON into a stable place model; clients never call Photon/OpenSearch directly and never receive Photon/OpenSearch hostnames or raw documents.

Photon runs as stateless application replicas against an external OpenSearch 3.8.0 cluster (the reviewed production pin for this hardening pass). The repository pins Photon 1.2.0 with a verified JAR SHA-256 and a digest-pinned JRE base image. Photon 1.2.1 existed when this hardening pass was performed, but its release artifact checksum was not independently available in this offline packaging environment; 1.2.0 remains the reproducible pin until operators explicitly verify and update the JAR checksum.

Nominatim is not a second primary geocoder. It may be enabled as an operator-controlled fallback and/or maintained as an import/update dependency. It is disabled as runtime fallback by default.

### Routing

The existing authenticated route boundary is retained because it is already part of Maps. Valhalla is the preferred open-source engine and is required for a production-ready Maps deployment. The `maps-routing` Compose profile exists only to keep the private routing runtime operationally separable from unrelated services; production configuration validation requires `maps_routing_enabled=1` and a safe private `valhalla_base_url`. Routing storage is never queried directly by future Sellers/Ads/Search code. A temporary routing outage must fail only the route operation with the stable dependency error while basemap/geocoding continue to work.

Global routing is built from the same checksum-pinned single OSM planet snapshot used by the Maps data release. `build-valhalla.sh` writes an immutable `maps/valhalla/releases/<MAP_DATA_VERSION>` graph release and never builds inside the serving container. `verify-valhalla.sh` verifies the indexed tile extract, admin/timezone databases and release checksums. `activate-valhalla.sh` atomically changes `maps/valhalla/current`, so rollback is a pointer switch plus a serving-container recreate. The serving container mounts only `current` read-only with all build flags disabled. `smoke-valhalla-global.py` validates representative routes across Africa, Europe, Asia, North America, South America and Oceania and exercises `auto`, `pedestrian` and `bicycle` costing.

### Dynamic AOS geospatial data

Current dynamic data is the explicit seller location already stored on `AOS Seller`: `latitude`, `longitude`, `has_location`, normalized display/locality/region/country metadata, location text, timestamp and optimistic `location_version`. Composite indexes used for viewport/nearby lookup are installed by the current idempotent `aos.services.sellers.schema` migration invariant. No Maps-owned PostGIS database is justified today.

`AOS Location`/Localization country reference data is not Maps-owned and is not duplicated.

`MAPS_OBJECT_STORAGE_ALLOW_ANONYMOUS_READ` defaults to `false`. Enable it only for an intentionally anonymous range origin, such as loopback-only MinIO behind the AOS Maps Nginx/CDN path. Production object storage should otherwise remain private and use the object-store/CDN origin-access mechanism.

## Dependencies and ownership boundaries

Authentication supplies the canonical Frappe/AOS session boundary. Guest access is intentional only for global autocomplete/search/reverse geocoding; route endpoints require a session. Maps introduces no token/session system.

Localization owns countries/languages/marketplace locale data. Maps accepts language/country bias but never turns preferred country into map bounds.

Accounts owns public account identity (`ACC-*`) and profile data. Device GPS is never silently written to Accounts. Social owns relationship/block/visibility/capability state; Maps must consume those capabilities for future user-owned layers rather than rebuilding them. Catalog remains the owner of catalog/category data.

Sellers is the next hardening pass. Maps currently preserves the smallest existing seller-location integration: seller coordinates/address mutation, owner/public location read, viewport primitives and route destination resolution. Maps does not redesign Seller onboarding/storefronts/lifecycle. Sellers should later consume these hardened Maps primitives instead of duplicating geocoding or coordinate validation.

## Provider-neutral place result

A place result uses only normalized AOS fields. `place_id` is `osm:<node|way|relation>:<id>` when a durable OpenStreetMap identity is available; AOS does not expose a Photon document id or Nominatim `place_id` as a stable identity. Results can contain:

- `place_id`, `osm_type`, `osm_id`
- `name`, `display_address`
- `latitude`, `longitude`
- normalized `category`, `type`, `address_type`
- `locality`, `region`, `postcode`, `country`, `country_code`
- `bounding_box` and `importance` when supported
- reverse results may additionally contain normalized road/house-number fields

Clients must treat nullable address components as optional. AOS never stores raw provider payloads merely to preserve search results.

## Public API

All API endpoints use the standard AOS response envelope and stable Maps error mapping. Unknown fields are rejected. GET endpoints do not accept JSON request-body contracts; query arguments are used. Provider failures are converted to AOS dependency errors without raw exception bodies, infrastructure hostnames, file paths or credentials.

### `GET /api/method/aos.api.v1.maps.autocomplete_places`

Guest allowed. Query: required `q` (2-160 normalized characters), optional `limit` (1-8), `country_code`, `language`, and paired `latitude`/`longitude` for ranking bias. The bias does not create a geographic boundary. IP limit: 180/minute. Successful normalized lists are cached for 60 seconds via privacy-safe hashed keys. Clients should debounce search-as-you-type and cancel stale requests; retries should be limited to transient 5xx/network failures with jitter.

### `GET /api/method/aos.api.v1.maps.search_places`

Guest allowed. Query: required `q` (2-160), optional `limit` (1-20), `country_code`, `language`. IP limit: 90/minute. Successful normalized lists are cached for 180 seconds. Empty search is a successful empty result, not a provider leak/error.

### `GET /api/method/aos.api.v1.maps.reverse_geocode`

Guest allowed. Query: required `latitude`, `longitude`, optional `language`. Coordinates are globally validated. IP limit: 120/minute. Successful normalized result is cached for 3600 seconds using a hashed key. A provider timeout/unavailability produces the stable Maps dependency response; the already-rendered map remains independently usable.

### `POST /api/method/aos.api.v1.maps.get_route`

Session required. JSON/form arguments: `locations` (2-10 objects with canonical `latitude`/`longitude`) or one origin plus optional `destination_seller_id`, optional `costing` (`auto`, `pedestrian`, `bicycle`), `units`, `language`. User limit: 60/minute; IP limit: 240/minute. Route responses are cached for 120 seconds. Seller destination access uses the existing Seller authorization boundary.

### `POST /api/method/aos.api.v1.maps.refresh_route`

Session required. Canonical fields: `current_latitude`, `current_longitude`, `destination_seller_id`, optional costing/units/language. User limit: 30/minute; IP limit: 120/minute. This endpoint is bounded and does not enable passive tracking; every call is an explicit client operation.

Seller-facing API wrappers remain Seller-owned and are not redefined in this Maps pass.

## Validation, bounding and abuse resistance

Autocomplete/search limits are clamped to safe maxima; queries are length-bounded and control/script-scheme input is rejected. Coordinates must be finite and inside WGS84 ranges. Route bodies are limited to 64 KiB and at most 10 locations. Viewport queries are globally valid but bounded to at most 30 degrees per dimension (2 degrees at high zoom); antimeridian crossing is handled explicitly rather than interpreted as an invalid longitude range.

Guest geocoding uses per-IP shared rate limiting. Authenticated route operations use both per-user and per-IP shared limits. There is no Maps-specific rate-limit implementation. Clients cannot supply upstream URLs/hosts.

## Caching

Versioned PMTiles receive one-year immutable object/CDN caching. `current.json` receives short revalidation caching so a new version can be switched atomically. Application search/reverse/route caches have short bounded TTLs and SHA-256-derived keys. Precise coordinate values and search strings are not emitted in cache-key/log text. Private/owner-sensitive seller data is not placed in shared geocoder caches.

## Timeouts, retries and failure handling

Photon client connect/read timeouts are bounded (2s connect, 6s read by default) and only idempotent GETs get a small retry budget for connection failures/502/503/504 with backoff. Nominatim fallback and Valhalla have bounded timeouts. The design avoids a stateful per-process circuit breaker because application replicas are stateless; upstream health-based load balancing, strict local timeout/retry budgets, and service readiness isolate failures without creating retry storms.

Stable failure classes cover validation, authorization/not-found/conflict and dependency/unavailable failures. Raw Photon/OpenSearch/Valhalla/Nominatim errors are sanitized. A tile/CDN failure does not crash unrelated application pages; a geocoder failure does not invalidate an already-loaded map; routing failure does not disable search.

## Photon/OpenSearch production topology

Production should run multiple Photon replicas behind a private health-checked load balancer and an external OpenSearch 3.8.0 cluster with at least three data-capable nodes across failure domains. OpenSearch upgrades are deliberate reviewed changes rather than floating tags. OpenSearch is intentionally not embedded in each Photon container and is not exposed to clients/public networks.

OpenSearch capacity must be sized from the actual imported planet index plus recovery headroom. Photon upstream guidance indicates a planet database on the order of ~95 GB in 2026 and recommends substantial memory for heavy load; production planning should provision materially more disk than the live index for merges, watermarks, snapshots and blue/green recovery. JVM heap should normally remain at or below roughly half node memory (and within OpenSearch/JVM compressed-oops guidance), swap should be disabled/controlled, disk watermarks monitored, replica count >=1, snapshots tested, and shard count derived from measured index size/query load rather than a hard-coded repository value.

`PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES` accepts multiple private OpenSearch endpoints. Do not expose OpenSearch 9200 publicly. Use TLS/auth whenever cluster traffic leaves an isolated trusted network. Health, JVM pressure, disk watermarks, shard health, indexing lag and snapshot failures are operational alerts.

## Photon data lifecycle

A planet import/rebuild is a background/operations job, never an API request and never part of `bench migrate`. `infra/maps/scripts/import-photon.sh` requires an explicit destructive-target confirmation and supports either a verified prepared Photon dump or a maintained Nominatim source database. The import job network is explicit via `PHOTON_IMPORT_DOCKER_NETWORK` (default `host` for externally managed OpenSearch); staging may point it at the private AOS Docker network. `PHOTON_IMPORT_COUNTRY_CODES` and `PHOTON_IMPORT_LANGUAGES` allow bounded sampled imports without changing the runtime API contract.

For production reindexing, build into an inactive OpenSearch cluster/index set, validate global queries/reverse geocoding and index health, then switch the private Photon/OpenSearch target/load-balancer/alias atomically. Keep the previous healthy generation through the rollback window. Routine updates should be automated from the chosen OSM/Nominatim update pipeline or periodic verified full rebuilds; operators must record OSM data timestamp/update lag. Multiple replicas must never independently initiate the same import; the import job is an externally serialized operations task.

Staging may deliberately use fewer OpenSearch nodes/resources, but that must never be described as highly available production topology.

## Basemap data lifecycle and capacity

1. Select a dated OSM planet PBF URL and SHA-256 in `infra/maps/manifest.env`.
2. Run `download-planet.sh`; a checksum mismatch aborts.
3. Run `build-world-pmtiles.sh` on a dedicated high-memory/high-disk worker, not an API host under request load.
4. Validate PMTiles and resulting SHA.
5. Run `publish-basemap.py`; it uploads `basemap/<MAP_DATA_VERSION>/<filename>` first and `basemap/current.json` last.
6. Verify CDN byte-range fetches and representative global tiles before changing client style/config.
7. Roll back by repointing `current.json`/client manifest to the prior version. Clean old versions only after the rollback retention policy expires.

World builds require dedicated capacity. Planetiler upstream examples place world PMTiles and temporary build working sets in the many-tens-of-GB range; provision significant additional SSD scratch/headroom instead of sizing only to final artifact size. Build CPU/RAM/disk are isolated from serving containers.

## Valhalla routing data lifecycle

1. Use the same dated, SHA-256-pinned single planet PBF identified by `MAP_DATA_VERSION`. Multiple regional PBF stitching is not the AOS production path.
2. Run `build-valhalla.sh` on a dedicated high-memory/high-disk build worker. The script rechecks the planet checksum before building admins, time zones and the indexed graph tar.
3. The build is staged under `maps/valhalla/releases/.<version>.building.*`; a failed build is deleted and cannot replace an active graph.
4. `verify-valhalla.sh` validates `valhalla.json`, its `/custom_files` artifact paths, the non-empty tile extract/admin/timezone files, the planet checksum/image pin recorded in `aos-valhalla-manifest.json`, and every artifact SHA-256.
5. A successful release is moved atomically to `maps/valhalla/releases/<MAP_DATA_VERSION>`. Existing releases are immutable and a build refuses to overwrite one.
6. `activate-valhalla.sh <version>` verifies the target again and atomically switches `maps/valhalla/current`. Recreate serving replicas after the switch; they mount the active release read-only and never rebuild.
7. Run `smoke-valhalla-global.py` against each candidate serving pool before it receives application traffic. `get_route` must then return real AOS `200` route results.
8. Keep at least the previous verified release through the rollback window. Roll back by activating the previous version and recreating Valhalla serving replicas.

Planet graph builds are intentionally not sized for the 15 GiB staging/API host. Staging may validate runtime behavior with a separately prepared representative graph, but production completion requires a true planet-derived release built on dedicated capacity and multiple private serving replicas behind health-checked load balancing.

## Docker and infrastructure

The Compose `photon` service uses a reproducibly built pinned Photon JAR, non-root user, `tini`, restart policy, healthcheck, loopback-bound host port, bounded memory/CPU/PIDs, `nofile` ulimit, log rotation inherited from shared policy, private Docker network and graceful stop. It does not carry a local planet index.

Valhalla runs under the operationally separate `--profile maps-routing`, is digest-pinned, loopback/private-bound, resource-bounded and health-checked. Its runtime mounts `${VALHALLA_GRAPH_CURRENT_PATH:-./maps/valhalla/current}` read-only and bypasses the scripted build entrypoint entirely by launching `valhalla_service /custom_files/valhalla.json <threads>` directly. This is deliberate: the scripted entrypoint updates hash/config files even when graph rebuilding is disabled, which is incompatible with an immutable read-only release. Expensive planet graph builds use the scripted image separately via `infra/maps/scripts/build-valhalla.sh`; serving runtime configuration never mutates the verified release.

MinIO remains the existing shared production-ready object-storage service; Maps receives a dedicated bucket/prefix and credentials for publication. The Maps Nginx origin exposes only read-only basemap objects. For a multi-region production deployment, use replicated/redundant S3-compatible storage (or equivalent object storage) behind the CDN rather than treating one local MinIO volume as global HA.

Secrets stay in environment/secret management and are never committed. Internal Photon/OpenSearch/Valhalla endpoints stay private.

## Security

All coordinates and structured inputs are server validated. Provider URLs come only from trusted server configuration, and the internal URL helper rejects credentials, query/fragment injection and public numeric addresses. AOS does not accept arbitrary upstream URL input. Search limits, payload bounds and rate limits reduce regex/query/bbox abuse. Provider response shapes are validated before normalization. Authorization for Seller-owned coordinates stays in the existing Seller boundary; public map primitives must not bypass Social/Account/Seller visibility checks as those layers are introduced.

No precise GPS coordinates are deliberately logged. Provider exceptions/bodies are sanitized and infrastructure topology/credentials are excluded from public envelopes.

## Observability

Maps structured logs record operation, provider/fallback outcome, cache state, count and failure class without query strings or coordinates. Existing application rate-limit/dependency metrics cover API traffic. Infrastructure monitoring must additionally alert on Photon latency/error/timeout rate, OpenSearch cluster/JVM/disk/shard health, Photon restarts, basemap origin/CDN 4xx/5xx/range failures, cache hit rates, import/build failures, snapshot health, OSM data update lag, and Valhalla health/latency/error rate. Routing is a required production Maps dependency.

## High availability

Ordinary Maps API traffic is stateless and requires no sticky sessions. Production supports multiple AOS API replicas, multiple Photon replicas, multiple Valhalla serving replicas, a replicated OpenSearch cluster, CDN-served immutable PMTiles, and redundant object storage. Health-based load balancing should remove individual failed Photon/Valhalla/AOS replicas. Valhalla graph generation is blue/green at the release level: build and validate an inactive immutable release, then switch serving replicas to it; never rebuild underneath live routing traffic. Heavy import/build/update jobs are separate, restartable, externally serialized and observable.

## Fresh-site schema and migrations

There is no historical Maps cleanup patch in `patches.txt`. Fresh `bench migrate` synchronizes current DocTypes and `aos.migrate.after_migrate` reasserts the current idempotent Seller location indexes through `aos.services.sellers.schema`. Planet/Photon/Valhalla imports are explicitly separate from Frappe schema migration. The shared `infra/maps/manifest.env` is also validated per concern: Photon import requires only Photon/import settings, planet download only the OSM source, basemap generation only OSM + Planetiler/basemap settings, and Valhalla only OSM + Valhalla settings. Unconfigured future components therefore do not block an unrelated Maps maintenance job.

## Deployment and rollback sequence

Backend deployment order is: provision/validate external OpenSearch; build/pull Photon; import/verify the Photon planet index; start Photon; configure AOS private Photon URL; build/publish a versioned PMTiles basemap; verify Maps origin/CDN; build and verify a versioned planet Valhalla release on dedicated capacity; activate the candidate graph; start/recreate private Valhalla serving replicas; run `smoke-valhalla-global.py`; set `maps_routing_enabled=1` and the private `valhalla_base_url`; run `bench migrate`; restart AOS workers/web; run production configuration/operational health validators and Maps/API tests.

Do not delete the previous Photon/OpenSearch generation, PMTiles version or Valhalla graph release until the rollback window closes. Application rollback can point AOS back to the previous Photon private endpoint, clients/CDN back to the prior immutable basemap generation, and Valhalla serving replicas back to the previous `maps/valhalla/releases/<version>` without a schema migration. Seller-specific `refresh_route` validation remains deferred until Sellers supplies a real persisted destination, but coordinate-to-coordinate `get_route` is part of the Maps completion gate.

## Open-source quality target and deliberate omissions

The stack supports a Google/Apple-quality interaction foundation using MapLibre, OSM vector data, Planetiler/PMTiles, CDN delivery, Photon/OpenSearch and Valhalla. The web phase can add polished current-location marker/accuracy circle/recenter, smooth camera, debounced biased search, localized labels, light/dark styles, clustering, lazy viewport loads, touch/keyboard accessibility, pitch/bearing and justified 3D/terrain/globe features.

This does not claim proprietary Google/Apple datasets: real-time traffic intelligence, Street View, proprietary satellite imagery or proprietary POI ranking/coverage require separate lawful data sources.
