# Operations

Monitor bounded events under logger `aos.reviews`, moderation/outbox backlog, `Pending` review age, failed moderation jobs and aggregate reconciliation drift.

Recommended smoke sequence after deployment:

1. Create an active buyer and seller with a real conversation/message.
2. Upload `review_image` Media and confirm it.
3. Create a review and verify `Pending`, moderation job and outbox row share one transaction.
4. Apply an allow callback and verify `Approved`, ad/seller aggregates and public listing.
5. Edit the review, then deliver the older callback; verify it cannot publish the edit.
6. Exercise reaction add/switch/remove and duplicate reporting.
7. Withdraw the review and verify public removal, Media release and aggregate repair.

No new environment variable is required. Reviews uses the existing moderation, Media, Redis and outbox configuration.
