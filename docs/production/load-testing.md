# AOS production load-testing guide

This guide covers P2.5 load testing for the AOS backend. It is designed for staging and production rehearsals, not for attacking live production traffic without a maintenance plan.

## Goals

Load testing should prove that the highest-traffic AOS flows remain stable under realistic pressure:

- auth/session validation
- ads list/detail/search
- shorts feed/detail/like/comment/view tracking
- media upload initialization
- chat list/send flows
- live list/start/join/token/tracking flows
- notifications list and push-token registration
- maps autocomplete/search/reverse/route flows
- admin-only operational diagnostics at low rate

It should also prove that protections added earlier still hold under concurrency:

- callback and session endpoints do not leak secrets
- rate limits return controlled responses
- unique constraints prevent duplicate engagement rows
- Frappe queues do not build uncontrolled backlog
- AOS service-job tables do not accumulate stale processing rows
- operational health and job monitoring remain green after the test

## Tooling

The repository includes k6 scripts under:

```text
infra/load-testing/k6/
```

k6 is preferred here because AOS load tests are mostly HTTP API tests and can be run from a single staging runner without adding Python test-worker state.

## Safety model

The k6 suite is safe by default.

Read-heavy scripts run immediately. Write-heavy flows are disabled until explicitly enabled with environment flags:

```bash
RUN_WRITES=false
RUN_MEDIA_INIT=false
RUN_SHORT_WRITES=false
RUN_CHAT_WRITES=false
RUN_LIVE_START=false
RUN_LIVE_JOIN=false
RUN_NOTIFICATION_WRITES=false
RUN_ADMIN_DIAGNOSTICS=false
```

Never use real customer accounts for write-enabled tests. Use dedicated staging load-test users and seed records.

Never commit real credentials in `env.example.sh`, `env.example.json`, shell history, screenshots, tickets, or chat logs.

## Environment setup

Create a local environment file from the template:

```bash
cd /home/aos/aos
cp infra/load-testing/k6/env.example.sh infra/load-testing/k6/env.local.sh
nano infra/load-testing/k6/env.local.sh
chmod 600 infra/load-testing/k6/env.local.sh
source infra/load-testing/k6/env.local.sh
```

Minimum values:

```bash
export BASE_URL="https://aos-staging.duckdns.org"
export USER_EMAIL="load-user@example.com"
export USER_PASSWORD="replace-with-staging-test-password"
```

Recommended seed values:

```bash
export AD_IDS="ad_replace_with_public_id_1,ad_replace_with_public_id_2"
export SHORT_IDS="SHR-AAAAAAAAAAAAAAAAAAAA,SHR-BBBBBBBBBBBBBBBBBBBB"
export TEST_SHORT_ID="SHR-AAAAAAAAAAAAAAAAAAAA"
export CHAT_CONVERSATION_ID="CONV-2026-00001"
export CHAT_RECEIVER_USER="load-user-2@example.com"
export LIVE_ID="LIVE-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
```

Admin diagnostics require a user with effective `AOS Settings` Read permission and should remain low-rate:

```bash
export ADMIN_EMAIL="admin-load-check@example.com"
export ADMIN_PASSWORD="replace-with-staging-admin-password"
export RUN_ADMIN_DIAGNOSTICS=true
```

## Install k6

Install k6 on the load runner, not necessarily on the Frappe server. Keep the runner close enough to staging that network latency is predictable, but not on the same small machine if it would distort CPU/memory readings.

Verify:

```bash
k6 version
```

## Baseline gates before load

Before running load, staging should be clean:

```bash
cd /home/aos/frappe-bench
bench --site aos-staging.duckdns.org execute aos.utils.production_config.production_config_summary
bench --site aos-staging.duckdns.org execute aos.utils.operational_health.operational_health_summary
bench --site aos-staging.duckdns.org execute aos.utils.job_monitoring.job_monitoring_summary
bench --site aos-staging.duckdns.org execute aos.utils.backup_readiness.backup_readiness_summary
```

Expected:

```text
production_config_summary.ready = true
operational_health_summary.ready = true
job_monitoring_summary.ready = true
backup_readiness_summary.ready = true
```

`backup_readiness_summary` may have a degraded offsite backup check until remote backup copy is configured. That should not block API load testing, but it should block final production sign-off.

## Log watching during load

Frappe logs:

```bash
cd /home/aos/frappe-bench
tail -f logs/frappe.log logs/worker.log logs/web.log
```

External services:

```bash
cd /home/aos/aos
docker compose logs -f aos-minio aos-video-api aos-video-worker aos-livekit
```

More workers:

```bash
cd /home/aos/aos
docker compose logs -f aos-notification-api aos-notification-worker aos-search-api aos-search-worker aos-analytics-api aos-analytics-worker
```

Optional system monitoring:

```bash
htop
free -h
df -h
docker stats
```

## Test stages

### 1. Smoke test

Run this first after every deploy:

```bash
cd /home/aos/aos
source infra/load-testing/k6/env.local.sh
VUS=1 ITERATIONS=1 k6 run infra/load-testing/k6/smoke.js
```

Pass criteria:

- no 5xx responses
- p95 under roughly 1.2 seconds for the tiny smoke run
- no new Frappe traceback
- diagnostics remain ready afterward

### 2. Component tests

Run each area separately before the mixed test.

Ads:

```bash
VUS=10 DURATION=5m k6 run infra/load-testing/k6/ads.js
```

Shorts read/tracking:

```bash
VUS=10 DURATION=5m k6 run infra/load-testing/k6/shorts.js
```

Maps:

```bash
VUS=5 DURATION=3m k6 run infra/load-testing/k6/maps.js
```

Chat read/list:

```bash
VUS=5 DURATION=3m k6 run infra/load-testing/k6/chat.js
```

Notifications read/list:

```bash
VUS=3 DURATION=2m k6 run infra/load-testing/k6/notifications.js
```

Live browse:

```bash
VUS=5 DURATION=3m k6 run infra/load-testing/k6/live.js
```

Media upload-init, only when intentionally creating media rows:

```bash
RUN_MEDIA_INIT=true VUS=3 DURATION=2m k6 run infra/load-testing/k6/media.js
```

Admin diagnostics, low rate only:

```bash
RUN_ADMIN_DIAGNOSTICS=true VUS=1 DURATION=1m k6 run infra/load-testing/k6/diagnostics.js
```

### 3. Optional write tests

Use short runs and dedicated staging accounts.

Shorts like/comment burst:

```bash
RUN_SHORT_WRITES=true VUS=3 DURATION=2m k6 run infra/load-testing/k6/shorts.js
```

Chat send burst:

```bash
RUN_CHAT_WRITES=true VUS=2 DURATION=2m k6 run infra/load-testing/k6/chat.js
```

Live join/token/tracking:

```bash
RUN_LIVE_JOIN=true VUS=5 DURATION=2m k6 run infra/load-testing/k6/live.js
```

Live start should be used rarely because each authenticated host can only have one active live:

```bash
RUN_LIVE_START=true VUS=1 DURATION=30s k6 run infra/load-testing/k6/live.js
```

Push token register/deactivate:

```bash
RUN_NOTIFICATION_WRITES=true VUS=2 DURATION=1m k6 run infra/load-testing/k6/notifications.js
```

### 4. Mixed production rehearsal

Start small:

```bash
VUS=10 DURATION=5m k6 run infra/load-testing/k6/mixed-production-rehearsal.js
```

Then run the staging rehearsal:

```bash
VUS=25 DURATION=15m k6 run infra/load-testing/k6/mixed-production-rehearsal.js
```

For a stronger pre-production rehearsal, increase gradually:

```bash
VUS=50 DURATION=20m k6 run infra/load-testing/k6/mixed-production-rehearsal.js
```

Do not jump straight to large tests. Scale only after diagnostics are clean.

## Default thresholds

The scripts use these general thresholds:

```text
http_req_failed < 3%
aos_unexpected_errors < 0.1%
aos_business_failures < 5%
p95 HTTP duration < 1.5–1.8s for most scripts
p99 HTTP duration < 3–4s for most scripts
```

AOS-specific interpretation:

- 2xx is expected for successful calls.
- Some defensive 4xx responses are expected during safe load tests, for example invalid credentials, missing optional seed records, validation responses, conflicts, permission responses, and rate limits. The shared k6 helper marks 401/403/404/409/422/429 as expected for k6's built-in `http_req_failed` metric, while the AOS-specific `record()` helper still enforces endpoint-level allow-lists through `aos_business_failures`.
- 429 can be acceptable during intentional burst tests because it proves rate limits are working.
- 5xx is never acceptable and is counted as an unexpected error.

## Pass/fail criteria

A load test passes only if all are true:

```text
k6 exits successfully
5xx rate is effectively zero
p95 latency is within the chosen threshold
Frappe logs have no new unhandled traceback
external service logs have no crash loop
operational_health_summary.ready = true
job_monitoring_summary.ready = true
Frappe queue backlog remains controlled
no stale AOS service jobs remain after the cool-down window
```

After any test, run:

```bash
cd /home/aos/frappe-bench
bench --site aos-staging.duckdns.org execute aos.utils.operational_health.operational_health_summary
bench --site aos-staging.duckdns.org execute aos.utils.job_monitoring.job_monitoring_summary
```

If write-enabled tests created media objects, notification tokens, comments, messages, or lives, wait a few minutes and run job monitoring again.

## Result interpretation

### Healthy result

```text
http_req_failed below threshold
p95 and p99 stable
no 5xx spikes
job_monitoring_summary.ready = true
operational_health_summary.ready = true
```

This means the backend handled the tested load profile.

### Rate-limit-heavy result

```text
429 responses visible
no 5xx responses
job_monitoring_summary.ready = true
```

This can be acceptable for burst tests. It means rate limiting protected the system. Lower VUs or increase the load-test duration if you want normal-user throughput instead of abuse-resistance behavior.

### Queue backlog result

If k6 looks good but job monitoring turns unhealthy, the HTTP layer survived but background workers did not keep up. Check:

```bash
bench --site aos-staging.duckdns.org execute aos.utils.job_monitoring.job_monitoring_summary
cd /home/aos/aos && docker compose logs -f aos-search-worker aos-analytics-worker aos-notification-worker aos-video-worker
```

Then decide whether to tune workers, reduce synchronous enqueue volume, or batch events.

### 5xx result

Any 5xx during load must be investigated before production. Capture:

```text
script name
VUs and duration
endpoint/method tag
first traceback in logs
job monitoring summary
operational health summary
```

Then rerun the smallest component script that reproduces it.

## Production caution

Do not run write-heavy load tests against real production without:

- a maintenance window or explicit approval
- dedicated load-test users
- clear cleanup plan
- alert silencing/notification plan
- current backup and restore rehearsal
- rollback plan

For real production after launch, prefer low-rate synthetic monitoring and short read-only probes, not write-heavy stress tests.
