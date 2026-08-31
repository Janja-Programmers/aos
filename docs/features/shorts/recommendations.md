# Shorts recommendations

`feed_for_you` is recommendation-first and keeps the existing `ranking_score` as a quality feature/fail-open fallback. Recommendation state never grants access: the canonical Shorts audience and Social visibility policy is applied again before any candidate is serialized.

## Serving pipeline

1. Build a bounded actor profile from durable AOS interaction tables.
2. Generate candidates from global quality/freshness, creator affinity, sound affinity, collaborative likes, and the existing external Shorts candidate provider.
3. Score candidates using personal affinity, global ranking quality, source support and freshness.
4. Rerank for creator/sound/mode diversity and controlled exploration.
5. Suppress explicit `not_interested`, hidden-creator and hidden-sound preferences and strongly deprioritize recently seen Shorts.
6. Store the ordered candidate list as a short-lived Redis feed session and paginate it with a signed `short_recommendation` cursor.
7. Apply canonical SQL audience predicates and the final batched Social/privacy policy before returning rows.

If recommendation generation or Redis is unavailable, `feed_for_you` falls open to the previous deterministic `ranking_score / creation / name` feed. Ranking and recommendation are therefore complementary rather than competing systems.

## Signals

The profile uses existing SSOT data rather than inventing duplicate analytics events:

- `AOS Short View`: watch ratio, early skips and completion;
- current likes, saves, reposts and comments;
- share and impression events;
- creator, content-mode, hashtag and sound affinity derived from interacted Shorts;
- collaborative candidates from users whose likes overlap the actor's strong-positive Shorts;
- explicit recommendation feedback from `recommendation_feedback`.

Watch completion is weighted more strongly than a shallow impression. Recent behavior is decayed so the feed can adapt, while explicit curation (`not_interested`, `hide_creator`, `hide_sound`) is intentionally long-lived.

Authenticated users are keyed by account. Guests can send a stable opaque `session_id`; raw account/session identifiers are not used in Redis recommendation keys.

## Candidate diversity and exploration

The reranker penalizes immediate/recent repeats from the same creator, repeated sounds and over-concentration of one content mode. Roughly every seventh slot can receive a controlled exploration bonus for a fresh, low-affinity candidate. This gives new topics/creators a path into the feed without allowing exploration to dominate known interests.

Recently seen Shorts are a fallback pool rather than the primary pool so small catalogs still function without making normal feeds repetitive.

## Explicit feedback

`POST /api/method/aos.api.v1.shorts.recommendation_feedback`

Supported actions:

- `not_interested`
- `hide_creator`
- `hide_sound`

The endpoint is guest-capable with the standard guest `session_id` requirement, rate-limited, deduplicated by the Shorts event key, and invalidates the actor's cached recommendation profile after recording durable feedback.

## Evolution path

This implementation is deliberately an online deterministic recommender, not a claim of a TikTok-scale neural model. It establishes stable candidate/profile/feedback boundaries so a learned scorer or semantic-vector candidate provider can later replace or augment individual candidate generators without changing the public feed contract.
