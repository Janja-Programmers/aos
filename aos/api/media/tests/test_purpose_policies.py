from __future__ import annotations

from unittest import TestCase

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


class TestMediaPurposePolicies(TestCase):
	def test_all_current_media_purposes_are_centralized(self):
		self.assertEqual(set(MEDIA_PURPOSES), EXPECTED_PURPOSES)
		self.assertEqual(get_media_purpose("Short-Video Raw").key, "short_video_raw")
		self.assertIsNone(get_media_purpose("not-real"))

	def test_private_purposes_never_use_public_visibility(self):
		private = {
			"chat_attachment",
			"verification_document",
			"background_removal_source",
			"short_video_raw",
		}
		actual_private = {
			key for key, policy in MEDIA_PURPOSES.items() if policy.is_private
		}
		self.assertEqual(actual_private, private)
		self.assertTrue(
			all(MEDIA_PURPOSES[key].bucket_type == "private" for key in private)
		)

	def test_internal_derived_thumbnail_is_not_client_selectable(self):
		self.assertFalse(MEDIA_PURPOSES["short_thumbnail"].client_upload_allowed)
		self.assertNotIn(
			"short_thumbnail",
			list_media_purposes(client_upload_only=True),
		)

	def test_category_icon_uses_role_permission_manager_capability(self):
		policy = MEDIA_PURPOSES["category_icon"]
		self.assertEqual(policy.required_permission_doctype, "AOS Category")
		self.assertEqual(policy.required_permission_type, "write")
		self.assertEqual(
			policy.allowed_attachment_doctypes,
			frozenset({"AOS Category"}),
		)

	def test_policies_define_bounded_sizes_and_resource_counts(self):
		for purpose, policy in MEDIA_PURPOSES.items():
			with self.subTest(purpose=purpose):
				self.assertGreater(policy.max_size_bytes, 0)
				self.assertLessEqual(policy.max_size_bytes, 300 * 1024 * 1024)
				self.assertGreater(policy.max_items_per_resource, 0)
				self.assertLessEqual(policy.max_items_per_resource, 10)
				self.assertIn(policy.visibility, {"Public", "Private"})
				self.assertTrue(policy.allowed_content_types)
				self.assertTrue(policy.allowed_extensions)
				self.assertTrue(policy.prefix)
				self.assertFalse(policy.prefix.startswith("/"))

	def test_ad_video_upload_limit_matches_product_policy(self):
		policy = MEDIA_PURPOSES["ad_video"]
		self.assertEqual(policy.max_size_bytes, 200 * 1024 * 1024)
		self.assertEqual(policy.max_duration_seconds, 300)

	def test_sensitive_and_immutable_purposes_have_stricter_lifecycle_rules(self):
		self.assertEqual(
			MEDIA_PURPOSES["verification_document"].orphan_retention_days,
			1,
		)
		self.assertFalse(MEDIA_PURPOSES["chat_attachment"].replacement_permitted)
		self.assertTrue(MEDIA_PURPOSES["short_video_raw"].processing_required)
		self.assertFalse(MEDIA_PURPOSES["short_video_raw"].replacement_permitted)
		self.assertFalse(MEDIA_PURPOSES["sound_upload"].replacement_permitted)
