# Architecture

## Layers

- `aos/api/v1/social/__init__.py`: stable whitelisted routes only.
- `aos/api/social/*.py`: authentication, endpoint rate limits, response messages, and compatibility helpers.
- `aos/services/social/api.py`: exception-to-public-response boundary.
- `service.py`: relationship state transitions and transaction orchestration.
- `repository.py`: bounded reads/writes and parameterized SQL.
- `validation.py`: unknown-field rejection, alias conflict detection, scalar bounds, and signed cursors.
- `policy.py`: actor/target lifecycle and self-action rules.
- `serializers.py`: batched public projections with no email or internal document names; shared identity/live/media resolution is set-based to avoid list N+1 reads.
- `observability.py`: bounded structured events with no identifiers or user text.

## Transaction boundary

Services and migrations never call `frappe.db.commit()`. Frappe’s request/test/deployment caller owns commit. API error boundaries roll back failed mutations. A new follow, persistent notification, delivery job, and transactional-outbox row are created in one transaction.

## Database boundaries

`unique_aos_follow_pair(follower_user, following_user)` prevents duplicate edges. `unique_aos_user_block_active_pair(active_pair_key)` prevents duplicate active blocks while retaining unblocked history. Indexes cover both edge directions, block direction/status, deterministic ordering, and discovery state.

## Compatibility

`toggle_follow` still toggles when `action` is omitted. `action=follow|unfollow` adds idempotent set semantics. Legacy `target_user` remains accepted; new clients should send canonical `account_id`. Public output always uses `ACC-*` identifiers.
