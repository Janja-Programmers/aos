from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.accounts.identity import ensure_public_account_id
from aos.api.ads.create import create_ad_impl
from aos.api.auth.register import register_impl
from aos.api.auth.session import me_impl
from aos.api.chat.message import send_message_impl
from aos.api.live.live import join_live_impl, start_live_impl
from aos.api.live.token import get_live_token_impl
from aos.services.livekit.admin import RoomAdminResult
from aos.tasks.live import activate_live_room
from aos.api.media.upload import init_upload_impl
from aos.api.notifications.token import deactivate_push_token_impl, register_push_token_impl
from aos.api.reports.report_user import report_user_impl
from aos.api.reviews.create import create_review_impl
from aos.api.social.block import block_user_impl

from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestCoreFeatureFlows(AOSFeatureTestMixin, FrappeTestCase):
    """Feature-level API flow tests for core production behavior."""

    def setUp(self):
        self.prefix = self.make_prefix("feature")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_auth_register_creates_profile_preference_and_accepts_duplicate_retry(self):
        email = f"{self.prefix}-signup@example.com"
        country, language, currency = self.preference_defaults()

        # Registration is a guest flow. Running it as Administrator in CI makes
        # market resolution use Administrator's missing AOS preference instead
        # of the explicit signup country/currency.
        frappe.set_user("Guest")

        with (
            patch("aos.api.auth.register.auth_rate_limit", return_value=None),
            patch("aos.api.auth.register.auth_ip_limit", return_value=None),
            patch("aos.api.auth.otp_service.generate_otp", return_value="123456"),
            patch("aos.api.auth.otp_service.queue_otp_email") as send_email,
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
        self.assertTrue(second.get("ok"), second)
        self.assertNotIn("error", second)
        self.assertTrue(frappe.db.exists("User", email))
        self.assertTrue(frappe.db.exists("AOS Profile", {"user": email}))
        self.assertTrue(frappe.db.exists("AOS User Preference", {"user": email}))
        self.assertTrue(frappe.db.exists("AOS Auth Challenge", {"user": email, "purpose": "email_verification"}))
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
            duration_seconds=None,
            checksum_sha256=None,
            idempotency_key=None,
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
            patch(
                "aos.api.ads.create.prepare_image_rows",
                return_value=[{"media": image_media.name, "is_primary": 1, "sort_order": 0}],
            ),
            patch("aos.api.ads.create.prepare_video", return_value=None),
            patch("aos.api.ads.create.attach_all"),
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
                idempotency_key=f"{self.prefix}-create-ad",
            )

        self.assertTrue(response.get("ok"), response)
        public_ad_id = response.get("data", {}).get("id")
        self.assertTrue(public_ad_id)
        ad_name = frappe.db.get_value("AOS Ad", {"public_id": public_ad_id}, "name")
        self.assertTrue(ad_name)
        self.assertEqual(frappe.db.get_value("AOS Ad", ad_name, "status"), "Reviewing")
        self.assertEqual(frappe.db.get_value("AOS Ad", ad_name, "currency"), currency)
        self.assertTrue(frappe.db.exists("AOS Seller", {"user": user}))
        self.assertEqual(frappe.db.count("AOS Ad Image", {"parent": ad_name}), 1)


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
                target_user=ensure_public_account_id(target),
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
                ad_id=ad.public_id,
                rating=5,
                title="Excellent seller",
                comment="Good communication and fast response.",
                media=[],
            )

        self.assertTrue(response.get("ok"), response)
        review_id = response.get("data", {}).get("review", {}).get("id")
        self.assertTrue(review_id)
        review_name = frappe.db.get_value("AOS Review", {"public_id": review_id}, "name")
        self.assertEqual(frappe.db.get_value("AOS Review", review_name, "reviewer"), reviewer)
        self.assertEqual(frappe.db.get_value("AOS Review", review_name, "status"), "Pending")

    def test_chat_send_message_is_blocked_when_receiver_blocked_sender(self):
        sender = self.make_user("chat-sender")
        receiver = self.make_user("chat-receiver")
        conv = self.make_conversation(sender, receiver)
        frappe.set_user(receiver)

        with (
            patch("aos.api.social.block.rate_limit", return_value=None),
            patch("aos.api.social.block.record_block_user_activity"),
        ):
            block_response = block_user_impl(account_id=ensure_public_account_id(sender), reason="No messages")

        self.assertTrue(block_response.get("ok"), block_response)
        frappe.set_user(sender)

        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.MediaService", return_value=Mock()),
        ):
            response = send_message_impl(conversation_id=conv.name, content="Hello")

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "USER_BLOCKED")
        self.assertEqual(frappe.db.count("AOS Message", {"conversation": conv.name, "sender": sender}), 0)

    def test_live_start_activation_and_join_return_livekit_session_payloads(self):
        host = self.make_user("live-host")
        viewer = self.make_user("live-viewer")
        frappe.set_user(host)

        with (
            patch("aos.api.live.live.rate_limit", return_value=None),
            patch("aos.api.live.live._enqueue_room_job"),
        ):
            start_response = start_live_impl(title=f"{self.prefix} Live Title")

        self.assertTrue(start_response.get("ok"), start_response)
        live_id = start_response.get("data", {}).get("live", {}).get("id")
        self.assertTrue(live_id)
        self.assertEqual(start_response.get("data", {}).get("live", {}).get("status"), "starting")
        self.assertIsNone(start_response.get("data", {}).get("session"))

        with (
            patch("aos.tasks.live.ensure_room", return_value=RoomAdminResult(True, "created")),
            patch("aos.api.live.activity.record_live_host_activity"),
            patch("aos.api.live.live._create_startup_messages"),
            patch("aos.api.live.realtime.publish_live_started"),
            patch("aos.tasks.live.enqueue_live_started_fanout"),
        ):
            activation = activate_live_room(live_id=live_id)

        self.assertTrue(activation.get("ok"), activation)
        self.assertEqual(frappe.db.get_value("AOS Live Stream", live_id, "status"), "live")
        self.assertEqual(int(frappe.db.get_value("AOS Live Stream", live_id, "is_active") or 0), 1)

        with (
            patch("aos.api.live.token.rate_limit", return_value=None),
            patch("aos.api.live.token.LiveKitService.generate_live_token", return_value="host-token"),
            patch("aos.api.live.token.LiveKitService.get_ws_url", return_value="wss://live.example.test"),
        ):
            host_token = get_live_token_impl(live_id=live_id)

        self.assertTrue(host_token.get("ok"), host_token)
        self.assertEqual(host_token.get("data", {}).get("session", {}).get("token"), "host-token")

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

    def test_live_room_failure_never_becomes_joinable_or_issues_token(self):
        host = self.make_user("live-room-failure-host")
        frappe.set_user(host)

        with (
            patch("aos.api.live.live.rate_limit", return_value=None),
            patch("aos.api.live.live._enqueue_room_job"),
        ):
            start_response = start_live_impl(title=f"{self.prefix} Failed Live")

        self.assertTrue(start_response.get("ok"), start_response)
        live_id = start_response["data"]["live"]["id"]
        self.assertIsNone(start_response["data"]["session"])

        with (
            patch("aos.tasks.live.ensure_room", return_value=RoomAdminResult(False, "unavailable")),
            patch("aos.tasks.live._enqueue_live_room_cleanup"),
        ):
            activation = activate_live_room(live_id=live_id)

        self.assertFalse(activation.get("ok"), activation)
        row = frappe.db.get_value(
            "AOS Live Stream",
            live_id,
            ["status", "is_active", "active_host_key"],
            as_dict=True,
        )
        self.assertEqual(row.status, "failed")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertFalse(row.active_host_key)

        with (
            patch("aos.api.live.token.rate_limit", return_value=None),
            patch("aos.api.live.token.LiveKitService.generate_live_token") as generate_token,
        ):
            token_response = get_live_token_impl(live_id=live_id)

        self.assertFalse(token_response.get("ok"), token_response)
        generate_token.assert_not_called()

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
                registration_kind="token",
            )
            deactivated = deactivate_push_token_impl(token=token, registration_kind="token")

        self.assertTrue(registered.get("ok"), registered)
        token_id = registered.get("data", {}).get("id")
        self.assertTrue(token_id)
        self.assertTrue(deactivated.get("ok"), deactivated)
        self.assertEqual(int(frappe.db.get_value("AOS Push Token", token_id, "is_active") or 0), 0)
        self.assertFalse(frappe.db.get_value("AOS Push Token", token_id, "active_device_key"))
