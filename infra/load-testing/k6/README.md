# AOS k6 load-testing suite

This directory contains safe-by-default k6 scripts for staging and production-rehearsal load tests.

Default behavior is read-heavy. Any endpoint that creates rows, tokens, comments, chat messages, live sessions, or push-token records is disabled until the relevant `RUN_*` flag is set.

## Files

- `smoke.js` — tiny API smoke test.
- `ads.js` — ads list/detail/search load.
- `shorts.js` — shorts feed/detail/comment-list/view-tracking load, with optional like/comment writes.
- `media.js` — optional upload-init load; disabled by default.
- `short-upload.js` — real Short upload rehearsal supporting both direct PUT and resumable multipart upload, optional Short creation, processing polling, and worker-capacity sizing; requires a local video fixture and explicit duration.
- `chat.js` — chat list load, with optional conversation/message writes.
- `live.js` — live list/detail load, with optional live start/join/token/tracking.
- `notifications.js` — notifications list load, with optional push-token register/deactivate.
- `maps.js` — map autocomplete/search/reverse load, with optional route checks.
- `diagnostics.js` — low-rate admin diagnostics load; disabled by default.
- `mixed-production-rehearsal.js` — mixed traffic rehearsal for staging.
- `env.example.sh` / `env.example.json` — environment templates; do not commit real credentials.

## Minimal run

```bash
source infra/load-testing/k6/env.local.sh
k6 run infra/load-testing/k6/smoke.js
```

## Mixed rehearsal

```bash
source infra/load-testing/k6/env.local.sh
VUS=20 DURATION=10m k6 run infra/load-testing/k6/mixed-production-rehearsal.js
```

## Write-enabled examples

```bash
RUN_MEDIA_INIT=true VUS=3 DURATION=2m k6 run infra/load-testing/k6/media.js
SHORT_VIDEO_FILE=/path/to/large.mp4 SHORT_VIDEO_DURATION_SECONDS=600 VUS=10 ITERATIONS=20 k6 run infra/load-testing/k6/short-upload.js
RUN_SHORT_WRITES=true VUS=3 DURATION=2m k6 run infra/load-testing/k6/shorts.js
RUN_CHAT_WRITES=true VUS=2 DURATION=2m k6 run infra/load-testing/k6/chat.js
RUN_LIVE_JOIN=true VUS=5 DURATION=2m k6 run infra/load-testing/k6/live.js
```

Only run write-enabled scripts with dedicated staging users and seed records.

## Heavy Short upload rehearsal

`short-upload.js` exercises the actual production upload contract. It authenticates, calls `init_upload` with `upload_mode=auto`, then follows whichever server-selected path applies:

- direct: one presigned PUT followed by `confirm_upload`;
- multipart: authoritative `multipart_status`, bounded `multipart_part_urls`, parallel UploadPart PUTs, status reconciliation/retry, then `complete_multipart_upload`.

The multipart script never constructs or submits a completion ETag manifest; object storage remains authoritative. `SHORT_UPLOAD_PART_PARALLELISM` can request lower concurrency, but the script never exceeds the server-provided `max_parallel_parts` hint. Use a dedicated staging account and fixture because the test uploads real bytes and can consume substantial bandwidth, CPU, object-storage I/O and storage.

For realistic aggregate concurrency, prefer a pool of dedicated staging users instead of raising one user's production quota. Supply comma-separated `SHORT_UPLOAD_USER_EMAILS` and either one shared test password or matching `SHORT_UPLOAD_USER_PASSWORDS`. VUs are distributed across the pool. If staging sessions are prepared out-of-band, `SHORT_UPLOAD_SIDS` can be supplied instead so repeated login work does not distort upload/control-plane measurements. This avoids the deliberate per-user active-multipart limit distorting an aggregate capacity test.

For Shorts, `duration_seconds` is an admission-control field and must be supplied to `init_upload`. Files above the duration/size policy are rejected before the server issues byte-upload capability.

To include the expensive processing tier and derive a starting video-worker count from measured service time:

```bash
SHORT_VIDEO_FILE=/path/to/10-minute.mp4 \
SHORT_VIDEO_DURATION_SECONDS=600 \
RUN_SHORT_CREATE=true \
WAIT_FOR_PROCESSING=true \
TARGET_UPLOADS_PER_MINUTE=120 \
PROCESSING_TARGET_UTILIZATION=0.70 \
VUS=20 ITERATIONS=40 \
k6 run infra/load-testing/k6/short-upload.js
```

The script polls each created Short until `ready`/`failed` and reports a worker estimate using:

`workers = ceil(target_uploads_per_minute × measured_processing_seconds / (60 × target_utilization))`

`create -> ready` includes queue wait. First run a **low-concurrency calibration** (for example VUS=1) so queue wait is near zero and the measured latency approximates processing service time; use that result for the sizing formula. Then run the target-arrival rehearsal with the estimated replica count and validate queue oldest-age, CPU, memory, object-store bandwidth and callback latency. Under saturation, the p95 value is a queueing signal and becomes a conservative overestimate rather than pure service time. This is a capacity estimate for one-job-at-a-time RQ video workers, not a substitute for autoscaling/queue SLOs.

A two-phase staging rehearsal is recommended:

```bash
# 1. Processing service-time calibration: keep queue wait near zero.
SHORT_VIDEO_FILE=/path/to/10-minute.mp4 \
SHORT_VIDEO_DURATION_SECONDS=600 \
RUN_SHORT_CREATE=true WAIT_FOR_PROCESSING=true \
TARGET_UPLOADS_PER_MINUTE=120 \
VUS=1 ITERATIONS=3 \
k6 run infra/load-testing/k6/short-upload.js

# 2. Upload/control-plane arrival-rate rehearsal. Use the calibrated worker
#    estimate before this run; do not block each arrival on transcoding.
SHORT_VIDEO_FILE=/path/to/10-minute.mp4 \
SHORT_VIDEO_DURATION_SECONDS=600 \
RUN_SHORT_CREATE=true WAIT_FOR_PROCESSING=false \
ARRIVAL_RATE_UPLOADS_PER_MINUTE=120 \
PREALLOCATED_VUS=40 MAX_VUS=200 DURATION=10m \
k6 run infra/load-testing/k6/short-upload.js
```

The arrival-rate summary reports configured arrivals/minute, achieved completed uploads/minute and dropped iterations. If k6 drops iterations, increase load-generator VU capacity (and, for very large fixtures, load-generator memory/distribution) before interpreting that run as backend saturation.

Useful controls:

- `SHORT_UPLOAD_PART_PARALLELISM` — client-side multipart PUT concurrency, capped by the backend hint.
- `UPLOAD_TIMEOUT` — per object-store PUT timeout, default `20m`.
- `PROCESSING_WAIT_TIMEOUT_SECONDS` — max processing wait, default `7200`.
- `PROCESSING_POLL_SECONDS` — Short-status polling interval, default `10`.
- `TARGET_UPLOADS_PER_MINUTE` — desired production arrival rate for sizing output.
- `PROCESSING_TARGET_UTILIZATION` — sizing headroom, default `0.70`.
- `SHORT_UPLOAD_USER_EMAILS` / `SHORT_UPLOAD_USER_PASSWORDS` — optional dedicated staging credential pools for aggregate concurrency.
- `SHORT_UPLOAD_SIDS` — optional comma-separated authenticated staging session ids; bypasses login overhead for upload-focused rehearsals.
- `ARRIVAL_RATE_UPLOADS_PER_MINUTE` — enables the constant-arrival-rate scenario instead of fixed iterations.
- `PREALLOCATED_VUS` / `MAX_VUS` / `DURATION` — load-generator capacity/window for arrival-rate rehearsals.
