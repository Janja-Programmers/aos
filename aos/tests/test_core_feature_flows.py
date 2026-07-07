from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.ads.create import create_ad_impl
from aos.api.auth.register import register_impl
from aos.api.auth.session import me_impl
from aos.api.chat.message import send_message_impl
from aos.api.live.live import join_live_impl, start_live_impl
from aos.api.media.upload import init_upload_impl
from aos.api.notifications.token import deactivate_push_token_impl, register_push_token_impl
from aos.api.reports.report_user import report_user_impl
from aos.api.reviews.create import create_review_impl
from aos.api.shorts.comments import add_comment_impl
from aos.api.shorts.engagement import toggle_like_impl
from aos.api.social.block import block_user_impl

from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestCoreFeatureFlows(AOSFeatureTestMixin, FrappeTestCase):
    """Feature-level API flow tests for core production behavior."""

    def setUp(self):
        self.prefix = self.make_prefix("feature")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_auth_register_creates_profile_preference_and_blocks_duplicate(self):
        email = f"{self.prefix}-signup@example.com"
        country, language, currency = self.preference_defaults()

        with (
            patch("aos.api.auth.register.rate_limit", return_value=None),
            patch("aos.api.auth.register.generate_otp", return_value="123456"),
            patch("aos.api.auth.register.send_otp_email") as send_email,
        ):
            first = register_impl(
                email=email,
                full_name="Feature Signup",
                password="StrongPass123!",
                country=country,
                language=language,
                currency=currency,
            )
            second = register_impl(
                email=email,
                full_name="Feature Signup",
                password="StrongPass123!",
                country=country,
                language=language,
                currency=currency,
            )

        self.created_users.append(email)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(second.get("ok"), second)
        self.assertEqual(second.get("code"), "ALREADY_EXISTS")
        self.assertTrue(frappe.db.exists("User", email))
        self.assertTrue(frappe.db.exists("AOS Profile", email))
        self.assertTrue(frappe.db.exists("AOS User Preference", {"user": email}))
        self.assertTrue(frappe.db.exists("AOS Email Verification", {"user": email, "purpose": "email_verification"}))
        send_email.assert_called_once()

    def test_auth_me_returns_current_enabled_user_payload(self):
        user = self.make_user("me")
        frappe.set_user(user)

        response = me_impl()

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("user", {}).get("email"), user)

    def test_media_init_upload_uses_authenticated_user_and_returns_upload_payload(self):
        user = self.make_user("media", with_preference=False)
        fake_doc = self.fake_media_doc(name=f"{self.prefix}-MEDIA", purpose="profile_image")
        service = Mock()
        service.init_upload.return_value = (
            fake_doc,
            "https://upload.example.test/presigned",
            {"Content-Type": "image/jpeg"},
            600,
        )
        frappe.set_user(user)

        with (
            patch("aos.api.media.upload.rate_limit", return_value=None),
            patch("aos.api.media.upload.MediaService", return_value=service),
        ):
            response = init_upload_impl(
                purpose="profile_image",
                filename="avatar.jpg",
                content_type="image/jpeg",
                size_bytes=1024,
            )

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("media_id"), fake_doc.name)
        service.init_upload.assert_called_once_with(
            user=user,
            purpose="profile_image",
            filename="avatar.jpg",
            content_type="image/jpeg",
            size_bytes=1024,
        )

    def test_ads_create_with_uploaded_image_creates_reviewing_ad_and_seller(self):
        user = self.make_user("ad-seller")
        country, _language, currency = self.preference_defaults()
        category = self.make_category()
        location = self.make_location(country=country)
        image_media = self.make_media(owner=user, purpose="ad_image")
        frappe.set_user(user)

        with (
            patch("aos.api.ads.create.rate_limit", return_value=None),
            patch("aos.api.ads.create.enqueue_ad_moderation", return_value=SimpleNamespace(name="MOD-TEST", status="Queued")),
            patch("aos.api.ads.create.record_ad_posted_activity"),
        ):
            response = create_ad_impl(
                title=f"{self.prefix} Test Ad",
                location=location,
                category=category,
                description="A proper feature-level ad creation test.",
                price_type="Fixed",
                price=123,
                images=[{"media_id": image_media.name, "is_primary": 1, "sort_order": 0}],
            )

        self.assertTrue(response.get("ok"), response)
        ad_id = response.get("data", {}).get("id")
        self.assertTrue(ad_id)
        self.assertEqual(frappe.db.get_value("AOS Ad", ad_id, "status"), "Reviewing")
        self.assertEqual(frappe.db.get_value("AOS Ad", ad_id, "currency"), currency)
        self.assertTrue(frappe.db.exists("AOS Seller", user))
        self.assertEqual(frappe.db.count("AOS Ad Image", {"parent": ad_id}), 1)

    def test_shorts_like_and_comment_flow_updates_viewer_state_and_records_comment(self):
        owner = self.make_user("short-owner")
        viewer = self.make_user("short-viewer")
        short = self.make_short(owner=owner)
        frappe.set_user(viewer)

        with (
            patch("aos.api.shorts.engagement.rate_limit", return_value=None),
            patch("aos.api.shorts.engagement.frappe.enqueue"),
            patch("aos.api.shorts.engagement.NotificationService.notify_short_like"),
            patch("aos.api.shorts.engagement.record_short_like_activity"),
            patch("aos.api.shorts.engagement.hide_short_like_activity"),
        ):
            liked = toggle_like_impl(short_id=short.name)
            unliked = toggle_like_impl(short_id=short.name)

        self.assertTrue(liked.get("ok"), liked)
        self.assertTrue(liked.get("data", {}).get("viewer_state", {}).get("is_liked"))
        self.assertTrue(unliked.get("ok"), unliked)
        self.assertFalse(unliked.get("data", {}).get("viewer_state", {}).get("is_liked"))
        self.assertEqual(frappe.db.count("AOS Short Like", {"short": short.name, "user": viewer}), 0)

        with (
            patch("aos.api.shorts.comments.rate_limit", return_value=None),
            patch("aos.api.shorts.comments.frappe.enqueue"),
            patch("aos.api.shorts.comments.NotificationService.notify_short_comment"),
            patch("aos.api.shorts.comments.record_short_comment_activity"),
            patch("aos.api.shorts.comments.sync_comment_mentions"),
        ):
            comment_response = add_comment_impl(short_id=short.name, comment="Great product short")

        self.assertTrue(comment_response.get("ok"), comment_response)
        comment_id = comment_response.get("data", {}).get("comment_id")
        self.assertTrue(frappe.db.exists("AOS Short Comment", comment_id))

    def test_report_user_with_block_option_creates_report_and_active_block(self):
        reporter = self.make_user("reporter")
        target = self.make_user("reported")
        reason = self.make_report_reason()
        frappe.set_user(reporter)

        with (
            patch("aos.api.reports.report_user.rate_limit", return_value=None),
            patch("aos.api.social.block.rate_limit", return_value=None),
            patch("aos.api.reports.report_user.record_report_user_activity"),
            patch("aos.api.social.block.record_block_user_activity"),
        ):
            response = report_user_impl(
                target_user=target,
                reason=reason,
                details="Feature report test",
                block_user=1,
            )

        self.assertTrue(response.get("ok"), response)
        self.assertTrue(response.get("data", {}).get("block_applied"), response)
        self.assertEqual(frappe.db.count("AOS User Report", {"reported_user": target, "reported_by": reporter}), 1)
        self.assertEqual(
            frappe.db.count(
                "AOS User Block",
                {"blocker_user": reporter, "blocked_user": target, "status": "Active"},
            ),
            1,
        )

    def test_review_create_requires_prior_chat_and_creates_pending_review(self):
        seller_user = self.make_user("review-seller")
        reviewer = self.make_user("reviewer")
        ad = self.make_ad(seller_user=seller_user, status="Active")
        self.make_conversation(reviewer, seller_user, with_message=True)
        frappe.set_user(reviewer)

        with (
            patch("aos.api.reviews.create.rate_limit", return_value=None),
            patch("aos.api.reviews.create.enqueue_review_moderation", return_value=SimpleNamespace(name="REV-MOD", status="Queued")),
        ):
            response = create_review_impl(
                ad=ad.name,
                rating=5,
                title="Excellent seller",
                comment="Good communication and fast response.",
            )

        self.assertTrue(response.get("ok"), response)
        review_id = response.get("data", {}).get("id")
        self.assertTrue(review_id)
        self.assertEqual(frappe.db.get_value("AOS Review", review_id, "reviewer"), reviewer)
        self.assertEqual(frappe.db.get_value("AOS Review", review_id, "status"), "Pending")

    def test_chat_send_message_is_blocked_when_receiver_blocked_sender(self):
        sender = self.make_user("chat-sender")
        receiver = self.make_user("chat-receiver")
        conv = self.make_conversation(sender, receiver)
        frappe.set_user(receiver)

        with (
            patch("aos.api.social.block.rate_limit", return_value=None),
            patch("aos.api.social.block.record_block_user_activity"),
        ):
            block_response = block_user_impl(target_user=sender, reason="No messages")

        self.assertTrue(block_response.get("ok"), block_response)
        frappe.set_user(sender)

        with patch("aos.api.chat.message.rate_limit", return_value=None):
            response = send_message_impl(conversation_id=conv.name, content="Hello")

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("code"), "USER_BLOCKED")
        self.assertEqual(frappe.db.count("AOS Message", {"conversation": conv.name, "sender": sender}), 0)

    def test_live_start_and_join_return_livekit_session_payloads(self):
        host = self.make_user("live-host")
        viewer = self.make_user("live-viewer")
        frappe.set_user(host)

        with (
            patch("aos.api.live.live.rate_limit", return_value=None),
            patch("aos.api.live.live.LiveKitService.generate_live_token", return_value="live-token"),
            patch("aos.api.live.live.LiveKitService.get_ws_url", return_value="wss://live.example.test"),
            patch("aos.api.live.live.publish_live_started"),
            patch("aos.api.live.live.publish_live_message_to_user"),
            patch("aos.api.live.messages.publish_live_message"),
            patch("aos.api.live.live.NotificationService.notify_live_started"),
            patch("aos.api.live.live.record_live_host_activity"),
        ):
            start_response = start_live_impl(title=f"{self.prefix} Live Title")

        self.assertTrue(start_response.get("ok"), start_response)
        live_id = start_response.get("data", {}).get("live", {}).get("id")
        self.assertTrue(live_id)
        self.assertEqual(start_response.get("data", {}).get("session", {}).get("token"), "live-token")

        frappe.set_user(viewer)
        with (
            patch("aos.api.live.live.rate_limit", return_value=None),
            patch("aos.api.live.live.LiveKitService.generate_live_token", return_value="viewer-token"),
            patch("aos.api.live.live.LiveKitService.get_ws_url", return_value="wss://live.example.test"),
        ):
            join_response = join_live_impl(live_id=live_id, session_id=f"{self.prefix}-viewer-session")

        self.assertTrue(join_response.get("ok"), join_response)
        self.assertEqual(join_response.get("data", {}).get("session", {}).get("role"), "viewer")
        self.assertEqual(join_response.get("data", {}).get("session", {}).get("token"), "viewer-token")

    def test_notifications_register_and_deactivate_push_token_flow(self):
        user = self.make_user("push-flow")
        token = f"{self.prefix}-fcm-token"
        device_id = f"{self.prefix}-device"
        frappe.set_user(user)

        with patch("aos.api.notifications.token.rate_limit", return_value=None):
            registered = register_push_token_impl(
                token=token,
                device_type="android",
                device_id=device_id,
            )
            deactivated = deactivate_push_token_impl(token=token)

        self.assertTrue(registered.get("ok"), registered)
        token_id = registered.get("data", {}).get("id")
        self.assertTrue(token_id)
        self.assertTrue(deactivated.get("ok"), deactivated)
        self.assertEqual(int(frappe.db.get_value("AOS Push Token", token_id, "is_active") or 0), 0)
        self.assertFalse(frappe.db.get_value("AOS Push Token", token_id, "active_device_key"))
