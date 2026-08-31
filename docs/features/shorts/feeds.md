# Feeds and discovery

Every feed starts from the same eligibility predicates: ready, visible, publicly approved, active creator, no block in either direction and audience access.

## For You

For You is recommendation-first. `RecommendationService` produces a bounded personalized candidate session from durable watch/engagement history, creator/mode/hashtag/sound affinity, collaborative behavior, global quality/freshness and controlled exploration. The existing `ranking_score` remains an important quality feature and is also the deterministic fail-open feed if recommendation/Redis is unavailable.

Recommendation candidates are ordering inputs only. They never bypass authorization: candidate rows still pass the canonical SQL audience boundary and final batched Social/privacy filter before serialization.

Pagination uses an HMAC-signed recommendation cursor bound to the actor, mode and short-lived Redis feed session. Feed sessions are intentionally bounded so long sessions periodically regenerate from newer behavior rather than freezing personalization for hundreds of items.

See [recommendations.md](recommendations.md) for signals, feedback and diversity behavior.

## Following and other feeds

Following remains a relationship/recency-quality feed rather than a personalized For You feed. Following membership uses `EXISTS`, so duplicate legacy relationship rows cannot duplicate Shorts. Ad, profile, saved, liked, reposted and sound feeds retain their existing contracts.

Joins are either unique or deduplicated, creator/media/relationship state is loaded in batches, and the final policy filter prevents nested leakage. Page size is bounded by endpoint constants.

A standalone hashtag search or Friends feed was not invented because no public endpoint/data contract exists in this backend.

## Mode feeds

Shop, Geo, Vibes, and Learn filters use the server-assigned `content_mode`. For You personalization is applied within the requested mode. All does not depend on classification confidence and always considers every otherwise-accessible mode, so an unavailable classifier cannot remove a Short from All. Shorts are hidden until ready/moderated, so provisional classification is not exposed in public mode feeds.
