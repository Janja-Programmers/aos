from __future__ import annotations

from types import SimpleNamespace

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

	def MulticastMessage(self, **kwargs):
		return SimpleNamespace(**kwargs)

	def send_each_for_multicast(self, message):
		self.sent.append(message)
		responses = [
			SimpleNamespace(success=True, message_id=f"provider-{idx}", exception=None)
			for idx, _token in enumerate(message.tokens)
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
				"call_id": "CALL-2026-00001",
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
	assert android_message.data["call_id"] == "CALL-2026-00001"
	assert android_message.android.priority == "high"
	assert android_message.android.ttl.total_seconds() == 30
	assert android_message.android.collapse_key == "aos-call:CALL-2026-00001"
	assert android_message.android.notification is None

	assert ios_message.tokens == ["ios-token"]
	assert ios_message.notification.title == "Incoming Call"
	assert ios_message.notification.body == "AOS User is calling you"
	assert ios_message.android.collapse_key is None
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
