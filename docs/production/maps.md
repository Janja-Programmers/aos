# AOS Maps Production Operations

## Purpose

AOS Maps is the seller-location and directions layer for the marketplace.
It is not a general Google Maps clone for product search. The production scope is:

```text
Seller sets one exact public location
Buyer finds seller locations near them
Buyer opens directions to a seller
Backend protects expensive map/routing services
```

## Private services

The following services must remain private behind the AOS backend or Nginx maps proxy:

```text
TileServer GL  -> public only through the maps domain/style/tiles
Nominatim      -> private, backend only
Photon         -> private, backend only
Valhalla       -> private, backend only
```

Do not expose Nominatim, Photon, or Valhalla directly to the public internet.

## Repository files

Main infrastructure files:

```text
docker-compose.yml
infra/maps/manifest.env.example
infra/maps/scripts/download-kenya.sh
infra/maps/scripts/prepare-kenya.sh
infra/maps/scripts/build-kenya-tiles.sh
infra/maps/scripts/build-valhalla.sh
infra/maps/scripts/build-map-fonts.sh
infra/maps/scripts/import-nominatim.sh
infra/maps/scripts/import-photon.sh
infra/maps/scripts/verify-map-data.sh
infra/maps/tileserver/config.json
infra/maps/tileserver/styles/aos/style.json
```

Main backend files:

```text
aos/api/maps/*
aos/api/maps/clients/*
aos/api/sellers/list_sellers.py
aos/api/sellers/map_points.py
aos/api/sellers/set_location.py
aos/api/sellers/get_location.py
aos/api/sellers/remove_location.py
```

## Manifest setup

Copy the sample manifest and fill all placeholders:

```bash
cp infra/maps/manifest.env.example infra/maps/manifest.env
nano infra/maps/manifest.env
```

Required production rules:

```text
Use immutable Docker image digests.
Use exact SHA-256 checksums for downloaded map data.
Keep infra/maps/manifest.env out of Git.
Keep generated map data out of Git.
```

## Build and import order

Run from the repository root on the deployment server:

```bash
./infra/maps/scripts/download-kenya.sh
./infra/maps/scripts/prepare-kenya.sh
./infra/maps/scripts/build-kenya-tiles.sh
./infra/maps/scripts/build-valhalla.sh
./infra/maps/scripts/build-map-fonts.sh
./infra/maps/scripts/import-nominatim.sh --rebuild
./infra/maps/scripts/verify-map-data.sh
```

Then start services and run runtime checks:

```bash
docker compose config
docker compose up -d tileserver valhalla nominatim
./infra/maps/scripts/verify-map-data.sh --services
```

Photon is deliberately not part of the maintained Compose stack until the
locally built image is published and its immutable registry digest is reviewed.
The source Dockerfile remains under `infra/maps/photon/`; see
`ARTIFACTS_MANIFEST.md` for the checksum-verified build procedure.

Do not run `import-photon.sh` as part of the maintained production sequence
while that service is absent. After publishing the image, independently verify
its manifest digest, restore the digest-pinned Compose service/volume, and only
then run the existing import script and Photon-specific checks.

## Photon autocomplete

Photon is used for fast autocomplete/search-as-you-type. Nominatim remains the trusted reverse-geocoding provider and fallback search provider.

Configure one prepared Photon archive source in `infra/maps/manifest.env`:

```env
PHOTON_DUMP_URL=
PHOTON_DUMP_LOCAL_PATH=/path/to/photon-kenya.tar.gz
PHOTON_DUMP_FILENAME=photon-kenya.tar.gz
PHOTON_DUMP_SHA256=<64-character-sha256>
```

The import script recreates the external Docker volume:

```text
aos_photon_data
```

If Photon is empty or missing, `verify-map-data.sh` fails.

## Nominatim import

Nominatim import recreates the external Docker volume:

```text
aos_nominatim_data
```

The script explicitly recreates the external volume before starting Compose. This is required because Docker Compose will not automatically create an `external: true` volume.

## Frappe site config

Set backend service URLs:

```bash
bench --site <site> set-config nominatim_base_url http://127.0.0.1:8081
bench --site <site> set-config maps_photon_enabled 0
# Configure Photon only after deploying an approved immutable image:
# bench --site <site> set-config photon_base_url http://photon:2322
bench --site <site> set-config valhalla_base_url http://127.0.0.1:8002
bench --site <site> set-config maps_geocoder_primary nominatim
bench --site <site> set-config maps_geocoder_fallback photon
bench --site <site> clear-cache
bench restart
```

## Smoke tests

Autocomplete:

```bash
curl 'http://127.0.0.1:2322/api?q=Nairobi&limit=1'
```

Nominatim:

```bash
curl 'http://127.0.0.1:8081/status?format=json'
curl 'http://127.0.0.1:8081/search?q=Nairobi&format=json&limit=1'
```

Valhalla:

```bash
curl 'http://127.0.0.1:8002/status'
```

TileServer:

```bash
curl 'http://127.0.0.1:8080/styles/aos/style.json'
curl 'http://127.0.0.1:8080/data/kenya.json'
curl --fail --output /dev/null \
  'http://127.0.0.1:8080/fonts/Noto%20Sans%20Regular/0-255.pbf'
curl --fail --output /dev/null \
  'http://127.0.0.1:8080/fonts/Noto%20Sans%20Bold/0-255.pbf'
```

A `400` response from either glyph URL means the local font tree was never
built or is incomplete. Run `./infra/maps/scripts/build-map-fonts.sh`, then
restart TileServer. The build is atomic and preserves a previously valid font
tree if an upstream download fails.

## Manual API checks

```text
/api/method/aos.api.v1.maps.autocomplete_places
/api/method/aos.api.v1.maps.search_places
/api/method/aos.api.v1.maps.reverse_geocode
/api/method/aos.api.v1.maps.get_route
/api/method/aos.api.v1.maps.refresh_route
/api/method/aos.api.v1.sellers.list_sellers
/api/method/aos.api.v1.sellers.list_seller_map_points
/api/method/aos.api.v1.sellers.set_my_seller_location
/api/method/aos.api.v1.sellers.get_seller_location
/api/method/aos.api.v1.sellers.remove_my_seller_location
```

Confirm:

```text
Autocomplete works with Photon.
Search falls back to Nominatim if Photon is down.
Reverse geocoding rejects coordinates outside Kenya coverage.
Near-me seller discovery rejects coordinates outside Kenya coverage.
Viewport map search rejects viewports outside Kenya coverage.
Route endpoints require login.
Route response includes shape_format=polyline6.
```

## Release checklist additions

Before marking a maps release complete:

```text
Kenya MBTiles exists and validates as SQLite.
Both Noto Sans glyph stacks contain all 256 PBF ranges and the public glyph
endpoints return HTTP 200.
Valhalla routing artifacts exist.
Nominatim volume exists and service is healthy.
Photon is explicitly disabled, or its published digest is verified and its
volume/service health checks pass.
Tile style loads over HTTPS through the maps domain.
Nominatim, any enabled Photon service, and Valhalla are not publicly exposed.
Seller near-me and viewport APIs have rate limits.
Route endpoints require login.
```

### Browser CORS ownership

TileServer GL may emit `Access-Control-Allow-Origin` itself. The AOS Maps Nginx proxy strips that upstream header and emits one canonical wildcard header. Do not remove `proxy_hide_header Access-Control-Allow-Origin;` from the Maps proxy locations; duplicate wildcard headers are accepted by `curl` but rejected by browsers.
