from __future__ import annotations

import json
from types import SimpleNamespace

from app import config, main
from app.security import build_signature, verify_signature
from fastapi.testclient import TestClient

SECRET = "unit-test-secret"


def settings(**overrides):
	values = {
		"service_name": "aos-notification-delivery",
		"environment": "test",
		"request_secret": SECRET,
		"queue_name": "notification-delivery",
		"job_timeout_seconds": 600,
		"result_ttl_seconds": 60,
		"failure_ttl_seconds": 60,
		"callback_job_timeout_seconds": 120,
		"callback_max_attempts": 3,
		"durable_result_ttl_seconds": 604800,
		"dry_run": True,
		"firebase_service_account_path": "/tmp/unused-firebase.json",
		"provider_max_retries": 3,
		"provider_timeout_seconds": 20,
		"callback_http_timeout_seconds": 20,
	}
	values.update(overrides)
	return SimpleNamespace(**values)


def payload() -> dict:
	return {
		"job_id": "job-1",
		"notification_id": "NTF-2026-00001",
		"delivery_kind": "persistent",
		"channel": "push",
		"user": "ACC-2026-00001",
		"event": "aos_new_message",
		"title": "New Message",
		"body": "You have a new message",
		"data": {"event": "aos_new_message", "message_id": "MSG-2026-00001"},
		"options": {},
		"callback_url": "https://callback.invalid/notification",
		"tokens": [],
	}


def signed(data: dict) -> tuple[bytes, dict[str, str]]:
	body = json.dumps(data, separators=(",", ":")).encode()
	return body, {"X-AOS-Notification-Signature": build_signature(SECRET, body)}


def test_configuration_defaults_and_invalid_integer(monkeypatch):
	monkeypatch.setenv("NOTIFICATION_MAX_TOKENS_PER_MULTICAST", "invalid")
	assert config._int("NOTIFICATION_MAX_TOKENS_PER_MULTICAST", 500) == 500
	assert config.Settings().queue_name == "notification-delivery"




def test_configuration_bounds_provider_retry_and_timeouts(monkeypatch):
	monkeypatch.setenv("NOTIFICATION_PROVIDER_MAX_RETRIES", "999")
	monkeypatch.setenv("NOTIFICATION_PROVIDER_TIMEOUT_SECONDS", "1")
	monkeypatch.setenv("NOTIFICATION_CALLBACK_HTTP_TIMEOUT_SECONDS", "999")
	config.get_settings.cache_clear()
	try:
		resolved = config.get_settings()
		assert resolved.provider_max_retries == 10
		assert resolved.provider_timeout_seconds == 5
		assert resolved.callback_http_timeout_seconds == 120
	finally:
		config.get_settings.cache_clear()


def test_firebase_configuration_validation_is_local_and_fail_closed(monkeypatch, tmp_path):
	# Dry-run smoke tests intentionally do not require Firebase credentials.
	config.validate_firebase_configuration(config.Settings(dry_run=True))

	monkeypatch.setattr(config.importlib.util, "find_spec", lambda _name: object())
	missing = config.Settings(
		dry_run=False,
		firebase_service_account_path=str(tmp_path / "missing.json"),
	)
	try:
		config.validate_firebase_configuration(missing)
	except RuntimeError as exc:
		assert "unavailable" in str(exc)
	else:
		raise AssertionError("missing Firebase credentials must fail readiness")

	credential = tmp_path / "firebase.json"
	credential.write_text(
		json.dumps(
			{
				"project_id": "aos-production",
				"client_email": "firebase-adminsdk@aos-production.iam.gserviceaccount.com",
				"private_key": "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n",
			}
		),
		encoding="utf-8",
	)
	configured = config.Settings(
		dry_run=False,
		firebase_service_account_path=str(credential),
	)
	config.validate_firebase_configuration(configured)


def test_ready_checks_redis_and_firebase_configuration(monkeypatch):
	class Redis:
		def ping(self):
			return True

	checks = []
	monkeypatch.setattr(main, "get_redis", lambda: Redis())
	monkeypatch.setattr(main, "get_settings", lambda: settings(dry_run=False))
	monkeypatch.setattr(
		main,
		"validate_firebase_configuration",
		lambda value: checks.append(value.firebase_service_account_path),
	)
	response = TestClient(main.app).get("/ready")
	assert response.status_code == 200
	assert response.json() == {"ok": True, "ready": True}
	assert checks == ["/tmp/unused-firebase.json"]


def test_signature_checks():
	body = b"payload"
	signature = build_signature(SECRET, body)
	assert verify_signature(SECRET, body, signature)
	assert not verify_signature(SECRET, b"other", signature)
	assert not verify_signature(SECRET, body, None)
	assert not verify_signature(" ", body, signature)


def test_health(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["environment"] == "test"


def test_valid_job_is_enqueued(monkeypatch):
	calls = []

	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *args, **kwargs):
			calls.append((args, kwargs))
			return SimpleNamespace(id="rq-1")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed(payload())
	response = TestClient(main.app).post("/jobs", content=body, headers=headers)
	assert response.status_code == 202
	assert response.json()["service_job_id"] == "rq-1"
	assert calls[0][1]["job_id"] == "job-1"


def test_queue_failure_returns_server_error(monkeypatch):
	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *_args, **_kwargs):
			raise RuntimeError("synthetic queue failure")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed(payload())
	response = TestClient(main.app, raise_server_exceptions=False).post(
		"/jobs", content=body, headers=headers
	)
	assert response.status_code == 500


def test_signature_and_request_validation(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	assert client.post("/jobs", json=payload()).status_code == 401

	body, headers = signed({"job_id": "job-1", "user": 42, "callback_url": "x"})
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code == 422
	assert response.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(response.json()["data"]["fields"], list)


def test_job_contract_accepts_fid_and_defaults_legacy_rows_to_token():
	legacy = main.PushToken(
		token="legacy-registration-token-abcdefghijklmnopqrstuvwxyz",
		token_hash="a" * 64,
		device_type="android",
	)
	assert legacy.registration_kind == "token"
	fid = main.PushToken(
		token="firebase-installation-id",
		token_hash="b" * 64,
		device_type="web",
		registration_kind="fid",
	)
	assert fid.registration_kind == "fid"


def test_job_contract_bounds_per_recipient_device_fanout(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	many_tokens = [
		{
			"token": f"web-token-{index:04d}-abcdefghijklmnopqrstuvwxyz",
			"token_hash": f"{index:064x}"[-64:],
			"device_type": "web",
		}
		for index in range(501)
	]
	body, headers = signed({**payload(), "tokens": many_tokens})
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code == 422
	assert response.json()["error"] == "VALIDATION_ERROR"


def test_job_contract_rejects_oversized_total_fcm_envelope(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	oversized = {
		**payload(),
		"title": "😀" * 140,
		"body": "😀" * 500,
		"data": {"padding": "x" * 1200},
	}
	body, headers = signed(oversized)
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code == 422
	assert response.json()["error"] == "VALIDATION_ERROR"


def test_job_contract_rejects_unknown_fields_and_unsafe_callback(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)

	unknown = {**payload(), "provider_options": {"arbitrary": True}}
	body, headers = signed(unknown)
	assert client.post("/jobs", content=body, headers=headers).status_code == 422

	unsafe = {**payload(), "callback_url": "file:///etc/passwd"}
	body, headers = signed(unsafe)
	assert client.post("/jobs", content=body, headers=headers).status_code == 422


def test_transient_contract_allows_only_existing_incoming_call_event(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	transient = {
		**payload(),
		"notification_id": None,
		"delivery_kind": "transient",
		"event": "aos_incoming_call",
	}
	body, headers = signed(transient)
	# Queue dependencies may be unavailable in this focused contract test; 422 is
	# the important forbidden outcome for a valid canonical transient payload.
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code != 422

	bad = {**transient, "event": "generic_call_notification"}
	body, headers = signed(bad)
	assert client.post("/jobs", content=body, headers=headers).status_code == 422


def test_blank_secret_fails_closed(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(request_secret=""))
	body, headers = signed(payload())
	assert TestClient(main.app).post("/jobs", content=body, headers=headers).status_code == 401


def test_operational_metrics_are_private_and_redacted(monkeypatch):
	secret = "metrics-token-secret-0123456789"
	monkeypatch.setenv("AOS_METRICS_ALLOW_LOOPBACK", "false")
	monkeypatch.setenv("AOS_METRICS_TOKEN", secret)
	client = TestClient(main.app)
	denied = client.get("/metrics")
	assert denied.status_code == 403
	response = client.get("/metrics", headers={"Authorization": f"Bearer {secret}"})
	assert response.status_code == 200
	assert "aos_companion_http_requests_total" in response.text
	assert "aos_companion_http_request_duration_seconds" in response.text
	assert "aos_companion_unhandled_exceptions_total" in response.text
	assert "aos_companion_dependency_ready" in response.text
	assert secret not in response.text
	assert "authorization" not in response.text.lower()


def test_unhandled_exception_response_never_leaks_raw_details():
	leaked = "token=do-not-leak /srv/private/internal.py https://internal.invalid"
	path = f"/__observability_failure_{main.app.title.replace(' ', '_').lower()}"

	async def fail_for_test():
		raise RuntimeError(leaked)

	main.app.add_api_route(path, fail_for_test, methods=["GET"], include_in_schema=False)
	response = TestClient(main.app, raise_server_exceptions=False).get(path)
	assert response.status_code == 500
	serialized = response.text
	assert "INTERNAL_ERROR" in serialized
	assert leaked not in serialized
	assert "/srv/private" not in serialized
	assert "internal.invalid" not in serialized
	assert "Traceback" not in serialized


def test_request_validation_error_redacts_sensitive_input(caplog):
	import asyncio

	from app.observability import render_metrics, request_validation_error_handler
	from fastapi.exceptions import RequestValidationError
	from starlette.requests import Request

	sensitive = "Bearer super-secret-token https://internal.invalid /srv/private/key.pem"
	request = Request({"type": "http", "method": "POST", "path": "/jobs", "headers": []})
	exc = RequestValidationError(
		[
			{
				"type": "string_type",
				"loc": ("body", "payload", "url"),
				"msg": "Input should be a valid string",
				"input": sensitive,
				"ctx": {"error": sensitive},
			}
		]
	)
	response = asyncio.run(request_validation_error_handler(request, exc))
	serialized = response.body.decode("utf-8") + caplog.text + render_metrics()
	assert response.status_code == 422
	assert "VALIDATION_ERROR" in serialized
	assert "payload.url" in serialized
	assert sensitive not in serialized
	assert "super-secret-token" not in serialized
	assert "internal.invalid" not in serialized
	assert "/srv/private" not in serialized


def test_idempotent_dispatch_uses_stable_job_id_and_preserves_active_generation(redis_conn, rq_queue):
	from app.idempotent_dispatch import enqueue_idempotent
	from rq import Retry

	payload = {
		"job_id": "service-job-1",
		"idempotency_key": "stable-dispatch-id",
		"dispatch_id": "stable-outbox-id",
		"dispatch_generation": 1,
		"dispatch_token": "a" * 32,
		"callback_url": "https://callback.invalid/result",
	}
	job, action = enqueue_idempotent(
		queue=rq_queue,
		worker_method="app.worker.process",
		payload=payload,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
	)
	assert action == "enqueued"
	assert job.id == "stable-dispatch-id"

	recovery = {**payload, "dispatch_generation": 2, "dispatch_token": "b" * 32}
	job_again, action_again = enqueue_idempotent(
		queue=rq_queue,
		worker_method="app.worker.process",
		payload=recovery,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
	)
	assert job_again.id == job.id
	# The queued older generation has not begun external work and is safely
	# replaced under the same stable work ID.
	assert action_again == "stale_generation_replaced"
	assert rq_queue.fetch_job("stable-dispatch-id").args[0]["dispatch_generation"] == 2


def test_validation_location_normalization_never_echoes_dynamic_keys(caplog):
	import asyncio
	import json as _json

	from app.observability import request_validation_error_handler
	from app.validation_fields import normalize_validation_location
	from fastapi import Request
	from fastapi.exceptions import RequestValidationError

	sensitive_values = [
		"https://internal.invalid/secret",
		"/srv/private/token.txt",
		"Bearer super-secret-token",
		"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.signature",
		"x" * 500,
		"control\x00\nkey",
	]
	for sensitive in sensitive_values:
		field = normalize_validation_location(("body", "payload", "items", 7, sensitive))
		assert field == "payload.items[].field"
		assert sensitive not in field

	nested = normalize_validation_location(("body", "payload", "attacker_key", "nested_key"))
	assert nested == "payload.item.field"

	scope = {
		"type": "http",
		"method": "POST",
		"path": "/jobs",
		"headers": [],
		"query_string": b"",
		"client": ("127.0.0.1", 1),
		"server": ("test", 80),
		"scheme": "http",
	}
	exc = RequestValidationError(
		[
			{
				"type": "value_error",
				"loc": ("body", "payload", "items", 0, sensitive_values[0]),
				"msg": "bad",
				"input": "secret",
			}
		]
	)
	response = asyncio.run(request_validation_error_handler(Request(scope), exc))
	serialized = response.body.decode("utf-8") + caplog.text
	assert response.status_code == 422
	assert "payload.items[].field" in serialized
	for sensitive in sensitive_values:
		assert sensitive not in serialized
	assert "secret" not in _json.dumps(_json.loads(response.body)).lower()


def test_internal_job_status_endpoint_is_signed_and_bounded(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	payload = {"job_id": "service-job-status", "idempotency_key": "stable-status-id"}
	assert client.post("/internal/jobs/status", json=payload).status_code == 401
	body, headers = signed(payload)
	response = client.post("/internal/jobs/status", content=body, headers=headers)
	assert response.status_code == 200
	data = response.json()
	assert data["state"] == "absent"
	assert "result_payload" not in data
	assert "dispatch_token" not in data
	assert client.post("/internal/jobs/callback/replay", json=payload).status_code == 401


def test_signed_uncertainty_resolution_records_operator_reference(monkeypatch):
	seen = {}
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_redis", lambda: object())
	monkeypatch.setattr(main, "get_queue", lambda: object())

	def resolve(**kwargs):
		seen.update(kwargs)
		return {"ok": True, "resolution": kwargs["resolution"]}

	monkeypatch.setattr(main, "resolve_uncertain_outcome", resolve)
	body, headers = signed(
		{
			"job_id": "job-1",
			"idempotency_key": "stable-job-1",
			"resolution": "confirmed_accepted",
			"operator_reference": "incident-2026-001",
		}
	)
	response = TestClient(main.app).post("/internal/jobs/uncertainty/resolve", content=body, headers=headers)
	assert response.status_code == 202
	assert response.json()["resolution"] == "confirmed_accepted"
	assert seen["stable_id"] == "stable-job-1"
	assert seen["operator_reference"] == "incident-2026-001"


def test_uncertainty_resolution_requires_signature(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	response = TestClient(main.app).post(
		"/internal/jobs/uncertainty/resolve",
		json={"job_id": "job-1", "resolution": "permanently_unresolved"},
	)
	assert response.status_code == 401
