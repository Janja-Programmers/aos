"""Personalized recommendation engine for AOS Shorts.

The global :class:`RankingService` answers "is this Short broadly good?".  This
module answers "is this Short a good next item for *this actor* right now?".

Design goals:
- keep the public feed contract stable;
- use durable AOS interaction tables as the source of truth;
- keep hot recommendation profiles/feed sessions in Redis only as caches;
- degrade to the existing global ranked feed if Redis or recommendation work
  is unavailable;
- bound every SQL query and every Redis payload;
- mix quality, personal affinity, collaborative discovery and exploration;
- never make recommendation state an authorization boundary.  The feed API
  still applies the canonical Shorts visibility policy to the returned rows.

This is intentionally a deterministic online recommender rather than a large
ML model.  It creates the data/serving boundary needed for a later learned
ranker or semantic vector candidate provider without requiring expensive
infrastructure on day one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import time
import uuid
from typing import Any, Iterable

import frappe
from frappe.utils import add_days, now_datetime

from aos.api.shorts.utils import decode_cursor, encode_cursor
from aos.services.search_ranking_service import short_feed_candidates
from aos.services.shorts.constants import (
    RECOMMENDATION_CANDIDATE_POOL_SIZE,
    RECOMMENDATION_COLLABORATIVE_CANDIDATES,
    RECOMMENDATION_FEED_SESSION_TTL_SECONDS,
    RECOMMENDATION_FEED_SESSION_MAX_ITEMS,
    RECOMMENDATION_FRESH_CANDIDATES,
    RECOMMENDATION_HISTORY_LOOKBACK_DAYS,
    RECOMMENDATION_HISTORY_MAX_ROWS,
    RECOMMENDATION_PROFILE_CACHE_TTL_SECONDS,
    RECOMMENDATION_QUALITY_CANDIDATES,
    RECOMMENDATION_RECENT_SEEN_DAYS,
    RECOMMENDATION_SESSION_WINDOW_MULTIPLIER,
)

_CURSOR_KIND = "short_recommendation"
_CACHE_PREFIX = "aos:shorts:recommendation"


@dataclass(slots=True)
class RecommendationProfile:
    mode_scores: dict[str, float] = field(default_factory=dict)
    creator_scores: dict[str, float] = field(default_factory=dict)
    hashtag_scores: dict[str, float] = field(default_factory=dict)
    sound_scores: dict[str, float] = field(default_factory=dict)
    short_scores: dict[str, float] = field(default_factory=dict)
    positive_short_ids: list[str] = field(default_factory=list)
    seen_short_ids: set[str] = field(default_factory=set)
    suppressed_short_ids: set[str] = field(default_factory=set)
    hidden_creators: set[str] = field(default_factory=set)
    hidden_sounds: set[str] = field(default_factory=set)
    personalized: bool = False

    def as_cache_payload(self) -> dict[str, Any]:
        return {
            "mode_scores": self.mode_scores,
            "creator_scores": self.creator_scores,
            "hashtag_scores": self.hashtag_scores,
            "sound_scores": self.sound_scores,
            "short_scores": self.short_scores,
            "positive_short_ids": self.positive_short_ids,
            "seen_short_ids": sorted(self.seen_short_ids),
            "suppressed_short_ids": sorted(self.suppressed_short_ids),
            "hidden_creators": sorted(self.hidden_creators),
            "hidden_sounds": sorted(self.hidden_sounds),
            "personalized": self.personalized,
        }

    @classmethod
    def from_cache_payload(cls, payload: object) -> "RecommendationProfile | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                mode_scores=_float_map(payload.get("mode_scores")),
                creator_scores=_float_map(payload.get("creator_scores")),
                hashtag_scores=_float_map(payload.get("hashtag_scores")),
                sound_scores=_float_map(payload.get("sound_scores")),
                short_scores=_float_map(payload.get("short_scores")),
                positive_short_ids=_string_list(payload.get("positive_short_ids"), 80),
                seen_short_ids=set(_string_list(payload.get("seen_short_ids"), 1000)),
                suppressed_short_ids=set(_string_list(payload.get("suppressed_short_ids"), 1000)),
                hidden_creators=set(_string_list(payload.get("hidden_creators"), 300)),
                hidden_sounds=set(_string_list(payload.get("hidden_sounds"), 300)),
                personalized=bool(payload.get("personalized")),
            )
        except Exception:
            return None


@dataclass(slots=True)
class RecommendationSlice:
    feed_id: str
    ids: list[str]
    offset: int
    total: int
    personalized: bool


class RecommendationService:
    """Bounded online Shorts recommender."""

    @classmethod
    def invalidate_profile(cls, *, user: str | None, session_id: str | None) -> None:
        actor_hash = _actor_hash(user=user, session_id=session_id)
        if not actor_hash:
            return
        try:
            frappe.cache().delete_value(_profile_cache_key(actor_hash))
        except Exception:
            # Recommendation cache invalidation is never allowed to fail the
            # primary engagement transaction.
            pass

    @classmethod
    def get_slice(
        cls,
        *,
        viewer: str | None,
        session_id: str | None,
        content_mode: str | None,
        cursor: str | None,
        limit: int,
    ) -> RecommendationSlice | None:
        """Return a stable candidate slice for one feed request.

        ``None`` means the caller should use the canonical global-ranking
        fallback.  A legacy ranking cursor is deliberately left to that path.
        """
        actor_hash = _actor_hash(user=viewer, session_id=session_id) or "anonymous"
        mode = _normalize_mode(content_mode)

        if cursor:
            data = decode_cursor(cursor) or {}
            if data.get("kind") != _CURSOR_KIND:
                return None
            if str(data.get("actor") or "") != actor_hash:
                return None
            if str(data.get("mode") or "all") != mode:
                return None
            feed_id = str(data.get("feed_id") or "")
            offset = max(0, int(data.get("offset") or 0))
            session = cls._load_feed_session(feed_id)
            if session and session.get("actor") == actor_hash and session.get("mode") == mode:
                ids = _string_list(session.get("ids"), RECOMMENDATION_CANDIDATE_POOL_SIZE)
                window = max(limit + 1, limit * RECOMMENDATION_SESSION_WINDOW_MULTIPLIER)
                return RecommendationSlice(
                    feed_id=feed_id,
                    ids=ids[offset : offset + window],
                    offset=offset,
                    total=len(ids),
                    personalized=bool(session.get("personalized")),
                )
            # Redis feed sessions are a cache.  If one expires mid-scroll,
            # regenerate from the actor's now-current durable history.  Seen
            # suppression makes repeats unlikely without failing pagination.

        profile = cls.profile_for(user=viewer, session_id=session_id)
        ids = cls._build_candidates(
            viewer=viewer,
            content_mode=mode,
            profile=profile,
        )
        if not ids:
            return None

        feed_id = uuid.uuid4().hex[:24]
        if not cls._store_feed_session(
            feed_id,
            {
                "actor": actor_hash,
                "mode": mode,
                "ids": ids,
                "personalized": profile.personalized,
                "created_at": int(time.time()),
            },
        ):
            return None

        window = max(limit + 1, limit * RECOMMENDATION_SESSION_WINDOW_MULTIPLIER)
        return RecommendationSlice(
            feed_id=feed_id,
            ids=ids[:window],
            offset=0,
            total=len(ids),
            personalized=profile.personalized,
        )

    @classmethod
    def build_cursor(
        cls,
        *,
        feed_id: str,
        next_offset: int,
        viewer: str | None,
        session_id: str | None,
        content_mode: str | None,
    ) -> str:
        return encode_cursor(
            {
                "kind": _CURSOR_KIND,
                "feed_id": feed_id,
                "offset": max(0, int(next_offset)),
                "actor": _actor_hash(user=viewer, session_id=session_id) or "anonymous",
                "mode": _normalize_mode(content_mode),
            }
        )

    @classmethod
    def profile_for(cls, *, user: str | None, session_id: str | None) -> RecommendationProfile:
        actor_hash = _actor_hash(user=user, session_id=session_id)
        if not actor_hash:
            return RecommendationProfile()

        key = _profile_cache_key(actor_hash)
        try:
            cached = RecommendationProfile.from_cache_payload(frappe.cache().get_value(key))
            if cached is not None:
                return cached
        except Exception:
            pass

        profile = cls._build_profile(user=user, session_id=session_id)
        try:
            frappe.cache().set_value(
                key,
                profile.as_cache_payload(),
                expires_in_sec=RECOMMENDATION_PROFILE_CACHE_TTL_SECONDS,
            )
        except Exception:
            pass
        return profile

    @classmethod
    def _build_profile(cls, *, user: str | None, session_id: str | None) -> RecommendationProfile:
        profile = RecommendationProfile()
        if not user and not session_id:
            return profile

        cutoff = add_days(now_datetime(), -RECOMMENDATION_HISTORY_LOOKBACK_DAYS)
        recent_seen_cutoff = add_days(now_datetime(), -RECOMMENDATION_RECENT_SEEN_DAYS)
        signal_by_short: dict[str, float] = {}
        signal_time: dict[str, datetime | None] = {}
        viewed_ids: set[str] = set()

        actor_clause, actor_params = _actor_sql(user=user, session_id=session_id, alias="v")
        views = frappe.db.sql(
            f"""
            SELECT v.short, v.watch_ms, v.last_seen_at,
                   s.duration_seconds
            FROM `tabAOS Short View` v
            INNER JOIN `tabAOS Short` s ON s.name = v.short
            WHERE {actor_clause}
              AND v.last_seen_at >= %s
            ORDER BY v.last_seen_at DESC, v.name DESC
            LIMIT %s
            """,
            (*actor_params, cutoff, RECOMMENDATION_HISTORY_MAX_ROWS),
            as_dict=True,
        )
        for row in views:
            short_id = str(row.get("short") or "")
            if not short_id:
                continue
            viewed_ids.add(short_id)
            watched = max(0, int(row.get("watch_ms") or 0))
            duration_ms = max(1, int(float(row.get("duration_seconds") or 0) * 1000))
            ratio = min(1.25, watched / duration_ms) if duration_ms > 1 else 0.0
            base = _watch_signal(ratio, watched)
            weight = base * _recency_weight(row.get("last_seen_at"), half_life_days=12.0)
            signal_by_short[short_id] = signal_by_short.get(short_id, 0.0) + weight
            signal_time[short_id] = row.get("last_seen_at")
            if row.get("last_seen_at") and row.get("last_seen_at") >= recent_seen_cutoff:
                profile.seen_short_ids.add(short_id)

        # Current-state explicit positive actions are stronger than passive
        # watch behaviour.  These tables naturally stop contributing after an
        # unlike/unsave/repost removal.
        if user:
            action_specs = (
                ("AOS Short Like", "", 4.0),
                ("AOS Short Save", "", 6.0),
                ("AOS Short Repost", "AND status = 'active'", 7.0),
                ("AOS Short Comment", "AND status = 'active'", 5.0),
            )
            for doctype, extra, base_weight in action_specs:
                rows = frappe.db.sql(
                    f"""
                    SELECT short, creation
                    FROM `tab{doctype}`
                    WHERE user = %s
                      AND creation >= %s
                      {extra}
                    ORDER BY creation DESC, name DESC
                    LIMIT %s
                    """,
                    (user, cutoff, RECOMMENDATION_HISTORY_MAX_ROWS),
                    as_dict=True,
                )
                for row in rows:
                    short_id = str(row.get("short") or "")
                    if not short_id:
                        continue
                    signal_by_short[short_id] = signal_by_short.get(short_id, 0.0) + (
                        base_weight * _recency_weight(row.get("creation"), half_life_days=20.0)
                    )
                    signal_time[short_id] = _newer(signal_time.get(short_id), row.get("creation"))

        event_actor_clause, event_actor_params = _actor_sql(user=user, session_id=session_id, alias="e")
        events = frappe.db.sql(
            f"""
            SELECT e.short, e.event_type, e.creation
            FROM `tabAOS Short Event` e
            WHERE {event_actor_clause}
              AND e.creation >= %s
              AND e.event_type IN ('impression', 'share')
            ORDER BY e.creation DESC, e.name DESC
            LIMIT %s
            """,
            (*event_actor_params, cutoff, RECOMMENDATION_HISTORY_MAX_ROWS),
            as_dict=True,
        )
        impression_ids: set[str] = set()
        for row in events:
            short_id = str(row.get("short") or "")
            event_type = str(row.get("event_type") or "")
            if not short_id:
                continue
            if event_type == "share":
                signal_by_short[short_id] = signal_by_short.get(short_id, 0.0) + (
                    7.0 * _recency_weight(row.get("creation"), half_life_days=24.0)
                )
            elif event_type == "impression":
                impression_ids.add(short_id)
                if row.get("creation") and row.get("creation") >= recent_seen_cutoff:
                    profile.seen_short_ids.add(short_id)

        # Explicit curation is intentionally long-lived instead of sharing the
        # 60-day behavioral window. A user who says "not interested" should not
        # see the Short return simply because time passed.
        preference_events = frappe.db.sql(
            f"""
            SELECT e.short, e.event_type
            FROM `tabAOS Short Event` e
            WHERE {event_actor_clause}
              AND e.event_type IN ('not_interested', 'hide_creator', 'hide_sound')
            ORDER BY e.creation DESC, e.name DESC
            LIMIT 1000
            """,
            event_actor_params,
            as_dict=True,
        )
        hide_creator_short_ids: set[str] = set()
        hide_sound_short_ids: set[str] = set()
        for row in preference_events:
            short_id = str(row.get("short") or "")
            event_type = str(row.get("event_type") or "")
            if not short_id:
                continue
            if event_type == "not_interested":
                profile.suppressed_short_ids.add(short_id)
                signal_by_short[short_id] = min(signal_by_short.get(short_id, 0.0), -20.0)
            elif event_type == "hide_creator":
                hide_creator_short_ids.add(short_id)
            elif event_type == "hide_sound":
                hide_sound_short_ids.add(short_id)

        # An impression with no corresponding view is a weak skip signal.  It
        # must not outweigh explicit positive actions or a completed watch.
        for short_id in impression_ids - viewed_ids:
            signal_by_short[short_id] = signal_by_short.get(short_id, 0.0) - 0.8

        feature_ids = set(signal_by_short) | hide_creator_short_ids | hide_sound_short_ids
        feature_rows = cls._load_short_features(feature_ids)
        by_id = {str(row.get("name")): row for row in feature_rows}

        for short_id, score in signal_by_short.items():
            profile.short_scores[short_id] = score
            row = by_id.get(short_id)
            if not row:
                continue
            _add_score(profile.mode_scores, row.get("content_mode"), score)
            _add_score(profile.creator_scores, row.get("owner"), score)
            for tag in _hashtags(row.get("hashtags")):
                _add_score(profile.hashtag_scores, tag, score * 0.65)
            _add_score(profile.sound_scores, row.get("sound_id"), score * 0.8)

        for short_id in hide_creator_short_ids:
            owner = (by_id.get(short_id) or {}).get("owner")
            if owner:
                profile.hidden_creators.add(str(owner))
        for short_id in hide_sound_short_ids:
            sound_id = (by_id.get(short_id) or {}).get("sound_id")
            if sound_id:
                profile.hidden_sounds.add(str(sound_id))

        positives = [
            (short_id, score, signal_time.get(short_id))
            for short_id, score in signal_by_short.items()
            if score >= 2.0 and short_id not in profile.suppressed_short_ids
        ]
        positives.sort(key=lambda item: (item[1], _timestamp(item[2])), reverse=True)
        profile.positive_short_ids = [item[0] for item in positives[:40]]
        profile.personalized = bool(
            profile.positive_short_ids
            or profile.suppressed_short_ids
            or profile.hidden_creators
            or profile.hidden_sounds
            or any(abs(score) >= 1.0 for score in profile.short_scores.values())
        )

        _trim_score_map(profile.mode_scores, 8)
        _trim_score_map(profile.creator_scores, 24)
        _trim_score_map(profile.hashtag_scores, 40)
        _trim_score_map(profile.sound_scores, 20)
        _trim_score_map(profile.short_scores, 120)
        return profile

    @classmethod
    def _build_candidates(
        cls,
        *,
        viewer: str | None,
        content_mode: str,
        profile: RecommendationProfile,
    ) -> list[str]:
        mode_value = None if content_mode == "all" else content_mode
        source_boosts: dict[str, float] = {}
        candidate_ids: list[str] = []

        def add(ids: Iterable[str], *, boost: float) -> None:
            for rank, raw in enumerate(ids):
                short_id = str(raw or "").strip()
                if not short_id:
                    continue
                if short_id not in source_boosts:
                    candidate_ids.append(short_id)
                    source_boosts[short_id] = 0.0
                # Earlier positions inside a candidate generator receive a
                # small additional boost, bounded so one source cannot dominate.
                source_boosts[short_id] += max(0.0, boost - min(rank, 50) * 0.01)

        try:
            external = short_feed_candidates(
                viewer=viewer,
                content_mode=mode_value,
                limit=min(120, RECOMMENDATION_CANDIDATE_POOL_SIZE),
                offset=0,
            )
            add(external, boost=1.1)
        except Exception:
            # Existing external ranking/search service remains advisory.
            pass

        add(cls._quality_candidates(content_mode=mode_value), boost=0.5)
        add(cls._fresh_candidates(content_mode=mode_value), boost=0.45)

        positive_creators = [
            key for key, value in _sorted_positive(profile.creator_scores, 10) if value > 0
        ]
        if positive_creators:
            add(
                cls._creator_candidates(positive_creators, content_mode=mode_value),
                boost=1.8,
            )

        positive_sounds = [
            key for key, value in _sorted_positive(profile.sound_scores, 8) if value > 0
        ]
        if positive_sounds:
            add(
                cls._sound_candidates(positive_sounds, content_mode=mode_value),
                boost=1.35,
            )

        if viewer and profile.positive_short_ids:
            add(
                cls._collaborative_candidates(
                    viewer=viewer,
                    positive_short_ids=profile.positive_short_ids[:20],
                    content_mode=mode_value,
                ),
                boost=2.2,
            )

        # Hard bound before loading feature rows.  Keep sources interleaved by
        # retaining insertion order and then globally scoring them below.
        candidate_ids = candidate_ids[: RECOMMENDATION_CANDIDATE_POOL_SIZE * 2]
        rows = cls._load_candidate_features(candidate_ids, content_mode=mode_value)
        if not rows:
            return []

        row_by_id = {str(row.get("name")): row for row in rows}
        max_rank_log = max(
            [math.log1p(max(0.0, float(row.get("ranking_score") or 0.0))) for row in rows] or [1.0]
        )
        max_rank_log = max(1.0, max_rank_log)

        scored: list[dict[str, Any]] = []
        fallback_seen: list[dict[str, Any]] = []
        for short_id in candidate_ids:
            row = row_by_id.get(short_id)
            if not row:
                continue
            owner = str(row.get("owner") or "")
            sound_id = str(row.get("sound_id") or "")
            if short_id in profile.suppressed_short_ids:
                continue
            if owner and owner in profile.hidden_creators:
                continue
            if sound_id and sound_id in profile.hidden_sounds:
                continue
            if viewer and owner == viewer:
                continue

            affinity = cls._affinity(row, profile)
            quality = math.log1p(max(0.0, float(row.get("ranking_score") or 0.0))) / max_rank_log
            freshness = _freshness_score(row.get("creation"))
            score = (
                source_boosts.get(short_id, 0.0)
                + quality * 2.25
                + freshness * 0.85
                + affinity * 3.2
            )
            item = {
                "id": short_id,
                "score": score,
                "affinity": affinity,
                "freshness": freshness,
                "creator": owner,
                "sound": sound_id,
                "mode": str(row.get("content_mode") or ""),
            }
            if short_id in profile.seen_short_ids:
                # Recently seen content is a fallback pool, not mixed into the
                # primary recommendation list.  This prevents repetitive feeds
                # while still allowing a small catalog to keep functioning.
                item["score"] -= 4.0
                fallback_seen.append(item)
            else:
                scored.append(item)

        scored.sort(key=lambda row: (float(row["score"]), float(row["freshness"]), row["id"]), reverse=True)
        fallback_seen.sort(key=lambda row: (float(row["score"]), float(row["freshness"]), row["id"]), reverse=True)
        ordered = cls._diversify(scored)
        if len(ordered) < RECOMMENDATION_CANDIDATE_POOL_SIZE:
            ordered.extend(cls._diversify(fallback_seen))
        return [row["id"] for row in ordered[:RECOMMENDATION_FEED_SESSION_MAX_ITEMS]]

    @staticmethod
    def _affinity(row: dict[str, Any], profile: RecommendationProfile) -> float:
        mode = str(row.get("content_mode") or "")
        creator = str(row.get("owner") or "")
        sound = str(row.get("sound_id") or "")
        values = [
            _normalized_feature(profile.mode_scores, mode),
            _normalized_feature(profile.creator_scores, creator) * 1.35,
            _normalized_feature(profile.sound_scores, sound) * 0.9,
        ]
        tag_affinity = 0.0
        for tag in _hashtags(row.get("hashtags"))[:8]:
            tag_affinity += _normalized_feature(profile.hashtag_scores, tag)
        if tag_affinity:
            values.append(max(-1.5, min(1.5, tag_affinity * 0.55)))
        return max(-2.0, min(2.5, sum(values)))

    @classmethod
    def _diversify(cls, pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not pool:
            return []
        remaining = list(pool[: min(len(pool), RECOMMENDATION_CANDIDATE_POOL_SIZE * 2)])
        result: list[dict[str, Any]] = []
        recent_creators: list[str] = []
        recent_sounds: list[str] = []
        recent_modes: list[str] = []

        while remaining and len(result) < RECOMMENDATION_CANDIDATE_POOL_SIZE:
            best_index = 0
            best_value = -10_000.0
            for idx, item in enumerate(remaining[:80]):
                adjusted = float(item["score"])
                creator = str(item.get("creator") or "")
                sound = str(item.get("sound") or "")
                mode = str(item.get("mode") or "")
                if creator and recent_creators and creator == recent_creators[-1]:
                    adjusted -= 2.25
                elif creator and creator in recent_creators[-3:]:
                    adjusted -= 0.75
                if sound and recent_sounds and sound == recent_sounds[-1]:
                    adjusted -= 1.4
                if mode and recent_modes[-4:].count(mode) >= 3:
                    adjusted -= 0.7

                # Roughly every seventh slot, give a fresh low-affinity item a
                # controlled exploration bonus.  This keeps the system learning
                # and prevents a filter bubble without flooding the feed.
                if (len(result) + 1) % 7 == 0 and abs(float(item.get("affinity") or 0.0)) < 0.35:
                    adjusted += float(item.get("freshness") or 0.0) * 1.2 + 0.55

                if adjusted > best_value:
                    best_value = adjusted
                    best_index = idx

            chosen = remaining.pop(best_index)
            result.append(chosen)
            recent_creators.append(str(chosen.get("creator") or ""))
            recent_sounds.append(str(chosen.get("sound") or ""))
            recent_modes.append(str(chosen.get("mode") or ""))
        return result

    @staticmethod
    def _quality_candidates(*, content_mode: str | None) -> list[str]:
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [content_mode] if content_mode else []
        params.append(RECOMMENDATION_QUALITY_CANDIDATES)
        rows = frappe.db.sql(
            f"""
            SELECT s.name
            FROM `tabAOS Short` s
            WHERE s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            ORDER BY COALESCE(s.ranking_score, 0) DESC, s.creation DESC, s.name DESC
            LIMIT %s
            """,
            tuple(params),
            as_dict=True,
        )
        return [str(row.name) for row in rows if row.get("name")]

    @staticmethod
    def _fresh_candidates(*, content_mode: str | None) -> list[str]:
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [content_mode] if content_mode else []
        params.append(RECOMMENDATION_FRESH_CANDIDATES)
        rows = frappe.db.sql(
            f"""
            SELECT s.name
            FROM `tabAOS Short` s
            WHERE s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            ORDER BY s.creation DESC, s.name DESC
            LIMIT %s
            """,
            tuple(params),
            as_dict=True,
        )
        return [str(row.name) for row in rows if row.get("name")]

    @staticmethod
    def _creator_candidates(creators: list[str], *, content_mode: str | None) -> list[str]:
        if not creators:
            return []
        placeholders = ",".join(["%s"] * len(creators))
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [*creators]
        if content_mode:
            params.append(content_mode)
        params.append(min(160, RECOMMENDATION_CANDIDATE_POOL_SIZE))
        rows = frappe.db.sql(
            f"""
            SELECT s.name
            FROM `tabAOS Short` s
            WHERE s.owner IN ({placeholders})
              AND s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            ORDER BY COALESCE(s.ranking_score, 0) DESC, s.creation DESC, s.name DESC
            LIMIT %s
            """,
            tuple(params),
            as_dict=True,
        )
        return [str(row.name) for row in rows if row.get("name")]

    @staticmethod
    def _sound_candidates(sounds: list[str], *, content_mode: str | None) -> list[str]:
        if not sounds:
            return []
        placeholders = ",".join(["%s"] * len(sounds))
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [*sounds]
        if content_mode:
            params.append(content_mode)
        params.append(min(140, RECOMMENDATION_CANDIDATE_POOL_SIZE))
        rows = frappe.db.sql(
            f"""
            SELECT s.name
            FROM `tabAOS Short Sound` ss
            INNER JOIN `tabAOS Short` s ON s.name = ss.short
            WHERE ss.sound IN ({placeholders})
              AND s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            ORDER BY COALESCE(s.ranking_score, 0) DESC, s.creation DESC, s.name DESC
            LIMIT %s
            """,
            tuple(params),
            as_dict=True,
        )
        return [str(row.name) for row in rows if row.get("name")]

    @staticmethod
    def _collaborative_candidates(
        *,
        viewer: str,
        positive_short_ids: list[str],
        content_mode: str | None,
    ) -> list[str]:
        if not positive_short_ids:
            return []
        placeholders = ",".join(["%s"] * len(positive_short_ids))
        peers = frappe.db.sql(
            f"""
            SELECT lk.user, COUNT(*) AS overlap
            FROM `tabAOS Short Like` lk
            WHERE lk.short IN ({placeholders})
              AND lk.user <> %s
            GROUP BY lk.user
            ORDER BY overlap DESC, MAX(lk.creation) DESC
            LIMIT 30
            """,
            (*positive_short_ids, viewer),
            as_dict=True,
        )
        peer_users = [str(row.user) for row in peers if row.get("user")]
        if not peer_users:
            return []
        peer_placeholders = ",".join(["%s"] * len(peer_users))
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [*peer_users]
        if content_mode:
            params.append(content_mode)
        params.append(RECOMMENDATION_COLLABORATIVE_CANDIDATES)
        rows = frappe.db.sql(
            f"""
            SELECT lk.short AS name, COUNT(*) AS support
            FROM `tabAOS Short Like` lk
            INNER JOIN `tabAOS Short` s ON s.name = lk.short
            WHERE lk.user IN ({peer_placeholders})
              AND s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            GROUP BY lk.short
            ORDER BY support DESC, MAX(COALESCE(s.ranking_score, 0)) DESC, MAX(s.creation) DESC
            LIMIT %s
            """,
            tuple(params),
            as_dict=True,
        )
        return [str(row.name) for row in rows if row.get("name")]

    @staticmethod
    def _load_short_features(short_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [str(value) for value in dict.fromkeys(short_ids) if value]
        if not ids:
            return []
        ids = ids[: RECOMMENDATION_HISTORY_MAX_ROWS]
        placeholders = ",".join(["%s"] * len(ids))
        return frappe.db.sql(
            f"""
            SELECT s.name, s.owner, s.content_mode, s.hashtags,
                   ss.sound AS sound_id
            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Short Sound` ss ON ss.short = s.name
            WHERE s.name IN ({placeholders})
            """,
            tuple(ids),
            as_dict=True,
        )

    @staticmethod
    def _load_candidate_features(
        short_ids: Iterable[str],
        *,
        content_mode: str | None,
    ) -> list[dict[str, Any]]:
        ids = [str(value) for value in dict.fromkeys(short_ids) if value]
        if not ids:
            return []
        ids = ids[: RECOMMENDATION_CANDIDATE_POOL_SIZE * 2]
        placeholders = ",".join(["%s"] * len(ids))
        mode_clause = "AND s.content_mode = %s" if content_mode else ""
        params: list[Any] = [*ids]
        if content_mode:
            params.append(content_mode)
        return frappe.db.sql(
            f"""
            SELECT s.name, s.owner, s.content_mode, s.hashtags,
                   s.ranking_score, s.creation,
                   ss.sound AS sound_id
            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Short Sound` ss ON ss.short = s.name
            WHERE s.name IN ({placeholders})
              AND s.status = 'ready'
              AND s.visibility_status = 'visible'
              {mode_clause}
            """,
            tuple(params),
            as_dict=True,
        )

    @staticmethod
    def _store_feed_session(feed_id: str, payload: dict[str, Any]) -> bool:
        try:
            frappe.cache().set_value(
                _feed_cache_key(feed_id),
                payload,
                expires_in_sec=RECOMMENDATION_FEED_SESSION_TTL_SECONDS,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _load_feed_session(feed_id: str) -> dict[str, Any] | None:
        if not feed_id or len(feed_id) > 40:
            return None
        try:
            value = frappe.cache().get_value(_feed_cache_key(feed_id))
            return value if isinstance(value, dict) else None
        except Exception:
            return None


def _actor_hash(*, user: str | None, session_id: str | None) -> str | None:
    if user and user != "Guest":
        material = f"user:{user}"
    elif session_id:
        material = f"session:{session_id}"
    else:
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _actor_sql(*, user: str | None, session_id: str | None, alias: str) -> tuple[str, tuple[Any, ...]]:
    if user and user != "Guest":
        return f"{alias}.user = %s", (user,)
    return f"{alias}.session_id = %s", (session_id,)


def _profile_cache_key(actor_hash: str) -> str:
    return f"{_CACHE_PREFIX}:profile:{actor_hash}"


def _feed_cache_key(feed_id: str) -> str:
    return f"{_CACHE_PREFIX}:feed:{feed_id}"


def _normalize_mode(value: str | None) -> str:
    mode = str(value or "all").strip().lower()
    return mode or "all"


def _watch_signal(ratio: float, watch_ms: int) -> float:
    if watch_ms < 750 or ratio < 0.08:
        return -2.25
    if ratio < 0.20:
        return -1.25
    if ratio < 0.35:
        return -0.25
    if ratio < 0.60:
        return 1.25
    if ratio < 0.85:
        return 2.5
    if ratio < 0.98:
        return 4.0
    return 5.0


def _recency_weight(value: Any, *, half_life_days: float) -> float:
    if not value:
        return 0.5
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return 0.5
    try:
        age_seconds = max(0.0, (now_datetime() - value).total_seconds())
    except Exception:
        return 0.5
    age_days = age_seconds / 86400.0
    return math.exp(-math.log(2.0) * age_days / max(0.25, half_life_days))


def _freshness_score(value: Any) -> float:
    return _recency_weight(value, half_life_days=3.0)


def _hashtags(value: Any) -> list[str]:
    if not value:
        return []
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            raw = [part for part in value.replace(",", " ").split() if part]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        tag = str(item or "").strip().lstrip("#").casefold()
        if tag and len(tag) <= 80 and tag not in out:
            out.append(tag)
        if len(out) >= 12:
            break
    return out


def _add_score(target: dict[str, float], key: Any, score: float) -> None:
    clean = str(key or "").strip()
    if not clean:
        return
    target[clean] = target.get(clean, 0.0) + float(score)


def _trim_score_map(target: dict[str, float], limit: int) -> None:
    items = sorted(target.items(), key=lambda item: abs(item[1]), reverse=True)[: max(0, limit)]
    target.clear()
    target.update(items)


def _normalized_feature(scores: dict[str, float], key: str) -> float:
    if not key or key not in scores:
        return 0.0
    scale = max([abs(value) for value in scores.values()] or [1.0])
    return max(-1.0, min(1.0, scores[key] / max(1.0, scale)))


def _sorted_positive(scores: dict[str, float], limit: int) -> list[tuple[str, float]]:
    return sorted(
        ((key, value) for key, value in scores.items() if value > 0),
        key=lambda item: item[1],
        reverse=True,
    )[:limit]


def _float_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in list(value.items())[:200]:
        try:
            result[str(key)] = float(item)
        except Exception:
            continue
    return result


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        clean = str(item or "").strip()
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _newer(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return right if right > left else left


def _timestamp(value: datetime | None) -> float:
    try:
        return value.timestamp() if value else 0.0
    except Exception:
        return 0.0
