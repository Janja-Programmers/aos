# Analytics

Stored counters include impressions, qualified views, likes, comments, shares, saves, downloads and reposts. Daily metrics add watch time, average watch time and completion rate.

Client totals are never accepted. Event IDs and daily actor uniqueness prevent replay inflation. Watch time is capped by server-known duration. Counters are exact for synchronous idempotent interactions and reconciled periodically; pipeline-derived aggregates are eventually consistent.

Creator analytics endpoints resolve public account IDs internally and never expose User names. Per-Short analytics is owner/authorized-only. Maintenance and migration reconciliation can safely restore likes, saves, comments and repost counts from source rows.

Retention of raw event/pipeline data follows the existing analytics-pipeline configuration; no new product retention duration was invented.
