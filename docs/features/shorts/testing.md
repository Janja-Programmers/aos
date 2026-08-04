# Testing

## Test layout

Feature-owned Shorts tests live in `aos/api/shorts/tests/`; generic repository contracts remain in `aos/tests/`.

```text
aos/api/shorts/tests/
  __init__.py
  test_api_contracts.py
  test_database_contracts.py
  test_classification.py
```

Cross-service callback, outbox, dynamic SQL, repository hygiene, and other genuinely generic suites remain under `aos/tests/`.

## Feature test commands

```bash
bench --site <site> run-tests --app aos --module aos.api.shorts.tests.test_api_contracts
bench --site <site> run-tests --app aos --module aos.api.shorts.tests.test_database_contracts
bench --site <site> run-tests --app aos --module aos.api.shorts.tests.test_classification
```

## API examples

Authenticated requests use the normal Frappe session cookie.

```bash
curl -sS -b cookies.txt 'https://<host>/api/method/aos.api.v1.shorts.feed_for_you?limit=20'
curl -sS -b cookies.txt -X POST 'https://<host>/api/method/aos.api.v1.shorts.toggle_like' -d 'short_id=SHORT-2026-00137'
curl -sS -X POST 'https://<host>/api/method/aos.api.v1.shorts.track_view' -d 'short_id=SHORT-2026-00137' -d 'session_id=<opaque-session>' -d 'watch_ms=5000' -d 'event_id=<uuid>'
```

Use the returned `next_cursor` unchanged. Altering any byte or using an expired cursor must return `SHORTS_INVALID_CURSOR`.

## Required regression matrix

- strict fields, aliases, IDs and `cmd` transport handling;
- creator mode ignored, automatic Math/Learn classification, Shop/ad enforcement, signed frame requests, and non-fatal classifier fallback;
- upload owner/key/size/content validation and duplicate confirmation;
- generation-aware ready/failed/replayed/stale callbacks;
- everyone/followers/friends/only-me, guest and both block directions;
- All, Following, vibes/content-mode, profile, saved, liked, reposted and sound feeds;
- no duplicate or skipped cursor rows and All not narrowed to ranking candidates;
- idempotent likes/saves/reposts/reports/events and exact counters;
- comments-disabled and cascade-delete counter behavior;
- share playable metadata and download object-key non-disclosure;
- migration rerun safety and populated-database duplicate reconciliation;
- schema-only index patch ordering and absence of mixed DML/DDL.
