from __future__ import annotations

from aos.services.media.media_purposes import (
    MEDIA_PURPOSES,
    get_media_purpose,
    list_media_purposes,
)

EXPECTED_PURPOSES = {
    "ad_image",
    "ad_video",
    "review_image",
    "seller_banner",
    "live_cover",
    "profile_image",
    "category_icon",
    "chat_attachment",
    "verification_document",
    "background_removal_source",
    "short_video_raw",
    "short_thumbnail",
    "sound_upload",
}


def test_all_current_media_purposes_are_centralized():
    assert set(MEDIA_PURPOSES) == EXPECTED_PURPOSES
    assert get_media_purpose("Short-Video Raw").key == "short_video_raw"
    assert get_media_purpose("not-real") is None


def test_private_purposes_never_use_public_visibility():
    private = {
        "chat_attachment",
        "verification_document",
        "background_removal_source",
        "short_video_raw",
    }
    assert {key for key, policy in MEDIA_PURPOSES.items() if policy.is_private} == private
    assert all(MEDIA_PURPOSES[key].bucket_type == "private" for key in private)


def test_internal_derived_thumbnail_is_not_client_selectable():
    assert MEDIA_PURPOSES["short_thumbnail"].client_upload_allowed is False
    assert "short_thumbnail" not in list_media_purposes(client_upload_only=True)


def test_category_icon_requires_an_administrative_role():
    policy = MEDIA_PURPOSES["category_icon"]
    assert policy.allowed_roles == frozenset({"System Manager"})
    assert policy.allowed_attachment_doctypes == frozenset({"AOS Category"})


def test_policies_define_bounded_sizes_and_resource_counts():
    for policy in MEDIA_PURPOSES.values():
        assert 0 < policy.max_size_bytes <= 300 * 1024 * 1024
        assert 0 < policy.max_items_per_resource <= 10
        assert policy.visibility in {"Public", "Private"}
        assert policy.allowed_content_types
        assert policy.allowed_extensions
        assert policy.prefix and not policy.prefix.startswith("/")


def test_sensitive_and_immutable_purposes_have_stricter_lifecycle_rules():
    assert MEDIA_PURPOSES["verification_document"].orphan_retention_days == 1
    assert MEDIA_PURPOSES["chat_attachment"].replacement_permitted is False
    assert MEDIA_PURPOSES["short_video_raw"].processing_required is True
    assert MEDIA_PURPOSES["short_video_raw"].replacement_permitted is False
    assert MEDIA_PURPOSES["sound_upload"].replacement_permitted is False
