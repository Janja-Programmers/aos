# Security

## Input safety

Every endpoint rejects unknown fields. Aliases are normalized and conflicting values fail closed. Limits, offsets, actions, reasons, and search text are strongly bounded. Cursors contain versioned JSON plus a site-secret HMAC; malformed, cross-query, cross-list, or modified cursors return `SOCIAL_INVALID_CURSOR`.

## Authorization

Authentication and active-actor policy are mandatory. Self-follow and self-block are rejected. Target lifecycle checks use a generic unavailable response. Block enforcement is bidirectional.

## Concurrency

Every pair mutation locks both User rows in lexical order before reading or changing follow/block state. This serializes concurrent follow-versus-block races without broad table locks. First-insert races additionally converge through unique indexes. Blocks use an active-only unique key. Counter values are recalculated from graph rows instead of trusting client or incremental state.

## SQL and enumeration

All user values are parameters. The only SQL interpolation is server-owned query structure selected from fixed modes. Queries are bounded and deterministically ordered. Search does not distinguish disabled, suspended, deleted, missing, or blocked targets in result sets.

## Abuse controls

Mutation, relationship, list, discovery, and block endpoints each use repository rate limits. Explicit follow/unfollow idempotency reduces retries and churn. Follow notifications are created only for a newly inserted edge and are deduplicated for five minutes. Follower-wide Live/Shorts event fanout is filtered for active/block-compatible recipients and capped at 500 recipients per originating action.
