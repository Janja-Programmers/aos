# Chat testing

## Repository/unit gates

```bash
python -m compileall aos
python -m unittest \
  aos.api.chat.tests.test_chat_validation_unit \
  aos.api.chat.tests.test_chat_source_guards -v
python ci/validate_rate_limit_coverage.py .
python ci/validate_repository.py
python ci/validate_doc_paths.py
```

## Bench/site gates

```bash
bench --site <site> run-tests --app aos --module aos.api.chat
bench --site <site> run-tests --app aos --module aos.tests.test_core_feature_flows
bench --site <site> run-tests --app aos
```

## Staging happy path

1. Create/reuse a direct conversation with two active non-blocked accounts.
2. Send text and retry with the same idempotency key; verify one message/unread/notification.
3. Send private Media; verify only participants can obtain attachment access.
4. Reply, edit, react, star, mark delivered/read and clear/delete for me.
5. Delete a sender-owned message for everyone and verify peer realtime/history.
6. Forward to several authorized conversations and verify no duplicate retries.
7. Share a Short through the Shorts feature endpoint and open it natively from `SHORT-*`.
8. Start a Live, call `live.share_live_to_chat`, verify native `LIVE-*` message, preview and web/mobile native navigation.
9. End the Live; reload history and verify ended preview remains when allowed.
10. Block the peer; verify new sends/open, presence and inaccessible shared-object disclosure are denied/suppressed.

## Failure/concurrency tests

- simultaneous conversation open -> one canonical pair row;
- concurrent identical send -> one idempotent message;
- edit versus delete, reaction/star versus delete, forward versus delete;
- outer transaction rollback after successful Chat call -> Chat/outbox/realtime callbacks do not escape;
- invalid/unknown fields, malformed IDs, oversized lists/body/depth;
- deleted/suspended recipient;
- Short/Live visibility change between share attempt and serialization;
- notification/websocket failure isolation;
- migration rerun on populated data.
