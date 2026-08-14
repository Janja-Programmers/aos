from __future__ import annotations

import pytest
from app import worker


def test_work_happy_path_is_separate_from_callback(monkeypatch):
	monkeypatch.setattr(
		worker,
		"_send_push",
		lambda _payload: {
			"status": "delivered",
			"success_count": 1,
			"failure_count": 0,
			"inactive_token_hashes": [],
			"provider_responses": [{"success_count": 1, "provider_acceptance_ids": ["abc"]}],
			"error": None,
		},
	)
	result = worker._perform_notification_work(
		{
			"job_id": "job-1",
			"callback_url": "https://callback.invalid/notification",
			"tokens": [{"token": "synthetic-token"}],
		}
	)
	assert result["status"] == "delivered"
	assert result["success_count"] == 1


def test_provider_transport_uncertainty_is_not_blindly_retried(monkeypatch):
	monkeypatch.setattr(
		worker, "_send_push", lambda _payload: (_ for _ in ()).throw(TimeoutError("provider timeout"))
	)
	with pytest.raises(TimeoutError):
		worker._perform_notification_work(
			{
				"job_id": "job-2",
				"callback_url": "https://callback.invalid/notification",
				"tokens": [{"token": "synthetic-token"}],
			}
		)


def test_persisted_provider_success_prevents_duplicate_push_send(monkeypatch, redis_conn, rq_queue):
	calls = []

	def send_push(_payload):
		calls.append("provider-send")
		return {
			"status": "delivered",
			"success_count": 1,
			"failure_count": 0,
			"inactive_token_hashes": [],
			"provider_responses": [{"success_count": 1, "provider_acceptance_ids": ["provider-id-hash"]}],
			"error": None,
		}

	monkeypatch.setattr(worker, "_send_push", send_push)
	payload = {
		"job_id": "notification-job-1",
		"idempotency_key": "stable-notification-delivery-1",
		"dispatch_id": "stable-outbox-dispatch-1",
		"dispatch_generation": 1,
		"dispatch_token": "a" * 32,
		"callback_url": "https://callback.invalid/notification",
		"tokens": [{"token": "synthetic-token"}],
	}
	first = worker.process_notification_delivery_job(payload)
	second = worker.process_notification_delivery_job(payload)
	assert first["status"] == "delivered"
	assert second["status"] == "delivered"
	assert calls == ["provider-send"]


def test_fully_transient_provider_failure_uses_bounded_rq_retry(monkeypatch):
	monkeypatch.setattr(
		worker,
		"_send_push",
		lambda _payload: {
			"status": "failed",
			"success_count": 0,
			"failure_count": 2,
			"inactive_token_hashes": [],
			"provider_responses": [],
			"retryable_failure_count": 2,
			"error": "transient_provider_failure:UnavailableError",
		},
	)
	monkeypatch.setattr(worker, "_rq_retries_left", lambda: 2)
	with pytest.raises(worker.RetryableWorkError):
		worker._perform_notification_work(
			{
				"job_id": "job-retryable",
				"callback_url": "https://callback.invalid/notification",
				"tokens": [{"token": "token-1"}, {"token": "token-2"}],
			}
		)


def test_retryable_provider_failure_becomes_terminal_when_retry_budget_is_exhausted(monkeypatch):
	monkeypatch.setattr(
		worker,
		"_send_push",
		lambda _payload: {
			"status": "failed",
			"success_count": 0,
			"failure_count": 1,
			"inactive_token_hashes": [],
			"provider_responses": [],
			"retryable_failure_count": 1,
			"error": "transient_provider_failure:UnavailableError",
		},
	)
	monkeypatch.setattr(worker, "_rq_retries_left", lambda: 0)
	result = worker._perform_notification_work(
		{
			"job_id": "job-retry-exhausted",
			"callback_url": "https://callback.invalid/notification",
			"tokens": [{"token": "token-1"}],
		}
	)
	assert result["status"] == "failed"
	assert result["failure_count"] == 1


def test_partial_success_is_not_retried_to_avoid_duplicate_device_delivery(monkeypatch):
	monkeypatch.setattr(
		worker,
		"_send_push",
		lambda _payload: {
			"status": "delivered",
			"success_count": 1,
			"failure_count": 1,
			"inactive_token_hashes": [],
			"provider_responses": [],
			"retryable_failure_count": 1,
			"error": "transient_provider_failure:UnavailableError",
		},
	)
	monkeypatch.setattr(worker, "_rq_retries_left", lambda: 2)
	result = worker._perform_notification_work(
		{
			"job_id": "job-partial",
			"callback_url": "https://callback.invalid/notification",
			"tokens": [{"token": "token-1"}, {"token": "token-2"}],
		}
	)
	assert result["status"] == "delivered"
	assert result["success_count"] == 1
	assert result["failure_count"] == 1
