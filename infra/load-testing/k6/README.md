# AOS k6 load-testing suite

This directory contains safe-by-default k6 scripts for staging and production-rehearsal load tests.

Default behavior is read-heavy. Any endpoint that creates rows, tokens, comments, chat messages, live sessions, or push-token records is disabled until the relevant `RUN_*` flag is set.

## Files

- `smoke.js` — tiny API smoke test.
- `ads.js` — ads list/detail/search load.
- `shorts.js` — shorts feed/detail/comment-list/view-tracking load, with optional like/comment writes.
- `media.js` — optional upload-init load; disabled by default.
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
RUN_SHORT_WRITES=true VUS=3 DURATION=2m k6 run infra/load-testing/k6/shorts.js
RUN_CHAT_WRITES=true VUS=2 DURATION=2m k6 run infra/load-testing/k6/chat.js
RUN_LIVE_JOIN=true VUS=5 DURATION=2m k6 run infra/load-testing/k6/live.js
```

Only run write-enabled scripts with dedicated staging users and seed records.
