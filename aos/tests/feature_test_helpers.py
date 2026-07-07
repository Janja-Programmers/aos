from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import frappe
from frappe.utils import now_datetime


class AOSFeatureTestMixin:
    """Small DB fixture helpers for feature-level API tests.

    These helpers intentionally create only the minimum rows needed by the
    public API implementations under test. They avoid external services and keep
    cleanup scoped by a per-test prefix.
    """

    prefix: str
    created_users: list[str]

    def make_prefix(self, namespace: str = "feature") -> str:
        return f"{namespace}-{uuid.uuid4().hex[:10]}"

    def make_user(self, label: str, *, enabled: int = 1, with_preference: bool = True) -> str:
        email = f"{self.prefix}-{label}@example.com"
        if not frappe.db.exists("User", email):
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": "Feature",
                    "last_name": label.title(),
                    "enabled": enabled,
                    "user_type": "Website User",
                    "send_welcome_email": 0,
                }
            )
            user.insert(ignore_permissions=True)

        if not frappe.db.exists("AOS Profile", email):
            frappe.get_doc(
                {
                    "doctype": "AOS Profile",
                    "user": email,
                    "account_status": "Active",
                    "is_deleted": 0,
                }
            ).insert(ignore_permissions=True)

        if with_preference:
            self.ensure_user_preference(email)

        if email not in self.created_users:
            self.created_users.append(email)

        frappe.db.commit()
        return email

    def ensure_user_preference(self, user: str):
        if frappe.db.exists("AOS User Preference", {"user": user}):
            return

        country, language, currency = self.preference_defaults()
        frappe.get_doc(
            {
                "doctype": "AOS User Preference",
                "user": user,
                "country": country,
                "language": language,
                "currency": currency,
            }
        ).insert(ignore_permissions=True)

    def preference_defaults(self) -> tuple[str, str, str]:
        country = (
            frappe.db.get_single_value("AOS Settings", "default_country")
            or self.first_existing_value("Country", ["Kenya", "United States"])
            or frappe.db.get_value("Country", {}, "name")
        )
        language = (
            frappe.db.get_single_value("AOS Settings", "default_language")
            or self.first_existing_value("Language", ["en", "English"])
            or frappe.db.get_value("Language", {}, "name")
        )
        currency = (
            frappe.db.get_single_value("AOS Settings", "default_currency")
            or self.first_existing_value("Currency", ["KES", "USD"])
            or frappe.db.get_value("Currency", {}, "name")
        )

        missing = [
            label
            for label, value in (
                ("country", country),
                ("language", language),
                ("currency", currency),
            )
            if not value
        ]
        if missing:
            self.fail(f"Missing preference fixture values: {', '.join(missing)}")

        return str(country), str(language), str(currency)

    @staticmethod
    def first_existing_value(doctype: str, names: list[str]) -> str | None:
        for name in names:
            if frappe.db.exists(doctype, name):
                return name
        return None

    def make_category(self) -> str:
        category = f"{self.prefix} Category"
        if not frappe.db.exists("AOS Category", category):
            frappe.get_doc(
                {
                    "doctype": "AOS Category",
                    "category_name": category,
                    "is_active": 1,
                }
            ).insert(ignore_permissions=True)
        frappe.db.commit()
        return category

    def make_location(self, *, country: str | None = None) -> str:
        country = country or self.preference_defaults()[0]
        location = f"{self.prefix} Location"
        if not frappe.db.exists("AOS Location", location):
            frappe.get_doc(
                {
                    "doctype": "AOS Location",
                    "location": location,
                    "country": country,
                    "is_active": 1,
                }
            ).insert(ignore_permissions=True)
        frappe.db.commit()
        return location

    def make_media(
        self,
        *,
        owner: str,
        purpose: str,
        content_type: str = "image/jpeg",
        filename: str = "image.jpg",
        visibility: str = "Public",
        status: str = "Uploaded",
    ):
        media = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": owner,
                "bucket": "aos-test",
                "object_key": f"tests/{self.prefix}/{uuid.uuid4().hex}/{filename}",
                "original_filename": filename,
                "content_type": content_type,
                "size_bytes": 1024,
                "visibility": visibility,
                "purpose": purpose,
                "status": status,
                "public_url": f"https://cdn.example.test/{self.prefix}/{filename}",
            }
        )
        media.insert(ignore_permissions=True)
        frappe.db.commit()
        return media

    def make_seller(self, user: str):
        if frappe.db.exists("AOS Seller", user):
            return frappe.get_doc("AOS Seller", user)

        seller = frappe.get_doc(
            {
                "doctype": "AOS Seller",
                "user": user,
                "status": "Active",
                "seller_type": "Individual",
            }
        )
        seller.insert(ignore_permissions=True)
        frappe.db.commit()
        return seller

    def make_ad(self, *, seller_user: str, status: str = "Active"):
        country = self.preference_defaults()[0]
        category = self.make_category()
        location = self.make_location(country=country)
        seller = self.make_seller(seller_user)
        image_media = self.make_media(owner=seller_user, purpose="ad_image")

        ad = frappe.get_doc(
            {
                "doctype": "AOS Ad",
                "title": f"{self.prefix} Test Ad",
                "category": category,
                "description": "Feature test ad description",
                "price_type": "Fixed",
                "price": 99,
                "currency": self.preference_defaults()[2],
                "country": country,
                "location": location,
                "seller": seller.name,
                "status": status,
                "images": [
                    {
                        "media": image_media.name,
                        "image": image_media.public_url,
                        "is_primary": 1,
                        "sort_order": 0,
                    }
                ],
            }
        )
        ad.insert(ignore_permissions=True)
        frappe.db.commit()
        return ad

    def make_conversation(self, user_a: str, user_b: str, *, with_message: bool = False):
        p1, p2 = sorted([user_a, user_b])
        conv_name = frappe.db.get_value(
            "AOS Conversation",
            {"participant_1": p1, "participant_2": p2},
            "name",
        )
        if conv_name:
            conv = frappe.get_doc("AOS Conversation", conv_name)
        else:
            conv = frappe.get_doc(
                {
                    "doctype": "AOS Conversation",
                    "participant_1": p1,
                    "participant_2": p2,
                    "is_active_1": 1,
                    "is_active_2": 1,
                }
            )
            conv.insert(ignore_permissions=True)

        if with_message and not frappe.db.exists("AOS Message", {"conversation": conv.name}):
            frappe.get_doc(
                {
                    "doctype": "AOS Message",
                    "conversation": conv.name,
                    "sender": user_a,
                    "message_type": "text",
                    "content": "Feature test communication",
                }
            ).insert(ignore_permissions=True)

        frappe.db.commit()
        return conv

    def make_short(self, *, owner: str):
        media = self.make_media(
            owner=owner,
            purpose="short_raw",
            content_type="video/mp4",
            filename="short.mp4",
        )
        short = frappe.get_doc(
            {
                "doctype": "AOS Short",
                "file_key": f"tests/{self.prefix}/{uuid.uuid4().hex}.mp4",
                "raw_video_media": media.name,
                "status": "ready",
                "visibility_status": "visible",
                "content_mode": "vibes",
                "audience": "everyone",
                "allow_comments": 1,
                "allow_downloads": 0,
                "caption": "Feature test short",
                "playback_url": f"https://cdn.example.test/{self.prefix}/short.m3u8",
                "thumbnail_url": f"https://cdn.example.test/{self.prefix}/short.jpg",
                "duration_seconds": 10,
            }
        )
        short.insert(ignore_permissions=True)
        frappe.db.commit()
        return short

    def make_report_reason(self) -> str:
        reason = f"{self.prefix} Abuse"
        if not frappe.db.exists("AOS Report Reason", reason):
            frappe.get_doc(
                {
                    "doctype": "AOS Report Reason",
                    "title": reason,
                    "is_active": 1,
                }
            ).insert(ignore_permissions=True)
        frappe.db.commit()
        return reason

    def make_live(self, *, host: str):
        live = frappe.get_doc(
            {
                "doctype": "AOS Live Stream",
                "title": f"{self.prefix} Live",
                "host_user": host,
                "status": "live",
                "is_active": 1,
            }
        )
        live.insert(ignore_permissions=True)
        frappe.db.commit()
        return live

    def fake_media_doc(self, *, name: str = "MEDIA-TEST", purpose: str = "profile_image"):
        return SimpleNamespace(
            name=name,
            purpose=purpose,
            status="Initialized",
            visibility="Public",
            original_filename="avatar.jpg",
            content_type="image/jpeg",
            size_bytes=1024,
            width=None,
            height=None,
            duration_seconds=None,
        )

    def cleanup_feature_rows(self):
        like = f"{self.prefix}%"
        email_like = f"{self.prefix}-%@example.com"
        path_like = f"tests/{self.prefix}/%"

        frappe.set_user("Administrator")

        # Feature/action rows first.
        frappe.db.sql("DELETE FROM `tabAOS Notification` WHERE user LIKE %s OR actor LIKE %s", (email_like, email_like))
        frappe.db.sql("DELETE FROM `tabAOS Push Token` WHERE user LIKE %s OR device_id LIKE %s OR token LIKE %s", (email_like, like, like))
        frappe.db.sql("DELETE FROM `tabAOS User Activity` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS User Block` WHERE blocker_user LIKE %s OR blocked_user LIKE %s", (email_like, email_like))
        frappe.db.sql("DELETE FROM `tabAOS User Report` WHERE reported_user LIKE %s OR reported_by LIKE %s", (email_like, email_like))

        frappe.db.sql("DELETE FROM `tabAOS Live Message` WHERE user LIKE %s OR content LIKE %s", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS Live Stream View` WHERE user LIKE %s OR session_id LIKE %s", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS Live Stream` WHERE host_user LIKE %s OR title LIKE %s", (email_like, like))

        frappe.db.sql("DELETE FROM `tabAOS Message Attachment` WHERE message IN (SELECT name FROM `tabAOS Message` WHERE sender LIKE %s)", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Message Reaction` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Message Star` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Message` WHERE sender LIKE %s OR conversation IN (SELECT name FROM `tabAOS Conversation` WHERE participant_1 LIKE %s OR participant_2 LIKE %s)", (email_like, email_like, email_like))
        frappe.db.sql("DELETE FROM `tabAOS Conversation` WHERE participant_1 LIKE %s OR participant_2 LIKE %s", (email_like, email_like))

        frappe.db.sql("DELETE FROM `tabAOS Review Reaction` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Review Image` WHERE parent IN (SELECT name FROM `tabAOS Review` WHERE reviewer LIKE %s)", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Review` WHERE reviewer LIKE %s OR ad IN (SELECT name FROM `tabAOS Ad` WHERE title LIKE %s)", (email_like, like))

        frappe.db.sql("DELETE FROM `tabAOS Short Comment Like` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Short Comment` WHERE user LIKE %s OR short IN (SELECT name FROM `tabAOS Short` WHERE file_key LIKE %s)", (email_like, path_like))
        frappe.db.sql("DELETE FROM `tabAOS Short Like` WHERE user LIKE %s OR short IN (SELECT name FROM `tabAOS Short` WHERE file_key LIKE %s)", (email_like, path_like))
        frappe.db.sql("DELETE FROM `tabAOS Short Save` WHERE user LIKE %s OR short IN (SELECT name FROM `tabAOS Short` WHERE file_key LIKE %s)", (email_like, path_like))
        frappe.db.sql("DELETE FROM `tabAOS Short Report` WHERE reported_by LIKE %s OR short IN (SELECT name FROM `tabAOS Short` WHERE file_key LIKE %s)", (email_like, path_like))
        frappe.db.sql("DELETE FROM `tabAOS Short View` WHERE user LIKE %s OR session_id LIKE %s OR short IN (SELECT name FROM `tabAOS Short` WHERE file_key LIKE %s)", (email_like, like, path_like))
        frappe.db.sql("DELETE FROM `tabAOS Short` WHERE file_key LIKE %s", (path_like,))

        frappe.db.sql("DELETE FROM `tabAOS Ad Report` WHERE reported_by LIKE %s OR ad IN (SELECT name FROM `tabAOS Ad` WHERE title LIKE %s)", (email_like, like))
        frappe.db.sql("DELETE FROM `tabAOS Ad Image` WHERE parent IN (SELECT name FROM `tabAOS Ad` WHERE title LIKE %s)", (like,))
        frappe.db.sql("DELETE FROM `tabAOS Ad` WHERE title LIKE %s", (like,))
        frappe.db.sql("DELETE FROM `tabAOS Seller` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Media Object` WHERE owner_user LIKE %s OR object_key LIKE %s", (email_like, path_like))

        frappe.db.sql("DELETE FROM `tabAOS Report Reason` WHERE title LIKE %s", (like,))
        frappe.db.sql("DELETE FROM `tabAOS Location` WHERE location LIKE %s", (like,))
        frappe.db.sql("DELETE FROM `tabAOS Category` WHERE category_name LIKE %s", (like,))

        frappe.db.sql("DELETE FROM `tabAOS Email Verification` WHERE user LIKE %s OR email LIKE %s", (email_like, email_like))
        frappe.db.sql("DELETE FROM `tabAOS User Preference` WHERE user LIKE %s", (email_like,))
        frappe.db.sql("DELETE FROM `tabAOS Profile` WHERE user LIKE %s", (email_like,))

        for user in list(getattr(self, "created_users", [])):
            if frappe.db.exists("User", user):
                frappe.delete_doc("User", user, ignore_permissions=True, force=True)

        frappe.db.commit()
