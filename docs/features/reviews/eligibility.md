# Eligibility

Eligibility is derived entirely by the server:

1. The session account must be active and enabled.
2. The ad must exist and be `Active`.
3. The linked seller must exist and be `Active`.
4. The reviewer must not own the seller/ad.
5. Neither party may have an active block against the other.
6. No review may already exist for the reviewer/ad pair, including a withdrawn review.
7. A canonical AOS Conversation with at least one AOS Message must exist between reviewer and seller.

The persisted basis is `communication`; the private conversation reference is audit evidence and is never included in public serialization.

Client flags such as `verified_purchase`, `can_review`, `reviewer`, `seller`, or `completed_order` are unsupported and rejected. There is no expiry window because the current product model has no completed transaction timestamp. Adding a review window requires a future verified-transaction model and a versioned policy change.
