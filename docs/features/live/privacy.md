# Live Privacy and Social Policy

## Actual visibility

The inspected Live Stream model has no visibility/audience field. Current supported visibility is public/everyone, including guest access, subject to host/account availability and bidirectional blocking. Followers-only, friends-only, private, and invite-only modes are not silently inferred.

## Central policy

`LivePolicy` uses the canonical Social repository/account state and canonical bidirectional block helper. It is applied to:

- discovery feed;
- direct Live detail;
- join and token issuance;
- viewer tracking and participant interactions;
- comments/replies/reactions;
- co-host invitation/request/read/accept/token paths;
- webhook presence refresh;
- follower notification fanout.

## Block behavior

For an authenticated blocked relationship in either direction, the backend prevents discovery, direct lookup, new tokens, viewer interaction, co-host workflows, follower notifications, and private workflow reads. Feed filtering is performed in SQL before pagination. Direct lookup uses the same policy and may return non-enumerable not-found behavior.

A tracked participant that becomes blocked or unavailable is closed when observed by webhook/reconciliation and queued for LiveKit removal. Short token TTL and fresh policy checks prevent new authorization.

## Account state

Disabled, deleted, suspended, and non-active hosts are excluded from feed/direct access and are ended by reconciliation. Disabled/deleted/suspended/non-active viewers cannot obtain new tokens or interact. Account deletion closes hosted Lives, viewer sessions, co-host state, profile indicators, notifications/activity through existing cleanup, and queues room shutdown.

## Public payloads

Public Live payloads use canonical `LIVE-*` and public `ACC-*` identifiers. Serializers do not intentionally expose email, internal User name, DocType names, DB row internals beyond established opaque workflow/message IDs, tokens, secrets, or moderation state.
