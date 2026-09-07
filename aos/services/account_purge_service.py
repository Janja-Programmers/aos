"""Bounded permanent cleanup for expired recoverable account deletions.

Account deletion is reversible for 30 days. During that window no durable
relationship/content rows are destroyed. After the deadline, this service
removes private/personalization state in bounded background batches and finally
anonymizes profile PII. Shared marketplace/history records remain linked to the
stable internal/public account identity and are rendered as a deleted account.

No function in this module commits. Scheduler/Frappe job transaction ownership
remains authoritative.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.accounts.constants import (
    ACCOUNT_PURGE_SOCIAL_EDGE_BATCH_SIZE,
    ACCOUNT_STATUS_DELETED,
    PROFILE_DOCTYPE,
    PROFILE_IMAGE_FIELD,
    PURGE_STATUS_COMPLETED,
    PURGE_STATUS_PENDING,
    PURGE_STATUS_PURGING,
)
from aos.services.account_deletion_service import (
    _cleanup_verification_documents,
    _doctype_exists,
    _revoke_verification_requests,
)
from aos.services.accounts.observability import account_log
from aos.services.media.media_service import MediaError, MediaService
from aos.services.social.repository import SocialRepository


class AccountPurgeError(RuntimeError):
    pass


def _count(doctype: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not _doctype_exists(doctype):
        return 0
    rows = frappe.db.sql(
        f"SELECT COUNT(*) AS count FROM `tab{doctype}` WHERE {where_sql}",
        params,
        as_dict=True,
    )
    return int((rows[0] or {}).get("count") or 0) if rows else 0


def _delete_name_batch(
    doctype: str,
    *,
    where_sql: str,
    params: tuple[Any, ...],
    limit: int,
) -> int:
    if not _doctype_exists(doctype):
        return 0
    size = max(1, min(int(limit or 1), 10_000))
    rows = frappe.db.sql(
        f"SELECT name FROM `tab{doctype}` WHERE {where_sql} ORDER BY name ASC LIMIT %s FOR UPDATE",
        (*params, size),
        pluck=True,
    )
    names = tuple(str(name) for name in rows if name)
    if not names:
        return 0
    frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name IN %s", (names,))
    return len(names)


def _purge_social_batch(*, user: str, limit: int) -> dict[str, int]:
    """Delete at most ``limit`` follow edges and block rows for one account."""
    size = max(1, min(int(limit or ACCOUNT_PURGE_SOCIAL_EDGE_BATCH_SIZE), 10_000))
    repository = SocialRepository()
    removed = 0
    affected: set[str] = set()

    if _doctype_exists("AOS Follow"):
        rows = frappe.db.sql(
            """
            SELECT name, follower_user, following_user
            FROM `tabAOS Follow`
            WHERE follower_user = %s OR following_user = %s
            ORDER BY name ASC
            LIMIT %s FOR UPDATE
            """,
            (user, user, size),
            as_dict=True,
        )
        names = tuple(str(row.name) for row in rows if row.name)
        for row in rows:
            for candidate in (row.follower_user, row.following_user):
                if candidate:
                    affected.add(str(candidate))
        if names:
            frappe.db.sql("DELETE FROM `tabAOS Follow` WHERE name IN %s", (names,))
            removed = len(names)
            repository.sync_counters(sorted(affected))

    blocks_closed = 0
    remaining_budget = max(0, size - removed)
    if remaining_budget and _doctype_exists("AOS User Block"):
        now = now_datetime()
        names = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS User Block`
            WHERE status = 'Active' AND (blocker_user = %s OR blocked_user = %s)
            ORDER BY name ASC
            LIMIT %s FOR UPDATE
            """,
            (user, user, remaining_budget),
            pluck=True,
        )
        names = tuple(str(name) for name in names if name)
        if names:
            frappe.db.sql(
                """
                UPDATE `tabAOS User Block`
                SET status = 'Unblocked', unblocked_at = COALESCE(unblocked_at, %(now)s),
                    active_pair_key = NULL, modified = %(now)s
                WHERE name IN %(names)s
                """,
                {"now": now, "names": names},
            )
            blocks_closed = len(names)

    return {
        "follow_rows_removed": removed,
        "active_social_blocks_closed": blocks_closed,
    }


def _purge_wishlist_batch(*, user: str, limit: int) -> int:
    if not _doctype_exists("AOS Wishlist"):
        return 0
    size = max(1, min(int(limit or 1), 10_000))
    rows = frappe.db.sql(
        """
        SELECT name, ad
        FROM `tabAOS Wishlist`
        WHERE user = %s
        ORDER BY name ASC
        LIMIT %s FOR UPDATE
        """,
        (user, size),
        as_dict=True,
    )
    names = tuple(str(row.name) for row in rows if row.name)
    ads = sorted({str(row.ad) for row in rows if row.ad})
    if not names:
        return 0
    frappe.db.sql("DELETE FROM `tabAOS Wishlist` WHERE name IN %s", (names,))
    if ads:
        from aos.services.wishlist.counters import recompute_wishlist_counts

        recompute_wishlist_counts(ads, source="account_permanent_purge")
    return len(names)


def _purge_notifications_batch(*, user: str, limit: int) -> int:
    return _delete_name_batch(
        "AOS Notification", where_sql="user = %s", params=(user,), limit=limit
    )


def _purge_saved_search_batch(*, user: str, limit: int) -> int:
    return _delete_name_batch(
        "AOS Saved Search", where_sql="user = %s", params=(user,), limit=limit
    )


def _purge_auth_identity_rows(*, user: str, limit: int) -> int:
    total = 0
    total += _delete_name_batch(
        "AOS Auth Identity", where_sql="user = %s", params=(user,), limit=limit
    )
    total += _delete_name_batch(
        "AOS Auth Challenge", where_sql="user = %s", params=(user,), limit=limit
    )
    return total


def _purge_localization_preference(*, user: str, limit: int) -> int:
    return _delete_name_batch(
        "AOS User Preference", where_sql="user = %s", params=(user,), limit=limit
    )



def _purge_chat_private_batch(*, user: str, limit: int) -> dict[str, int]:
    """Remove one bounded batch of account-private Chat state."""
    removed_stars = _delete_name_batch(
        "AOS Message Star", where_sql="user = %s", params=(user,), limit=limit
    )
    removed_reactions = _delete_name_batch(
        "AOS Message Reaction", where_sql="user = %s", params=(user,), limit=limit
    )
    removed_translations = _delete_name_batch(
        "AOS Message Translation", where_sql="translated_by = %s", params=(user,), limit=limit
    )
    deactivated = 0
    if _doctype_exists("AOS Conversation"):
        size = max(1, min(int(limit or 1), 10_000))
        rows = frappe.db.sql(
            """
            SELECT name, participant_1, participant_2
            FROM `tabAOS Conversation`
            WHERE (participant_1 = %(user)s AND IFNULL(is_active_1, 1) = 1)
               OR (participant_2 = %(user)s AND IFNULL(is_active_2, 1) = 1)
            ORDER BY name ASC
            LIMIT %(limit)s FOR UPDATE
            """,
            {"user": user, "limit": size},
            as_dict=True,
        )
        p1 = tuple(str(row.name) for row in rows if row.participant_1 == user and row.name)
        p2 = tuple(str(row.name) for row in rows if row.participant_2 == user and row.name)
        if p1:
            frappe.db.sql(
                "UPDATE `tabAOS Conversation` SET is_active_1 = 0, unread_count_1 = 0 WHERE name IN %(names)s",
                {"names": p1},
            )
        if p2:
            frappe.db.sql(
                "UPDATE `tabAOS Conversation` SET is_active_2 = 0, unread_count_2 = 0 WHERE name IN %(names)s",
                {"names": p2},
            )
        deactivated = len(rows)
    return {
        "chat_stars_removed": removed_stars,
        "chat_reactions_removed": removed_reactions,
        "chat_translation_cache_removed": removed_translations,
        "chat_conversations_deactivated": deactivated,
    }


def _purge_reports_private_batch(*, user: str, limit: int) -> dict[str, int]:
    """Remove one bounded batch of reporter-private moderation rows."""
    user_reports = _delete_name_batch(
        "AOS User Report", where_sql="reported_by = %s", params=(user,), limit=limit
    )
    short_reports = _delete_name_batch(
        "AOS Short Report", where_sql="reported_by = %s", params=(user,), limit=limit
    )
    ad_reports = 0
    affected_ads: set[str] = set()
    if _doctype_exists("AOS Ad Report"):
        size = max(1, min(int(limit or 1), 10_000))
        rows = frappe.db.sql(
            """
            SELECT name, ad
            FROM `tabAOS Ad Report`
            WHERE reported_by = %s
            ORDER BY name ASC
            LIMIT %s FOR UPDATE
            """,
            (user, size),
            as_dict=True,
        )
        names = tuple(str(row.name) for row in rows if row.name)
        affected_ads = {str(row.ad) for row in rows if row.ad}
        if names:
            frappe.db.sql("DELETE FROM `tabAOS Ad Report` WHERE name IN %s", (names,))
            ad_reports = len(names)
    for ad_id in sorted(affected_ads):
        if frappe.db.exists("AOS Ad", ad_id):
            total = frappe.db.count("AOS Ad Report", {"ad": ad_id, "status": ["!=", "Rejected"]})
            frappe.db.set_value("AOS Ad", ad_id, "total_reports", int(total or 0), update_modified=False)
    return {
        "user_reports_removed": user_reports,
        "short_reports_removed": short_reports,
        "ad_reports_removed": ad_reports,
        "ad_report_totals_recalculated": len(affected_ads),
    }


def _purge_reviews_private_batch(*, user: str, limit: int) -> dict[str, int]:
    """Remove one bounded batch of reviewer-private reaction/report state."""
    reactions_removed = 0
    affected_reviews: set[str] = set()
    if _doctype_exists("AOS Review Reaction"):
        size = max(1, min(int(limit or 1), 10_000))
        rows = frappe.db.sql(
            """
            SELECT name, review
            FROM `tabAOS Review Reaction`
            WHERE user = %s
            ORDER BY name ASC
            LIMIT %s FOR UPDATE
            """,
            (user, size),
            as_dict=True,
        )
        names = tuple(str(row.name) for row in rows if row.name)
        affected_reviews = {str(row.review) for row in rows if row.review}
        if names:
            frappe.db.sql("DELETE FROM `tabAOS Review Reaction` WHERE name IN %s", (names,))
            reactions_removed = len(names)
    reports_removed = _delete_name_batch(
        "AOS Review Report", where_sql="reported_by = %s", params=(user,), limit=limit
    )
    counters_recalculated = 0
    if affected_reviews:
        from aos.services.reviews.aggregates import recompute_review_reaction_counts

        for review_id in sorted(affected_reviews):
            if frappe.db.exists("AOS Review", review_id):
                recompute_review_reaction_counts(review_id=review_id, lock_review=True)
                counters_recalculated += 1
    authored_reviews = _count("AOS Review", "reviewer = %s", (user,))
    return {
        "reviews_retained_anonymized": authored_reviews,
        "review_reactions_removed": reactions_removed,
        "review_reports_removed": reports_removed,
        "review_reaction_totals_recalculated": counters_recalculated,
    }


def _activity_snapshot_conditions(user: str) -> tuple[str, tuple[Any, ...]]:
    """Build parameterized target conditions for retained Activity snapshots."""
    clauses = ["(target_doctype = 'User' AND target_name = %s)"]
    params: list[Any] = [user]
    try:
        from aos.services.accounts.identity import public_account_id_for_user

        public_id = str(public_account_id_for_user(user) or "").strip()
    except Exception:
        public_id = ""
    if public_id:
        clauses.append("(route_type = 'profile' AND route_id = %s)")
        params.append(public_id)

    sellers: tuple[str, ...] = ()
    if _doctype_exists("AOS Seller"):
        sellers = tuple(
            str(name)
            for name in frappe.db.sql(
                "SELECT name FROM `tabAOS Seller` WHERE user = %s ORDER BY name ASC",
                (user,),
                pluck=True,
            )
            if name
        )
    if sellers and _doctype_exists("AOS Ad"):
        placeholders = ",".join(["%s"] * len(sellers))
        clauses.append(
            "(route_type = 'ad' AND route_id IN "
            f"(SELECT name FROM `tabAOS Ad` WHERE seller IN ({placeholders})))"
        )
        params.extend(sellers)
    if _doctype_exists("AOS Short"):
        if sellers:
            placeholders = ",".join(["%s"] * len(sellers))
            clauses.append(
                "(route_type = 'short' AND route_id IN "
                f"(SELECT name FROM `tabAOS Short` WHERE owner = %s OR seller IN ({placeholders})))"
            )
            params.append(user)
            params.extend(sellers)
        else:
            clauses.append(
                "(route_type = 'short' AND route_id IN "
                "(SELECT name FROM `tabAOS Short` WHERE owner = %s))"
            )
            params.append(user)
    if _doctype_exists("AOS Live Stream"):
        clauses.append(
            "(route_type = 'live' AND route_id IN "
            "(SELECT name FROM `tabAOS Live Stream` WHERE host_user = %s))"
        )
        params.append(user)
    return " OR ".join(clauses), tuple(params)


def _purge_activity_private_batch(*, user: str, limit: int) -> dict[str, int]:
    """Bound private Activity deletion/redaction for permanent deletion."""
    if not _doctype_exists("AOS User Activity"):
        return {
            "activity_rows_removed": 0,
            "activity_snapshots_redacted": 0,
        }
    own_rows = _delete_name_batch(
        "AOS User Activity", where_sql="user = %s", params=(user,), limit=limit
    )
    conditions, params = _activity_snapshot_conditions(user)
    size = max(1, min(int(limit or 1), 10_000))
    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tabAOS User Activity`
        WHERE user != %s AND status != 'Hidden' AND ({conditions})
        ORDER BY name ASC
        LIMIT %s FOR UPDATE
        """,
        (user, *params, size),
        pluck=True,
    )
    names = tuple(str(name) for name in rows if name)
    if names:
        frappe.db.sql(
            """
            UPDATE `tabAOS User Activity`
            SET status = 'Hidden', active_key = NULL,
                target_title = 'Unavailable content', target_subtitle = 'Removed',
                target_image = '', metadata_json = '{}'
            WHERE name IN %(names)s
            """,
            {"names": names},
        )
    return {
        "activity_rows_removed": own_rows,
        "activity_snapshots_redacted": len(names),
    }


def _count_activity_remaining(user: str) -> int:
    if not _doctype_exists("AOS User Activity"):
        return 0
    own = _count("AOS User Activity", "user = %s", (user,))
    conditions, params = _activity_snapshot_conditions(user)
    rows = frappe.db.sql(
        f"SELECT COUNT(*) AS count FROM `tabAOS User Activity` WHERE user != %s AND status != 'Hidden' AND ({conditions})",
        (user, *params),
        as_dict=True,
    )
    retained = int((rows[0] or {}).get("count") or 0) if rows else 0
    return own + retained

def _remaining_bounded_private_rows(user: str) -> int:
    checks = (
        ("AOS Follow", "follower_user = %s OR following_user = %s", (user, user)),
        ("AOS User Block", "status = 'Active' AND (blocker_user = %s OR blocked_user = %s)", (user, user)),
        ("AOS Wishlist", "user = %s", (user,)),
        ("AOS Notification", "user = %s", (user,)),
        ("AOS Saved Search", "user = %s", (user,)),
        ("AOS Auth Identity", "user = %s", (user,)),
        ("AOS Auth Challenge", "user = %s", (user,)),
        ("AOS User Preference", "user = %s", (user,)),
        ("AOS Message Star", "user = %s", (user,)),
        ("AOS Message Reaction", "user = %s", (user,)),
        ("AOS Message Translation", "translated_by = %s", (user,)),
        ("AOS Conversation", "(participant_1 = %s AND IFNULL(is_active_1, 1) = 1) OR (participant_2 = %s AND IFNULL(is_active_2, 1) = 1)", (user, user)),
        ("AOS User Report", "reported_by = %s", (user,)),
        ("AOS Short Report", "reported_by = %s", (user,)),
        ("AOS Ad Report", "reported_by = %s", (user,)),
        ("AOS Review Reaction", "user = %s", (user,)),
        ("AOS Review Report", "reported_by = %s", (user,)),
    )
    total = sum(_count(doctype, where, params) for doctype, where, params in checks)
    total += _count_activity_remaining(user)
    if _doctype_exists("AOS Verification Document") and _doctype_exists("AOS Verification Request"):
        rows = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Verification Document` d
            INNER JOIN `tabAOS Verification Request` r ON r.name = d.parent
            WHERE d.parenttype = 'AOS Verification Request' AND r.user = %s
            """,
            (user,),
            as_dict=True,
        )
        total += int((rows[0] or {}).get("count") or 0) if rows else 0
    return total



def _anonymize_verification_requests(*, user: str) -> int:
    if not _doctype_exists("AOS Verification Request"):
        return 0
    fields = (
        "legal_name", "phone_number", "business_name", "business_phone_number",
        "business_email", "business_website", "business_address",
        "submission_idempotency_hash",
    )
    available = [field for field in fields if frappe.get_meta("AOS Verification Request").has_field(field)]
    if not available:
        return 0
    count = _count("AOS Verification Request", "user = %s", (user,))
    if not count:
        return 0
    assignments = ", ".join(f"`{field}` = ''" for field in available)
    frappe.db.sql(
        f"UPDATE `tabAOS Verification Request` SET {assignments} WHERE user = %s",
        (user,),
    )
    return count

def _release_profile_media(*, profile, user: str) -> int:
    media_id = str(getattr(profile, PROFILE_IMAGE_FIELD, "") or "").strip()
    if not media_id:
        return 0
    try:
        MediaService().release_media(
            media_id=media_id,
            user=user,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=profile.name,
            system=True,
        )
    except MediaError as exc:
        raise AccountPurgeError("Profile media could not be released") from exc
    profile.set(PROFILE_IMAGE_FIELD, "")
    frappe.db.set_value("User", user, "user_image", "", update_modified=False)
    return 1


def _anonymize_profile(*, profile, user: str) -> dict[str, int]:
    media_released = _release_profile_media(profile=profile, user=user)
    profile.display_name = "Deleted User"
    profile.legal_name = ""
    profile.bio = ""
    profile.phone = ""
    profile.date_of_birth = None
    profile.gender = ""
    profile.delete_reason = ""
    profile.is_verified = 0
    if hasattr(profile, "lifecycle_reason"):
        profile.lifecycle_reason = "Permanent deletion completed"
    # Keep User.name/email as the stable internal Link key. Public serializers
    # never expose it; clearing display fields prevents retained presentation PII.
    frappe.db.set_value(
        "User",
        user,
        {"first_name": "Deleted", "last_name": "User", "full_name": "Deleted User", "user_image": ""},
        update_modified=False,
    )
    return {"profile_media_released": media_released, "profile_pii_anonymized": 1}


def purge_expired_deleted_account(
    user: str,
    *,
    batch_size: int = ACCOUNT_PURGE_SOCIAL_EDGE_BATCH_SIZE,
) -> dict[str, Any]:
    """Advance one expired account's permanent cleanup by a bounded batch."""
    user = str(user or "").strip()
    if not user:
        return {"completed": False, "reason": "missing_user"}

    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Profile`
        WHERE user = %s
        LIMIT 1 FOR UPDATE
        """,
        (user,),
        as_dict=True,
    )
    if not rows:
        return {"completed": False, "reason": "profile_missing"}
    profile = frappe.get_doc("AOS Profile", rows[0].name)
    if str(profile.account_status or "") != ACCOUNT_STATUS_DELETED:
        return {"completed": False, "reason": "not_deleted"}
    if not profile.restore_deadline or now_datetime() <= profile.restore_deadline:
        return {"completed": False, "reason": "restore_window_open"}
    if str(getattr(profile, "purge_status", "") or "") == PURGE_STATUS_COMPLETED:
        return {"completed": True, "idempotent": True}

    now = now_datetime()
    if str(getattr(profile, "purge_status", "") or PURGE_STATUS_PENDING) != PURGE_STATUS_PURGING:
        profile.purge_status = PURGE_STATUS_PURGING
        profile.purge_started_at = profile.purge_started_at or now
        profile.save(ignore_permissions=True)

    size = max(1, min(int(batch_size or ACCOUNT_PURGE_SOCIAL_EDGE_BATCH_SIZE), 10_000))
    summary: dict[str, Any] = {}
    summary.update(_purge_social_batch(user=user, limit=size))
    summary["wishlist_rows_removed"] = _purge_wishlist_batch(user=user, limit=size)
    summary["notification_rows_removed"] = _purge_notifications_batch(user=user, limit=size)
    summary["saved_search_rows_removed"] = _purge_saved_search_batch(user=user, limit=size)
    summary["auth_identity_rows_removed"] = _purge_auth_identity_rows(user=user, limit=size)
    summary["localization_preferences_removed"] = _purge_localization_preference(user=user, limit=size)

    # High-cardinality private domains are also bounded. Shared history (messages,
    # reviews, calls, marketplace/moderation records) is retained and rendered
    # through the stable permanently-deleted identity.
    summary.update(_purge_chat_private_batch(user=user, limit=size))
    summary.update(_purge_reports_private_batch(user=user, limit=size))
    summary.update(_purge_reviews_private_batch(user=user, limit=size))
    summary.update(_purge_activity_private_batch(user=user, limit=size))

    # Verification evidence is expected to be small (strict submission limits)
    # and already uses <=500-row internal pages with idempotent media release.
    summary.update(_cleanup_verification_documents(user=user, batch_size=min(size, 500)))
    summary["verification_requests_revoked"] = _revoke_verification_requests(user=user, now=now)
    summary["verification_requests_anonymized"] = _anonymize_verification_requests(user=user)

    remaining = _remaining_bounded_private_rows(user)
    summary["remaining_bounded_private_rows"] = remaining
    if remaining:
        account_log("account.permanent_deletion.progress", user=user)
        return {"completed": False, "idempotent": False, "summary": summary}

    summary.update(_anonymize_profile(profile=profile, user=user))
    profile.purge_status = PURGE_STATUS_COMPLETED
    profile.purge_completed_at = now_datetime()
    profile.save(ignore_permissions=True)
    account_log("account.permanent_deletion.completed", user=user)
    return {"completed": True, "idempotent": False, "summary": summary}
