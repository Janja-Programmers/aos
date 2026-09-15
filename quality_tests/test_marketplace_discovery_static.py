from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def text(path:str)->str: return (ROOT/path).read_text(encoding='utf-8')

def test_public_v1_uses_only_shared_transport():
    for path in ('aos/api/v1/ads/__init__.py','aos/api/v1/saved_search/__init__.py','aos/api/v1/search_ranking/__init__.py'):
        source=text(path)
        assert 'execute_endpoint' in source
        assert 'transport.execute' not in source

def test_no_unversioned_marketplace_whitelists():
    for folder in ('aos/api/ads','aos/api/saved_search','aos/api/search_ranking'):
        for path in (ROOT/folder).glob('*.py'):
            assert '@frappe.whitelist' not in path.read_text(encoding='utf-8'), path

def test_ad_schema_has_public_ids_and_no_raw_media_fields():
    ad=json.loads(text('aos/aos/doctype/aos_ad/aos_ad.json'))
    fields={row['fieldname']:row for row in ad['fields'] if row.get('fieldname')}
    assert fields['public_id']['unique']==1
    assert fields['submission_key_hash']['unique']==1
    assert 'video' not in fields
    image=json.loads(text('aos/aos/doctype/aos_ad_image/aos_ad_image.json'))
    assert 'image' not in {row.get('fieldname') for row in image['fields']}

def test_manual_review_does_not_fabricate_human_reviewer():
    source=text('aos/aos/doctype/aos_ad/aos_ad.py')
    assert 'self.review_source = "Automatic"' in source
    assert 'self.reviewed_by = None' in source
    assert 'self.review_source = "Manual"' in source
    assert 'self.reviewed_by = reviewer' in source
    review=text('aos/services/ads/review.py')
    assert 'has_doctype_permission' in review and 'assert_version' in review and 'lock_ad' in review

def test_geography_is_final_reusable_policy():
    geo=text('aos/services/marketplace_discovery/geography.py')
    assert 'stable_geographic_rerank' in geo
    assert 'return 0' in geo and 'return 1' in geo and 'return 2' in geo
    listing=text('aos/api/ads/list_ads.py')
    assert 'geo_bucket' in listing and 'order_by = f"{geo_bucket} ASC, {primary}"' in listing
    projection=text('aos/services/marketplace_discovery/projection.py')
    assert 'stable_geographic_rerank(' in projection

def test_related_and_image_search_recheck_canonical_projection():
    related=text('aos/api/search_ranking/recommendations.py')
    image=text('aos/api/ads/image_search.py')
    assert 'related_ad_candidates' in related and 'load_public_ad_items' in related
    assert 'load_public_ad_items' in image
    assert 'matched_media_id' not in image
    assert 'score' not in image.split('return ok',1)[-1]

def test_qdrant_is_versioned_private_and_authenticated():
    compose=text('docker-compose.yml')
    assert 'QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:?QDRANT_API_KEY is required}' in compose
    assert '${QDRANT_BIND_ADDRESS:-127.0.0.1}' in compose
    assert 'aos_ad_images_v2' in compose
    store=text('infra/image-search/app/qdrant_store.py')
    for field in ('ad_id','media_id','generation','embedding_version','schema_version'):
        assert field in store
    assert 'max(existing) > generation' in store
    assert 'must_not=[FieldCondition(key="generation"' in store

def test_fx_uses_provider_timestamp_and_coherent_snapshot():
    refresh=text('aos/tasks/fx.py')
    reads=text('aos/services/fx_service.py')+text('aos/services/currency_conversion.py')
    assert 'provider_timestamp' in refresh and 'fx_max_stale_hours' in refresh
    assert 'provider_timestamp' in reads and 'rate_version' in reads
    assert 'requests.' not in text('aos/services/currency_conversion.py')

def test_saved_search_is_canonical_owner_cursor_contract():
    service=text('aos/api/saved_search/service.py')
    assert 'persisted_search_intent' in service
    assert '_lock_user(user)' in service
    assert 'MAX_SAVED_SEARCHES_PER_USER' in service
    assert 'ORDER BY modified DESC, public_id DESC' in service
    assert 'OFFSET' not in service
    assert '{"public_id":public_id,"user":user,"is_active":1}' in service

def test_schema_installer_replaces_migration_compatibility_patches():
    patches=text('aos/patches.txt')
    assert 'install_marketplace_discovery_indexes' in patches
    assert 'harden_ads_subsystem' not in patches
    assert 'normalize_ads_offer_fields' not in patches
    assert not (ROOT/'aos/patches/v1_0/harden_ads_subsystem.py').exists()
    assert not (ROOT/'aos/patches/v1_0/normalize_ads_offer_fields.py').exists()
    migrate=text('aos/migrate.py')
    assert 'install_marketplace_discovery_indexes.execute' in migrate

def test_canonical_docs_and_api_owners_are_current():
    assert (ROOT/'docs/features/marketplace-discovery/README.md').exists()
    for path in (
        'docs/features/ads/api.md',
        'docs/features/search-ranking/api.md',
        'docs/features/saved-search/api.md',
    ):
        source = text(path)
        assert 'Marketplace Discovery' in source
        assert 'BEGIN CODE-DERIVED ENDPOINTS' in source
    assert not (ROOT/'docs/features/ads/README.md').exists()
    assert not (ROOT/'docs/features/search-ranking/README.md').exists()
    assert not (ROOT/'docs/features/saved-search/README.md').exists()


def test_ads_media_input_is_canonical_media_id_only():
    validation=text('aos/services/ads/validation.py')
    media=text('aos/services/ads/media.py')
    assert '_IMAGE_KEYS = frozenset({"media_id", "is_primary", "sort_order"})' in validation
    assert 'raw.get("media_id")' in validation
    assert 'raw.get("image")' not in validation and 'raw.get("url")' not in validation
    assert 'public_media_url' not in media
    assert '"image":' not in media


def test_ad_location_validation_uses_canonical_location_lookup():
    source=text('aos/aos/doctype/aos_ad/aos_ad.py')
    expected='frappe.db.get_value(\n            "AOS Location",\n            self.location,\n            ["country", "is_active"],\n            as_dict=True,\n        )'
    assert expected in source


def test_changed_doctype_field_orders_cover_marketplace_fields():
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


def test_ads_notifications_expose_public_ad_ids():
    review=text('aos/services/ads/review.py')
    moderation=text('aos/services/moderation_service.py')
    expiry=text('aos/tasks/ads.py')
    assert 'ad_id=doc.public_id' in review and 'ad_id=doc.name' not in review
    assert moderation.count('ad_id=ad.public_id') >= 2
    assert 'ad_id=ad.name' not in moderation
    assert 'ad_id=ad.public_id' in expiry and 'ad_id=ad.name' not in expiry


def test_dependency_signal_changes_reproject_ads_in_bounded_batches():
    hooks=text('aos/hooks.py')
    signals=text('aos/services/marketplace_discovery/signals.py')
    assert 'marketplace_discovery.signals.seller_signal_changed' in hooks
    assert 'marketplace_discovery.signals.profile_signal_changed' in hooks
    assert 'marketplace_discovery.signals.user_signal_changed' in hooks
    assert 'limit=size + 1' in signals and 'order_by="name asc"' in signals
    assert 'enqueue_discovery_refresh' in signals


def test_missing_ad_index_delete_requires_captured_public_id():
    source=text('aos/services/search_ranking_service.py')
    assert 'Missing Ads require enqueue_ad_search_delete with a captured public ID' in source
    assert 'def enqueue_ad_search_delete' in source


def test_manual_review_can_override_automatic_nonterminal_decisions():
    lifecycle=text('aos/services/ads/lifecycle.py')
    assert 'frozenset({STATUS_REVIEWING, STATUS_DECLINED}), STATUS_ACTIVE' in lifecycle
    assert 'frozenset({STATUS_REVIEWING, STATUS_ACTIVE}), STATUS_DECLINED' in lifecycle


def test_saved_search_reuses_live_search_query_minimum():
    validation=text('aos/services/ads/validation.py')
    search_query=text('aos/services/marketplace_discovery/search_query.py')
    assert 'Search query must contain at least two characters.' in validation
    assert 'normalize_public_list_filters(dict(payload))' in search_query


def test_ads_caught_failures_rollback_only_to_local_savepoint():
    api=text('aos/services/ads/api.py')
    assert 'frappe.db.savepoint(savepoint)' in api
    assert 'frappe.db.rollback(save_point=savepoint)' in api
    for path in ('aos/api/ads/create.py','aos/api/ads/update.py','aos/api/ads/status.py','aos/api/ads/review.py','aos/api/ads/drafts.py'):
        assert 'frappe.db.rollback()' not in text(path)


def test_saved_search_caught_mutations_use_local_savepoints():
    api=text('aos/api/saved_search/api.py')
    assert 'frappe.db.savepoint(savepoint)' in api
    assert 'frappe.db.rollback(save_point=savepoint)' in api
    for path in ('aos/api/saved_search/create.py','aos/api/saved_search/update.py','aos/api/saved_search/delete.py'):
        assert 'run_saved_search_mutation' in text(path)


def test_image_search_client_has_bounded_transient_retries():
    source = text("aos/integrations/ai/image_search_client.py")
    assert "IMAGE_SEARCH_CLIENT_MAX_RETRIES" in source
    assert "IMAGE_SEARCH_CLIENT_RETRY_BACKOFF_MS" in source
    assert "RETRYABLE_STATUS_CODES" in source
    assert "_sleep_before_retry" in source
    assert "stream.seek(0)" in source
