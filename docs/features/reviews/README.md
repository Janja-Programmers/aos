# Reviews

Reviews is an ad-scoped marketplace trust feature. A logged-in AOS user may submit one review for an active ad only after AOS has evidence that the user communicated with the ad's seller through the canonical Conversation/Message model. The review contributes to both the ad and seller rating after moderation approves it.

The repository currently has no Orders, completed-purchase, booking, refund, or dispute model connected to Reviews. Therefore, `verified_interaction` means **server-verified AOS communication**, not verified purchase. This preserves the existing product rule without fabricating transaction verification. It remains weaker than completed-order eligibility and is documented as a residual abuse risk.

## Production boundaries

- Reviews owns eligibility, content validation, lifecycle, duplicate prevention, reactions, reports, serialization, moderation generation, and aggregate reconciliation.
- Accounts owns public identity and deleted-account anonymisation.
- Media owns upload validation, storage, ownership, attachment and release.
- Seller owns seller state. Suspended/deleted sellers cannot receive new reviews.
- Moderation owns classification. Reviews owns generation-safe lifecycle application.
- Transactional Outbox owns reliable moderation dispatch.

Public lifecycle: `Pending → Approved | Rejected`; approved content may be `Hidden` by moderation/administration and authors may soft-delete it as `Withdrawn`. Public aggregates include `Approved` only.
