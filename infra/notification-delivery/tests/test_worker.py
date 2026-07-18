from __future__ import annotations

import pytest
from app import worker


def test_worker_happy_path_stubs_provider_and_callback(monkeypatch):
	callbacks = []
	monkeypatch.setattr(
		worker,
		"_send_push",
		lambda _payload: {
			"status": "delivered",
			"success_count": 1,
			"failure_count": 0,
			"inactive_token_hashes": [],
			"provider_responses": [{"success_count": 1}],
			"error": None,
		},
	)
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))

	result = worker.process_notification_delivery_job(
		{
			"job_id": "job-1",
			"callback_url": "https://callback.invalid/notification",
			"tokens": [{"token": "synthetic-token"}],
		}
	)
	assert result["status"] == "delivered"
	assert result["success_count"] == 1
	assert callbacks[0][1]["status"] == "delivered"


def test_worker_failure_reports_failure_and_reraises(monkeypatch):
	callbacks = []
	monkeypatch.setattr(
		worker, "_send_push", lambda _payload: (_ for _ in ()).throw(RuntimeError("provider failed"))
	)
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))
	with pytest.raises(RuntimeError, match="provider failed"):
		worker.process_notification_delivery_job(
			{
				"job_id": "job-2",
				"callback_url": "https://callback.invalid/notification",
				"tokens": [{"token": "synthetic-token"}],
			}
		)
	assert callbacks[-1][1]["status"] == "failed"
