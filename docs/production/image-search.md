# AOS Image Search Operations

Image search is a core AOS feature and is expected to be available in production. It runs outside the Frappe business backend so ML/vector dependencies do not affect Frappe workers.

## Architecture

```text
AOS Frappe backend
  -> private image-search HTTP service
      -> OpenCLIP / Torch embedding runtime
      -> Qdrant vector store
AOS video worker
  -> signed internal frame-classification endpoint
      -> the same OpenCLIP runtime
```

Ownership boundary:

- Frappe owns ads, permissions, moderation, database records, rate limits, and response serialization.
- Image search owns embeddings, vector indexing, Qdrant access, similarity scoring, thresholds, model configuration, and visual Shop/Geo/Vibes/Learn frame scores.
- Qdrant is private infrastructure behind the image-search service.

The Frappe backend must not import `torch`, `open_clip`, `qdrant_client`, or image-search model logic.

## Services

Start image search and Qdrant:

```bash
docker compose up -d qdrant image-search
```

Check status:

```bash
docker compose ps qdrant image-search
curl http://127.0.0.1:8110/health
curl http://127.0.0.1:8110/ready

Docker health uses `/ready`, not `/health`, so the service is not considered healthy until the OpenCLIP model is loaded and Qdrant is reachable. The model cache is persisted in the `aos_image_search_models` volume so restarts do not repeatedly cold-download weights.
```

`/health` should be fast. `/ready` verifies model readiness and Qdrant access, so it can be slower during cold startup.

## AOS Settings and environment

Configure the private image-search service URL in `.env` and keep only product limits/timeouts in AOS Settings:

```text
# .env
IMAGE_SEARCH_SERVICE_URL=http://127.0.0.1:8110
IMAGE_SEARCH_FILE_BASE_URL=https://api.example.com
IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS=api.example.com
SHORT_CLASSIFICATION_SECRET=<openssl-rand-hex-32>

# AOS Settings
image_search_service_timeout_seconds: 20
image_search_default_limit: 20
image_search_max_limit: 100
```

Do not configure Qdrant in AOS Settings. Qdrant connection details belong to the image-search service environment.

`SHORT_CLASSIFICATION_SECRET` also authenticates Frappe-to-image-search mutation calls. Internal replace/delete requests are signed over the timestamp, HTTP method, request path, and exact request body; stale or invalid signatures are rejected.

Remote ad-image downloads are restricted to exact hostnames in `IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS`, plus the hostnames derived from `IMAGE_SEARCH_FILE_BASE_URL` and the canonical `AOS_MEDIA_PUBLIC_BASE_URL` that Docker Compose passes into the service. Redirects are not followed. Add only additional production image hosts explicitly; do not use wildcards or URL values in the allowlist.

For a host-based Frappe deployment with Docker Compose services bound privately, use:

```text
http://127.0.0.1:8110
```

If Frappe also runs inside the Docker network, use:

```text
http://aos-image-search:8000
```

## Indexing rules

Only Active ads with images should exist in the image-search index.

Recommended lifecycle behavior:

```text
Ad becomes Active      -> replace/index vectors
Ad leaves Active       -> delete vectors
Ad images are updated  -> replace vectors if Active, otherwise delete vectors
Ad is Sold/Expired/Deleted -> delete vectors
```

Indexing must be idempotent:

```text
delete all vectors for ad_id
index current saved image rows
```

Ad create/update/status flows should not fail because image-search indexing failed. They should log the failure and keep the business action successful.

## Manual rebuild

Use a dry run first:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true}'
```

Queue a full rebuild:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

Limit a test run:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"limit": 100}'
```

Reindex only Active ads:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.reindex_active_ads
```

Clean vectors for unindexable ads only:

```bash
bench --site <site> execute aos.integrations.ai.image_search_tasks.delete_vectors_for_unindexable_ads
```

## Failure behavior

Image search is always expected to be available. Do not add a product-level disable flag.

Expected behavior:

- Ad create/update/status should continue if indexing fails.
- Image search requests should return a friendly temporary unavailable error if the service is down.
- Internal model, Qdrant, and network details should be logged server-side, not exposed to clients.

## Recovery

Qdrant can be backed up for faster disaster recovery, but it is not the source of truth. The source of truth is AOS ads and saved ad images.

If Qdrant is stale, missing, or corrupted:

```bash
docker compose up -d qdrant image-search
bench --site <site> execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index
```

After the rebuild, test visual search with known Active ads.

## Shorts frame classification

Set `SHORT_CLASSIFICATION_SECRET` to the same long random value used by the video services. `/internal/shorts/classify-frames` is hidden from OpenAPI, rejects unsigned or stale requests, binds `X-AOS-Timestamp` into the HMAC signature, bounds frame count/size, and returns only four normalized scores plus model metadata. It does not decide Shop authorization or publish state; Frappe owns those rules. Warm `/ready` after deployment so the OpenCLIP model is loaded before the first Short is processed.
