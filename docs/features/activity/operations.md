# Activity operations

## Activity Center ownership

Activity is a best-effort presentation projection. Ads, Shorts, Social/Search, and Live remain authoritative. Failure to write Activity history must not roll back an otherwise valid owning-domain action; producer hooks catch Activity failures and log a generic operational error.

## Hide vs clear

- **Hide** removes one item from the user's active Activity Center.
- **Clear** removes all matching active history from the user's Activity Center in bounded batches.

Both are logical terminal transitions, not physical deletion. If the same real-world action happens later, a new active history row can be recorded.

## Account deletion

A deleted account's own Activity rows are physically removed because Activity is personalization/history rather than retained moderation audit. Other users' profile-history snapshots referencing the deleted account, plus snapshots of Ads/Shorts/Lives made unavailable by the deletion, are hidden and scrubbed of title/image/metadata.

## Monitoring

Use the `aos.activity` logger for lifecycle outcomes. Logs intentionally omit user identities, search text and activity metadata. Migration logs contain aggregate reconciliation counts only.
