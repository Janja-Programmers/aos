# Feeds and discovery

Every feed starts from the same eligibility predicates: ready, visible, publicly approved, active creator, no block in either direction and audience access.

The external ranking service is advisory. Candidate IDs boost/order matching rows but never become an exhaustive filter. If the service is empty or unavailable, the database returns the complete deterministic fallback. This corrects the former defect where All could contain fewer rows than a tag/vibe tab.

Ordering uses a stable score/time/name tie-breaker and the cursor uses the identical null-safe score expression. Following membership uses `EXISTS`, so duplicate legacy relationship rows cannot duplicate Shorts.

Joins are either unique or deduplicated, creator/media/relationship state is loaded in batches, and the final policy filter prevents nested leakage. Page size is bounded by endpoint constants.

A standalone hashtag search or Friends feed was not invented because no public endpoint/data contract exists in this backend. Existing content-mode (including vibes), profile, saved, liked, reposted, sound and ad feeds are preserved.
