# Relationship lifecycle

## Follow

`none -> following` inserts one unique edge. Repeating `action=follow` is a successful idempotent no-op. `toggle` preserves the original storefront/mobile behavior. `following + reciprocal edge -> friends` is a computed projection, not stored state.

## Unfollow and remove friend

`action=unfollow` removes only the viewer’s outgoing edge. If reciprocal edges existed, the other edge remains and the projection becomes `followed_by`. This is the existing AOS interpretation of removing a friend.

## Block

A block is directional. Activation and removal of both follow directions occur in the same transaction, followed by exact counter reconciliation. Repeated block is idempotent and may update the blocker-private reason. Unblock never restores deleted edges.

## Account lifecycle

Only enabled profiles with `account_status=Active` and `is_deleted=0` appear in discovery or active lists and can receive new follows/blocks. Recoverable account deletion preserves existing follow edges, counters, and active blocks for 30 days; the account tombstone makes the graph unavailable without rewriting those rows. Permanent cleanup after restore expiry removes follow edges and closes active blocks in bounded batches. Suspended/deactivated/deleted targets return the non-enumerating `SOCIAL_PROFILE_UNAVAILABLE` response.

## Unsupported states

There is no friend-request state machine, follow approval, private-account field, or pending relationship DocType in the current source of truth. Those remain intentionally deferred until a product/data-model contract is introduced.
