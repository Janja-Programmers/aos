from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestMarketplaceDiscoveryArchitectureContracts(unittest.TestCase):
    def test_public_v1_uses_only_shared_transport(self):
        for path in ('aos/api/v1/ads/__init__.py','aos/api/v1/saved_search/__init__.py','aos/api/v1/search_ranking/__init__.py'):
            source=text(path)
            assert 'execute_endpoint' in source
            assert 'transport.execute' not in source

    def test_no_unversioned_marketplace_whitelists(self):
        for folder in ('aos/api/ads','aos/api/saved_search','aos/api/search_ranking'):
            for path in (ROOT/folder).glob('*.py'):
                assert '@frappe.whitelist' not in path.read_text(encoding='utf-8'), path

    def test_ad_schema_has_public_ids_and_no_raw_media_fields(self):
        ad=json.loads(text('aos/aos/doctype/aos_ad/aos_ad.json'))
        fields={row['fieldname']:row for row in ad['fields'] if row.get('fieldname')}
        assert fields['public_id']['unique']==1
        assert fields['submission_key_hash']['unique']==1
        assert 'video' not in fields
        image=json.loads(text('aos/aos/doctype/aos_ad_image/aos_ad_image.json'))
        assert 'image' not in {row.get('fieldname') for row in image['fields']}

    def test_manual_review_does_not_fabricate_human_reviewer(self):
        source=text('aos/aos/doctype/aos_ad/aos_ad.py')
        assert 'self.review_source = "Automatic"' in source
        assert 'self.reviewed_by = None' in source
        assert 'self.review_source = "Manual"' in source
        assert 'self.reviewed_by = reviewer' in source
        review=text('aos/services/ads/review.py')
        assert 'has_doctype_permission' in review and 'assert_version' in review and 'lock_ad' in review

    def test_geography_is_final_reusable_policy(self):
        geo=text('aos/services/marketplace_discovery/geography.py')
        assert 'stable_geographic_rerank' in geo
        assert 'return 0' in geo and 'return 1' in geo and 'return 2' in geo
        listing=text('aos/api/ads/list_ads.py')
        assert 'geo_bucket' in listing and 'order_by = f"{geo_bucket} ASC, {primary}"' in listing
        projection=text('aos/services/marketplace_discovery/projection.py')
        assert 'stable_geographic_rerank(' in projection

    def test_related_and_image_search_recheck_canonical_projection(self):
        related=text('aos/api/search_ranking/recommendations.py')
        image=text('aos/api/ads/image_search.py')
        assert 'related_ad_candidates' in related and 'load_public_ad_items' in related
        assert 'required_category=source.category' in related
        projection=text('aos/services/marketplace_discovery/projection.py')
        assert 'a.category = %(required_category)s' in projection
        ranking=text('infra/search-ranking/app/store.py')
        assert 'doc.get("category")) != _clean(source.get("category"))' in ranking
        assert 'load_public_ad_items' in image
        assert 'matched_media_id' not in image
        assert 'score' not in image.split('return ok',1)[-1]

    def test_qdrant_is_versioned_private_and_authenticated(self):
        compose=text('docker-compose.yml')
        assert 'QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:?QDRANT_API_KEY is required}' in compose
        assert '${QDRANT_BIND_ADDRESS:-127.0.0.1}' in compose
        assert 'aos_ad_images_v2' in compose
        store=text('infra/image-search/app/qdrant_store.py')
        for field in ('ad_id','media_id','generation','embedding_version','schema_version'):
            assert field in store
        assert 'max(existing) > generation' in store
        assert 'must_not=[FieldCondition(key="generation"' in store

    def test_fx_uses_provider_timestamp_and_coherent_snapshot(self):
        refresh=text('aos/tasks/fx.py')
        reads=text('aos/services/fx_service.py')+text('aos/services/currency_conversion.py')
        assert 'provider_timestamp' in refresh and 'fx_max_stale_hours' in refresh
        assert 'provider_timestamp' in reads and 'rate_version' in reads
        assert 'requests.' not in text('aos/services/currency_conversion.py')

    def test_saved_search_is_canonical_owner_cursor_contract(self):
        service=text('aos/api/saved_search/service.py')
        assert 'persisted_search_intent' in service
        assert '_lock_user(user)' in service
        assert 'MAX_SAVED_SEARCHES_PER_USER' in service
        assert 'ORDER BY modified DESC, public_id DESC' in service
        assert 'OFFSET' not in service
        assert '{"public_id":public_id,"user":user,"is_active":1}' in service

    def test_marketplace_schema_installer_is_registered(self):
        patches=text('aos/patches.txt')
        assert 'install_marketplace_discovery_indexes' in patches
        migrate=text('aos/migrate.py')
        assert 'install_marketplace_discovery_indexes.execute' in migrate

    def test_canonical_docs_and_api_owners_are_current(self):
        for path in ('ads/README.md', 'search-ranking/README.md', 'saved-search/README.md'):
            source = text('docs/features/' + path)
            assert 'BEGIN CODE-DERIVED ENDPOINTS' in source
        for path in (
            'docs/features/ads/api.md',
            'docs/features/search-ranking/api.md',
            'docs/features/saved-search/api.md',
        ):
            assert not (ROOT / path).exists()


    def test_ads_media_input_is_canonical_media_id_only(self):
        validation=text('aos/services/ads/validation.py')
        media=text('aos/services/ads/media.py')
        assert '_IMAGE_KEYS = frozenset({"media_id", "is_primary", "sort_order"})' in validation
        assert 'raw.get("media_id")' in validation
        assert 'raw.get("image")' not in validation and 'raw.get("url")' not in validation
        assert 'public_media_url' not in media
        assert '"image":' not in media


    def test_ad_location_validation_uses_localization_boundary(self):
        controller = text('aos/aos/doctype/aos_ad/aos_ad.py')
        shared = text('aos/api/shared/validators.py')
        localization = text('aos/services/localization/validators.py')
        assert 'validate_location(self.location, country=self.country, required=True)' in controller
        assert 'from aos.services.localization import validate_location' in shared
        assert 'location_by_name(location)' in localization


    def test_changed_doctype_field_orders_cover_marketplace_fields(self):
        cases={
            'aos/aos/doctype/aos_ad/aos_ad.json': {'public_id','submission_key_hash','review_source','review_result'},
            'aos/aos/doctype/aos_ad_draft/aos_ad_draft.json': {'public_id'},
            'aos/aos/doctype/aos_exchange_rate/aos_exchange_rate.json': {'base_currency','rate_version','provider_timestamp'},
            'aos/aos/doctype/aos_saved_search/aos_saved_search.json': {'public_id','params_json','fingerprint','is_active'},
            'aos/aos/doctype/aos_settings/aos_settings.json': {'fx_max_stale_hours'},
        }
        for path,required in cases.items():
            doc=json.loads(text(path))
            order=set(doc.get('field_order') or [])
            assert required <= order, (path, required-order)


    def test_ads_notifications_expose_public_ad_ids(self):
        review=text('aos/services/ads/review.py')
        moderation=text('aos/services/moderation_service.py')
        expiry=text('aos/tasks/ads.py')
        assert 'ad_id=doc.public_id' in review and 'ad_id=doc.name' not in review
        assert moderation.count('ad_id=ad.public_id') >= 2
        assert 'ad_id=ad.name' not in moderation
        assert 'ad_id=ad.public_id' in expiry and 'ad_id=ad.name' not in expiry


    def test_dependency_signal_changes_reproject_ads_in_bounded_batches(self):
        hooks=text('aos/hooks.py')
        signals=text('aos/services/marketplace_discovery/signals.py')
        assert 'marketplace_discovery.signals.seller_signal_changed' in hooks
        assert 'marketplace_discovery.signals.profile_signal_changed' in hooks
        assert 'marketplace_discovery.signals.user_signal_changed' in hooks
        assert 'limit=size + 1' in signals and 'order_by="name asc"' in signals
        assert 'enqueue_discovery_refresh' in signals


    def test_missing_ad_index_delete_requires_captured_public_id(self):
        source=text('aos/services/search_ranking_service.py')
        assert 'Missing Ads require enqueue_ad_search_delete with a captured public ID' in source
        assert 'def enqueue_ad_search_delete' in source


    def test_ad_search_index_uses_canonical_account_display_name(self):
        source=text('aos/services/search_ranking_service.py')
        signals=text('aos/services/marketplace_discovery/signals.py')
        seller_schema=json.loads(text('aos/aos/doctype/aos_seller/aos_seller.json'))
        profile_schema=json.loads(text('aos/aos/doctype/aos_profile/aos_profile.json'))
        seller_fields={field.get('fieldname') for field in seller_schema.get('fields') or []}
        profile_fields={field.get('fieldname') for field in profile_schema.get('fields') or []}
        assert 'shop_name' not in seller_fields
        assert 'display_name' in profile_fields
        assert 's.shop_name' not in source
        assert 'p.display_name AS seller_name' in source
        assert '("status", "shop_name", "user")' not in signals
        assert '("status", "user")' in signals
        assert '("account_status", "is_verified", "display_name")' in signals


    def test_ad_search_index_uses_only_authoritative_ad_metrics(self):
        source=text('aos/services/search_ranking_service.py')
        store=text('infra/search-ranking/app/store.py')
        ad_schema=json.loads(text('aos/aos/doctype/aos_ad/aos_ad.json'))
        fields={field.get('fieldname') for field in ad_schema.get('fields') or []}
        assert 'view_count' not in fields
        assert 'a.view_count' not in source
        assert '"view_count": int(ad.view_count' not in source
        assert 'doc.get("view_count")' not in store
        assert 'wishlist_count' in source
        assert 'doc.get("wishlist_count")' in store


    def test_manual_review_can_override_automatic_nonterminal_decisions(self):
        lifecycle=text('aos/services/ads/lifecycle.py')
        assert 'frozenset({STATUS_REVIEWING, STATUS_DECLINED}), STATUS_ACTIVE' in lifecycle
        assert 'frozenset({STATUS_REVIEWING, STATUS_ACTIVE}), STATUS_DECLINED' in lifecycle


    def test_saved_search_reuses_live_search_query_minimum(self):
        validation=text('aos/services/ads/validation.py')
        search_query=text('aos/services/marketplace_discovery/search_query.py')
        assert 'Search query must contain at least two characters.' in validation
        assert 'normalize_public_list_filters(dict(payload))' in search_query


    def test_ads_caught_failures_rollback_only_to_local_savepoint(self):
        api=text('aos/services/ads/api.py')
        assert 'frappe.db.savepoint(savepoint)' in api
        assert 'frappe.db.rollback(save_point=savepoint)' in api
        for path in ('aos/api/ads/create.py','aos/api/ads/update.py','aos/api/ads/status.py','aos/api/ads/review.py','aos/api/ads/drafts.py'):
            assert 'frappe.db.rollback()' not in text(path)

        for path in ('aos/api/ads/create.py','aos/api/ads/update.py','aos/api/ads/status.py','aos/api/ads/review.py'):
            assert 'transactional=True' in text(path), path
        drafts = text('aos/api/ads/drafts.py')
        for operation in ('_save', '_abandon', '_submit'):
            assert f'run_ads_api({operation}' in drafts
        for path in ('aos/api/ads/list_ads.py','aos/api/ads/get_ad.py','aos/api/ads/get_my_ad.py','aos/api/ads/list_my_ads.py','aos/api/wishlist/list.py','aos/api/search_ranking/recommendations.py'):
            assert 'transactional=True' not in text(path), path


    def test_saved_search_caught_mutations_use_local_savepoints(self):
        api=text('aos/api/saved_search/api.py')
        assert 'frappe.db.savepoint(savepoint)' in api
        assert 'frappe.db.rollback(save_point=savepoint)' in api
        for path in ('aos/api/saved_search/create.py','aos/api/saved_search/update.py','aos/api/saved_search/delete.py'):
            assert 'run_saved_search_mutation' in text(path)


    def test_image_search_client_has_bounded_transient_retries(self):
        source = text("aos/integrations/ai/image_search_client.py")
        assert "IMAGE_SEARCH_CLIENT_MAX_RETRIES" in source
        assert "IMAGE_SEARCH_CLIENT_RETRY_BACKOFF_MS" in source
        assert "RETRYABLE_STATUS_CODES" in source
        assert "_sleep_before_retry" in source
        assert "stream.seek(0)" in source
    def test_ad_image_consumers_use_canonical_media_projection(self):
        activity=text('aos/api/ads/activity.py')
        wishlist=text('aos/api/wishlist/list.py')
        projection=text('aos/services/marketplace_discovery/projection.py')
        chat=text('aos/services/chat/shared_objects.py')
        shorts_feed=text('aos/api/shorts/feed.py')
        shorts_management=text('aos/api/shorts/management.py')
        media=text('aos/api/ads/media.py')

        for source in (activity, wishlist, chat, shorts_feed, shorts_management):
            assert 'SELECT adi.image' not in source
            assert '"media", "image"' not in source
        assert 'project_ad_image_urls(rows)' in activity
        assert 'load_public_ad_items' in wishlist
        assert 'get_public_attachment_url_map' in projection
        assert 'get_public_attachment_url_map' in chat
        assert 'project_ad_thumbnail_urls(visible_rows)' in shorts_feed
        assert 'project_ad_thumbnail_urls(rows)' in shorts_management
        assert 'def project_ad_thumbnail_urls(' in media

    def test_ad_activity_is_best_effort_for_primary_mutations(self):
        activity=text('aos/api/ads/activity.py')
        assert activity.count('_safe_record("load_ad_target", _load_ad_target, ad_id)') >= 4

