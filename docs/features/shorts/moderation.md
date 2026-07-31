# Moderation and reports

Public eligibility accepts only the established approved states and excludes hidden, flagged and rejected Shorts. Moderation reasons, reviewer identities and internal DocType/job names are not serialized publicly.

A reporter can have one active Reviewing report per Short. Concurrent duplicates are stopped by a fixed-size active-key unique index. Historical rejected/resolved rows remain available to moderators without blocking future valid reports.

The existing moderation companion and transactional outbox remain authoritative for automated decisions. Callback authentication/replay controls are shared infrastructure. Appeals, copyright adjudication and restoration workflows were not added because no compatible product state or endpoint exists.
