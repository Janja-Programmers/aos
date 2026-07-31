# Relationship lifecycle

## Follow

`none -> following` inserts one unique edge. Repeating `action=follow` is a successful idempotent no-op. `toggle` preserves the original storefront/mobile behavior. `following + reciprocal edge -> friends` is a computed projection, not stored state.

## Unfollow and remove friend

`action=unfollow` removes only the viewer’s outgoing edge. If reciprocal edges existed, the other edge remains and the projection becomes `followed_by`. This is the existing AOS interpretation of removing a friend.

## Block

A block is directional. Activation and removal of both follow directions occur in the same transaction, followed by exact counter reconciliation. Repeated block is idempotent and may update the blocker-private reason. Unblock never restores deleted edges.

## Account lifecycle

Only enabled profiles with `account_status=Active` and `is_deleted=0` appear in discovery or active lists and can receive new follows/blocks. Existing account-deletion cleanup removes graph rows, closes active blocks involving the deleted account, releases block uniqueness keys, and reconciles affected counters. Suspended/deactivated/deleted targets return the non-enumerating `SOCIAL_PROFILE_UNAVAILABLE` response.

## Unsupported states

There is no friend-request state machine, follow approval, private-account field, or pending relationship DocType in the current source of truth. Those remain intentionally deferred until a product/data-model contract is introduced.
