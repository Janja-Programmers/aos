# Activity operations

## Activity Center ownership

Activity is a best-effort presentation projection. Ads, Shorts, Social/Search, and Live remain authoritative. Failure to write Activity history must not roll back an otherwise valid owning-domain action; producer hooks catch Activity failures and log a generic operational error.

## Hide vs clear

- **Hide** removes one item from the user's active Activity Center.
- **Clear** removes all matching active history from the user's Activity Center in bounded batches.

Both are logical terminal transitions, not physical deletion. If the same real-world action happens later, a new active history row can be recorded.

## Account deletion

During the 30-day recoverable deletion window, Activity rows are preserved. After the restore deadline, permanent cleanup removes the deleted account's private Activity history and redacts retained snapshots that reference the permanently deleted profile/content. Cleanup is background-owned rather than part of the synchronous delete request.

## Monitoring

Use the `aos.activity` logger for lifecycle outcomes. Logs intentionally omit user identities, search text and activity metadata. Migration logs contain aggregate reconciliation counts only.
