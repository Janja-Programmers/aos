"""Post-migration schema invariants for AOS.

Frappe executes one-time application patches and DocType model synchronization
before the ``after_migrate`` hook. Composite/manual indexes that are not
expressible in DocType JSON therefore need an idempotent final reassertion so a
later DocType reload cannot leave a migrated site without production-critical
indexes.
"""
from __future__ import annotations

from collections.abc import Callable

from aos.patches.v1_0 import (
    install_media_indexes,
    install_accounts_indexes,
    install_activity_indexes,
    install_call_indexes,
    install_catalog_indexes,
    install_chat_indexes,
    install_live_indexes,
    install_localization_schema,
    install_marketplace_discovery_indexes,
    install_notification_indexes,
    install_review_indexes,
    install_verification_indexes,
    install_wishlist_indexes,
    install_report_indexes,
    install_social_indexes,
    install_shorts_indexes,
    install_shorts_recommendation_indexes,
)
from aos.services.sellers import schema as seller_schema


# Keep this list limited to schema-only, idempotent installers. Data
# reconciliation remains in one-time patches and must not run on every migrate.
_SCHEMA_INVARIANT_INSTALLERS: tuple[Callable[[], None], ...] = (
    install_localization_schema.execute,
    install_accounts_indexes.execute,
    install_media_indexes.execute,
    install_verification_indexes.execute,
    seller_schema.execute,
    install_shorts_indexes.execute,
    install_shorts_recommendation_indexes.execute,
    install_live_indexes.execute,
    install_chat_indexes.execute,
    install_call_indexes.execute,
    install_catalog_indexes.execute,
    install_marketplace_discovery_indexes.execute,
    install_wishlist_indexes.execute,
    install_review_indexes.execute,
    install_social_indexes.execute,
    install_report_indexes.execute,
    install_activity_indexes.execute,
    install_notification_indexes.execute,
)


def after_migrate() -> None:
    """Reassert current manual schema indexes after every DocType sync."""
    for install in _SCHEMA_INVARIANT_INSTALLERS:
        install()
