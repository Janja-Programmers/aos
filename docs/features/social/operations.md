# Operations

## Metrics/logs

Logger: `aos.social`. Fields are bounded: `operation`, `outcome`, `latency_ms`, `reason`, `changed`, `notification`, and `count`. Alert on sustained `failure`, cursor rejection spikes, high block/follow conflict rates, or delivery/outbox failures. No PII is logged.

## Health checks

Periodically compare `AOS Profile.total_followers/total_following` with grouped `AOS Follow` counts. Check uniqueness/index presence after migration. Review transactional outbox backlog for `notification_delivery` and the existing notification-delivery worker health.

## Staging commands

```bash
bench --site <site> backup --with-files
bench --site <site> migrate
bench --site <site> clear-cache
bench --site <site> run-tests --app aos --module aos.api.social
bench --site <site> run-tests --app aos --module aos.tests.test_dynamic_sql_safety
bench --site <site> run-tests --app aos
bash ci/repository-hygiene-and-secrets.sh
```

Restart web, queue, scheduler, and websocket processes through the deployment supervisor after migrate. Verify follow, reciprocal follow, block, blocked search, unblock, cursor continuation, and notification-outbox rows before production promotion.
