# AOS Production Release Checklist

## Code and repository

- [ ] Reviewed commit is pushed and tagged
- [ ] Working tree is clean
- [ ] No `.env`, credentials, map artifacts, backups, or compiled caches are tracked
- [ ] `python -m compileall aos` succeeds
- [ ] Required migrations are reviewed

## Images and configuration

- [ ] Valhalla, Planetiler, and other production images are pinned
- [ ] `docker compose config` succeeds
- [ ] No placeholder secret remains
- [ ] Internal services bind to `127.0.0.1`
- [ ] Image Search, Background Removal, Translation, and Qdrant are not publicly exposed
- [ ] Image Search `/health` and `/ready` pass
- [ ] Background Removal `/health` and `/ready` pass
- [ ] Translation `/health` and `/ready` pass
- [ ] LiveKit public ports match firewall rules

## Backup and rollback

- [ ] Fresh backup completed
- [ ] Backup verification passed
- [ ] Backup ID recorded
- [ ] Previous Git commit/image digests recorded
- [ ] Rollback operator and decision criteria assigned

## Deployment

- [ ] Application code updated
- [ ] `bench --site <site> migrate` succeeds
- [ ] Assets build succeeds
- [ ] Docker services are healthy
- [ ] Image-search vector rebuild dry run reviewed
- [ ] Image-search vector rebuild completed when required
- [ ] Background-removal service direct test completed
- [ ] Translation service direct test completed
- [ ] Translation Frappe client smoke test completed
- [ ] Nginx configuration test succeeds
- [ ] TLS certificates are valid

## Manual image-search checks

- [ ] Active ad with images is indexed
- [ ] Sold/Expired/Deleted ads do not appear in image search
- [ ] Image search returns serialized AOS ad data plus `image_search` metadata
- [ ] Image-search service unavailable path returns a friendly temporary error

## Manual background-removal checks

- [ ] User-owned image can be processed through the Frappe endpoint
- [ ] Processed result is saved as a new PNG Frappe File
- [ ] Original image remains unchanged
- [ ] Non-image files are rejected
- [ ] Oversized images are rejected
- [ ] User cannot process another user's private file
- [ ] Background-removal service unavailable path returns a friendly temporary error


## Manual translation checks

- [ ] Translation container is healthy
- [ ] Translation model reports loaded from `/ready`
- [ ] `aos.integrations.ai.translation_client.health_check()` works from bench console
- [ ] `translate_text()` works from bench console
- [ ] Chat translate endpoint returns translated content
- [ ] Translating the same message twice returns `cached: true` on the second request
- [ ] User cannot translate a message from a conversation they cannot access
- [ ] Deleted or unsupported message types are rejected
- [ ] Translation service unavailable path returns a friendly temporary error

## Manual map checks

- [ ] Search endpoint works
- [ ] Reverse geocoding works
- [ ] Route endpoint works
- [ ] Seller can set/remove location
- [ ] Guest can retrieve active seller location
- [ ] Tile style and vector tiles load over HTTPS
- [ ] Nominatim and Valhalla are not publicly exposed

## Post-deployment

- [ ] Error logs checked
- [ ] CPU, RAM, disk and latency checked
- [ ] Backup timer remains active
- [ ] Deployed commit and completion time recorded
