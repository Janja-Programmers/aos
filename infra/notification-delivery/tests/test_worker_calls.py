from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

from app import worker


class _FakeMessaging:
	def __init__(self):
		self.sent = []

	def Notification(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def AndroidNotification(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def AndroidConfig(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def APNSConfig(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def WebpushConfig(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def MulticastMessage(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def send_each_for_multicast(self, message):
		self.sent.append(message)
		targets = getattr(message, "tokens", None) or getattr(message, "fids", None) or []
		responses = [
			SimpleNamespace(success=True, message_id=f"provider-{idx}", exception=None)
			for idx, _target in enumerate(targets)
		]
		return SimpleNamespace(
			success_count=len(responses),
			failure_count=0,
			responses=responses,
		)


def _settings():
	return SimpleNamespace(dry_run=False, max_tokens_per_multicast=500)


def test_android_incoming_call_is_data_only_and_collapsible(monkeypatch):
	fake_messaging = _FakeMessaging()
	monkeypatch.setattr(worker, "messaging", fake_messaging)
	monkeypatch.setattr(worker, "get_settings", _settings)
	monkeypatch.setattr(worker, "_init_firebase", lambda: None)

	result = worker._send_push(
		{
			"event": "aos_incoming_call",
			"delivery_kind": "transient",
			"title": "Incoming Call",
			"body": "AOS User is calling you",
			"data": {
				"event": "aos_incoming_call",
				"call_id": "call_0123456789abcdef0123456789abcdef",
				"caller": "ACC-2026-00001",
				"call_type": "audio",
			},
			"options": {
				"priority": "high",
				"ttl_seconds": 30,
				"android_channel_id": "aos_calls",
				"android_notification_priority": "max",
			},
			"tokens": [
				{"token": "android-token", "token_hash": "android-hash", "device_type": "android"},
				{"token": "ios-token", "token_hash": "ios-hash", "device_type": "ios"},
			],
		}
	)

	assert result["status"] == "delivered"
	assert result["success_count"] == 2
	assert len(fake_messaging.sent) == 2

	android_message, ios_message = fake_messaging.sent
	assert android_message.tokens == ["android-token"]
	assert android_message.notification is None
	assert android_message.data["call_id"] == "call_0123456789abcdef0123456789abcdef"
	assert android_message.android.priority == "high"
	assert android_message.android.ttl.total_seconds() == 30
	assert android_message.android.collapse_key == "aos-call:call_0123456789abcdef0123456789abcdef"
	assert android_message.android.notification is None

	assert ios_message.tokens == ["ios-token"]
	assert ios_message.notification.title == "Incoming Call"
	assert ios_message.notification.body == "AOS User is calling you"
	assert ios_message.android.collapse_key is None
	assert ios_message.apns.headers["apns-priority"] == "10"
	assert int(ios_message.apns.headers["apns-expiration"]) > 0
	assert ios_message.webpush.headers["TTL"] == "30"
	assert ios_message.webpush.headers["Urgency"] == "high"
	assert result["provider_responses"][0]["delivery_mode"] == "android_data_only"
	assert result["provider_responses"][1]["delivery_mode"] == "alert_and_data"


def test_normal_push_keeps_alert_payload_for_android(monkeypatch):
	fake_messaging = _FakeMessaging()
	monkeypatch.setattr(worker, "messaging", fake_messaging)
	monkeypatch.setattr(worker, "get_settings", _settings)
	monkeypatch.setattr(worker, "_init_firebase", lambda: None)

	result = worker._send_push(
		{
			"event": "aos_new_message",
			"delivery_kind": "persistent",
			"title": "New Message",
			"body": "Hello",
			"data": {"event": "aos_new_message", "message_id": "MSG-1"},
			"options": {"priority": "high"},
			"tokens": [
				{"token": "android-token", "token_hash": "android-hash", "device_type": "android"},
			],
		}
	)

	assert result["status"] == "delivered"
	assert len(fake_messaging.sent) == 1
	message = fake_messaging.sent[0]
	assert message.tokens == ["android-token"]
	assert message.notification.title == "New Message"
	assert message.notification.body == "Hello"
	assert message.android.collapse_key is None


def test_normal_web_push_keeps_alert_and_data_with_web_headers(monkeypatch):
	fake_messaging = _FakeMessaging()
	monkeypatch.setattr(worker, "messaging", fake_messaging)
	monkeypatch.setattr(worker, "get_settings", _settings)
	monkeypatch.setattr(worker, "_init_firebase", lambda: None)

	result = worker._send_push(
		{
			"event": "aos_follow",
			"delivery_kind": "persistent",
			"title": "New Follower",
			"body": "Someone followed you",
			"data": {
				"event": "aos_follow",
				"notification_id": "NTF-2026-00001",
				"notification_type": "follow",
			},
			"options": {"priority": "normal", "ttl_seconds": 300},
			"tokens": [
				{"token": "web-token", "token_hash": "web-hash", "device_type": "web"},
			],
		}
	)

	assert result["status"] == "delivered"
	message = fake_messaging.sent[0]
	assert message.tokens == ["web-token"]
	assert message.notification.title == "New Follower"
	assert message.data["notification_id"] == "NTF-2026-00001"
	assert message.webpush.headers["TTL"] == "300"
	assert message.webpush.headers["Urgency"] == "normal"


def test_firebase_initialization_applies_bounded_http_timeout(monkeypatch, tmp_path):
	service_account = tmp_path / "firebase.json"
	service_account.write_text("{}", encoding="utf-8")
	initialized = []

	class _FirebaseAdmin:
		_apps: ClassVar[dict[str, object]] = {}

		@staticmethod
		def initialize_app(credential, options=None):
			initialized.append((credential, options))

	class _Credentials:
		@staticmethod
		def Certificate(path):
			return {"path": path}

	monkeypatch.setattr(worker, "firebase_admin", _FirebaseAdmin)
	monkeypatch.setattr(worker, "credentials", _Credentials)
	monkeypatch.setattr(
		worker,
		"get_settings",
		lambda: SimpleNamespace(
			dry_run=False,
			firebase_service_account_path=str(service_account),
			provider_timeout_seconds=17,
		),
	)
	monkeypatch.setattr(worker, "_FIREBASE_INITIALIZED", False)
	worker._init_firebase()
	assert initialized[0][1] == {"httpTimeout": 17}


def test_mixed_legacy_tokens_and_fids_use_the_matching_firebase_target_field(monkeypatch):
	fake_messaging = _FakeMessaging()
	monkeypatch.setattr(worker, "messaging", fake_messaging)
	monkeypatch.setattr(worker, "get_settings", _settings)
	monkeypatch.setattr(worker, "_init_firebase", lambda: None)

	result = worker._send_push(
		{
			"event": "aos_follow",
			"delivery_kind": "persistent",
			"title": "New follower",
			"body": "Someone followed you",
			"data": {"event": "aos_follow"},
			"options": {"priority": "normal"},
			"tokens": [
				{
					"token": "legacy-registration-token",
					"token_hash": "a" * 64,
					"device_type": "android",
					"registration_kind": "token",
				},
				{
					"token": "firebase-installation-id",
					"token_hash": "b" * 64,
					"device_type": "web",
					"registration_kind": "fid",
				},
			],
		}
	)

	assert result["status"] == "delivered"
	assert result["success_count"] == 2
	assert len(fake_messaging.sent) == 2
	legacy_message, fid_message = fake_messaging.sent
	assert legacy_message.tokens == ["legacy-registration-token"]
	assert not hasattr(legacy_message, "fids")
	assert fid_message.fids == ["firebase-installation-id"]
	assert not hasattr(fid_message, "tokens")
	assert [row["registration_kind"] for row in result["provider_responses"]] == ["token", "fid"]
