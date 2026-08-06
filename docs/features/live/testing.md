# Live Testing

## Repository validation

Run with the repository-required Python runtime and installed dependencies:

```bash
python -m compileall aos
python -m unittest discover -s aos/api/live/tests -p 'test_*.py' -v
python ci/validate_rate_limit_coverage.py .
ci/repository-hygiene-and-secrets.sh
```

## Frappe/Bench

```bash
bench --site <site> run-tests --app aos --module aos.api.live
bench --site <site> run-tests --app aos --module aos.tests.test_dynamic_sql_safety
bench --site <site> run-tests --app aos
```

The broad suite is required because Live touches Accounts, Social, Media, Notifications, transaction callbacks, and account deletion.

## Companion services

Use actual repository paths:

```bash
pytest infra/notification-delivery
pytest infra/moderation
pytest infra/analytics-pipeline
```

Live does not currently call Moderation or Analytics Pipeline directly; their tests guard shared infrastructure compatibility.

## LiveKit integration tests

Use a non-production LiveKit environment and credentials. Validate:

- role claims and TTL;
- viewer publish denial;
- opaque identities/metadata;
- create/delete/list/remove admin methods;
- timeout and retry categories;
- raw-body webhook signature verification;
- same-event replay;
- out-of-order room/participant callbacks;
- disconnect/reconnect and empty-room finish behavior.

Never record tokens, API secrets, raw Authorization headers, or private participant metadata in test output.

## API/Postman sequence

1. Authenticate host and viewer sessions.
2. `start_live`.
3. `track_join` for viewer using a random session ID.
4. `get_live_token` and join LiveKit.
5. `add_live_message`; verify a separate socket receives `aos_live_message`.
6. Fetch history and retain `next_cursor`; alter one character and verify `LIVE_INVALID_CURSOR`.
7. Exercise co-host invite/request/accept/activate/end.
8. `track_leave`.
9. `end_live` twice.
10. Verify direct access/feed/token behavior after end.

## Required race tests on staging

- two simultaneous starts for one host;
- start versus end;
- duplicate join and duplicate webhook join;
- leave versus reconnect;
- co-host accept versus reject;
- co-host accept versus Live end;
- cancellation/removal versus token refresh;
- block versus join/token issue;
- duplicate comment with same idempotency key;
- notification/outbox rollback at the operation savepoint.

## Tests added

`aos/api/live/tests` contains 41 test methods. The 29 import-free tests verify strict request fields/aliases/IDs/pagination/text bounds, thin endpoint wrappers, no internal commits, LiveKit role/privacy guards, verified webhook dedupe, after-commit realtime, bounded migration and duplicate-room repair, patch ordering, uniqueness fields, TTL bounds, rate-limit key privacy, and endpoint-policy coverage.

Twelve Frappe integration tests cover public contract responses, database uniqueness, notification deduplication, lifecycle idempotency, outer-transaction preservation, and migration reruns. Runtime Frappe, database, Redis, and LiveKit assertions still require the Bench and staging commands above.

See `validation-results.md` for the commands and exact results from the hardening environment.
