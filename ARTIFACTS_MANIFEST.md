# Deliverable artifact manifest

The Checkpoint 1 ZIP is a complete source deliverable, not a runtime-data
snapshot. The following reproducible or environment-owned artifacts are
intentionally excluded to keep the archive below 30 MiB and to prevent
credentials, caches, production data, or generated binaries from becoming
source artifacts.

## Excluded generated/runtime content

| Excluded path or class | Reason | Reproducible acquisition/build |
| --- | --- | --- |
| `.git/` | Local repository metadata, not source delivery content. | Initialize a new Git repository or extract into an existing checkout. |
| `.env`, private keys, credentials, service-account files | Environment-owned secrets must never be distributed. | Create local configuration from the committed `.example` and `ci/compose.env` fixtures; inject real values outside source control. |
| `.venv/`, `venv/`, caches, coverage and test reports, `node_modules/` | Reproducible dependency/test output. | Use the exact hash locks with `make ci`; Node dependencies are restored from the committed lockfiles by the owning build. |
| model weights and downloaded ML model directories | Large provider/model assets and licenses are runtime concerns. | Configure the documented local model paths and obtain the model from its approved upstream; CI never downloads models. |
| `maps/**/*.osm.pbf`, `maps/**/*.mbtiles`, Valhalla tiles, Nominatim/Photon data, Docker volumes | Large generated geospatial/runtime database data. | Fill `infra/maps/manifest.env` from its example, including the upstream dataset SHA-256, then run `download-kenya.sh`, `prepare-kenya.sh`, `build-kenya-tiles.sh`, `build-valhalla.sh`, and the relevant import script. Each download/build script validates the configured checksum or build output. |
| `infra/maps/tileserver/fonts/**/*.pbf` | 512 mirrored MapLibre glyph ranges total 70,005,946 bytes and alone exceed the delivery limit. | Run `infra/maps/scripts/build-map-fonts.sh`. The excluded source-tree set had aggregate `sha256sum`-of-sorted-`sha256sum` value `0c65a72d844214fc55f8d2e3015482377f89c3d33634f6f01f05028bd7391910`. |
| backups, SQL/database dumps, media and production data | Non-source, potentially sensitive state. | Restore only through the separately documented operational procedures; none is required for Checkpoint 1 CI. |

## Photon image status

The source Dockerfile remains reproducible, but Photon is removed from the
maintained `docker-compose.yml` until an AOS-built image is published and a
registry manifest digest can be verified. Build it locally with:

```bash
PHOTON_VERSION=1.2.0 \
PHOTON_JAR_SHA256=3455a6c2c9828393c2506d23540015b3b220cf00f4a9bb2c39e8007971cbe8c7 \
infra/maps/scripts/build-photon-image.sh
```

The upstream Photon 1.2.0 jar is 95,801,455 bytes and has SHA-256
`3455a6c2c9828393c2506d23540015b3b220cf00f4a9bb2c39e8007971cbe8c7`.
The Dockerfile verifies this value before the jar enters the image. Publishing,
recording the resulting registry digest, and re-adding the service are explicit
future operational actions; Checkpoint 1 does not publish images or deploy.
