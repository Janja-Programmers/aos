from __future__ import annotations

from types import SimpleNamespace

import pytest
from app import durable_lifecycle as lifecycle
from app import worker
from app.durable_lifecycle import (
	CallbackDeliveryRetryableError,
	deliver_callback,
	enqueue_or_reconcile,
	execute_work_job,
	job_status,
	load_result,
	replay_callback_delivery,
)
from rq import Retry

SERVICE_TYPE = "search_indexing"
STABLE_ID = "stable-service-job-0001"


def payload(generation: int = 1, token: str = "a" * 32):
	return {
		"job_id": "service-job-1",
		"idempotency_key": STABLE_ID,
		"dispatch_id": "stable-outbox-dispatch-1",
		"dispatch_generation": generation,
		"dispatch_token": token,
		"callback_url": "https://callback.invalid/result",
	}


def response(status_code: int = 200, body: dict | None = None):
	return SimpleNamespace(status_code=status_code, json=lambda: body or {"ok": True})


def test_work_result_persists_before_callback_and_callback_retries_independently(
	monkeypatch, redis_conn, rq_queue
):
	work_calls = []

	def perform(_payload):
		work_calls.append("executed")
		return {"job_id": "service-job-1", "status": "completed", "result": "bounded"}

	monkeypatch.setattr(worker, "_perform_search_work", perform)
	result = worker.process_search_ranking_job(payload())
	assert result["status"] == "completed"
	record = load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["work_state"] == "work_complete"
	assert record["callback_status"] == "pending"
	assert record["result_payload"]
	assert work_calls == ["executed"]

	monkeypatch.setattr(
		worker,
		"_callback",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("callback unavailable")),
	)
	with pytest.raises(CallbackDeliveryRetryableError):
		worker.deliver_callback_job(STABLE_ID)
	record = load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["work_state"] == "work_complete"
	assert record["callback_status"] == "retrying"
	assert work_calls == ["executed"]

	monkeypatch.setattr(worker, "_callback", lambda *_args, **_kwargs: response())
	completed = worker.deliver_callback_job(STABLE_ID)
	assert completed["callback_status"] == "complete"
	assert load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["callback_status"] == "complete"

	# A duplicate work execution reads the durable terminal result and never
	# repeats the external side effect.
	assert worker.process_search_ranking_job(payload())["status"] == "completed"
	assert work_calls == ["executed"]


def test_duplicate_active_preserves_running_generation_and_token(redis_conn, rq_queue):
	running_payload = payload(generation=1, token="a" * 32)
	job = rq_queue.enqueue(
		"app.worker.process_search_ranking_job",
		running_payload,
		job_id=STABLE_ID,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
	)
	job.set_status("started")
	# Queue state is active and generation one remains authoritative when a
	# recovery probe proposes generation two.
	decision = enqueue_or_reconcile(
		redis=redis_conn,
		queue=rq_queue,
		worker_method="app.worker.process_search_ranking_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type=SERVICE_TYPE,
		payload=payload(generation=2, token="b" * 32),
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert decision.job.id == job.id
	assert decision.outcome == "duplicate_active"
	assert decision.authoritative_generation == 1
	assert rq_queue.fetch_job(STABLE_ID).args[0]["dispatch_token"] == "a" * 32


def test_queued_stale_generation_is_replaced_before_work_starts(redis_conn, rq_queue):
	rq_queue.enqueue(
		"app.worker.process_search_ranking_job",
		payload(generation=1, token="a" * 32),
		job_id=STABLE_ID,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
	)
	decision = enqueue_or_reconcile(
		redis=redis_conn,
		queue=rq_queue,
		worker_method="app.worker.process_search_ranking_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type=SERVICE_TYPE,
		payload=payload(generation=2, token="b" * 32),
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert decision.outcome == "stale_generation_replaced"
	assert decision.authoritative_generation == 2
	assert rq_queue.fetch_job(STABLE_ID).args[0]["dispatch_token"] == "b" * 32


def test_completed_work_replays_callback_without_reexecuting_work(monkeypatch, redis_conn, rq_queue):
	calls = []
	monkeypatch.setattr(
		worker,
		"_perform_search_work",
		lambda _payload: calls.append("work") or {"job_id": "service-job-1", "status": "completed"},
	)
	worker.process_search_ranking_job(payload(generation=1, token="a" * 32))
	decision = enqueue_or_reconcile(
		redis=redis_conn,
		queue=rq_queue,
		worker_method="app.worker.process_search_ranking_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type=SERVICE_TYPE,
		payload=payload(generation=2, token="b" * 32),
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert decision.outcome == "callback_replay_scheduled"
	assert decision.authoritative_generation == 2
	assert calls == ["work"]
	record = load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["active_generation"] == "2"
	assert record["active_token"] == "b" * 32


def test_callback_dead_letter_can_be_replayed_without_work(monkeypatch, redis_conn, rq_queue):
	calls = []
	monkeypatch.setattr(
		worker,
		"_perform_search_work",
		lambda _payload: calls.append("work") or {"job_id": "service-job-1", "status": "completed"},
	)
	worker.process_search_ranking_job(payload())

	def forbidden(_url, _payload):
		return response(403, {"error": "CALLBACK_TOKEN_MISMATCH"})

	dead = deliver_callback(
		redis=redis_conn,
		service_type=SERVICE_TYPE,
		stable_id=STABLE_ID,
		send_callback=forbidden,
		result_ttl_seconds=3600,
		callback_max_attempts=2,
	)
	assert dead["callback_status"] == "dead_letter"
	replayed = replay_callback_delivery(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		stable_id=STABLE_ID,
		callback_worker_method="app.worker.deliver_callback_job",
		callback_timeout_seconds=120,
		callback_max_attempts=3,
		result_ttl_seconds=3600,
		failure_ttl_seconds=3600,
	)
	assert replayed["ok"] is True
	assert load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["callback_status"] == "pending"
	assert calls == ["work"]


def test_private_status_exposes_only_bounded_state(monkeypatch, redis_conn, rq_queue):
	monkeypatch.setattr(
		worker,
		"_perform_search_work",
		lambda _payload: {"job_id": "service-job-1", "status": "completed"},
	)
	worker.process_search_ranking_job(payload())
	status = job_status(redis_conn, SERVICE_TYPE, STABLE_ID, rq_queue)
	assert status["state"] == "callback_pending"
	assert status["work_state"] == "work_complete"
	assert "result_payload" not in status
	assert "dispatch_token" not in status


def test_terminal_result_survives_callback_enqueue_failure(redis_conn):
	class CallbackQueueUnavailable:
		def enqueue(self, *_args, **_kwargs):
			raise ConnectionError("callback queue unavailable")

	result = execute_work_job(
		redis=redis_conn,
		queue=CallbackQueueUnavailable(),
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: {"job_id": "service-job-1", "status": "completed"},
		failure_payload=lambda _payload, category: {
			"job_id": "service-job-1",
			"status": "failed",
			"error": category,
		},
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=3600,
		failure_ttl_seconds=3600,
		work_lock_seconds=300,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert result["status"] == "completed"
	record = load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["work_state"] == "work_complete"
	assert record["callback_status"] == "pending"
	assert record["last_callback_error_category"] == "CALLBACK_ENQUEUE_FAILED"
	assert record["result_payload"]


def test_callback_response_loss_retries_without_reapplying_work(redis_conn, rq_queue):
	work_calls = []
	callback_commits = []
	execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: (
			work_calls.append("work") or {"job_id": "service-job-1", "status": "completed"}
		),
		failure_payload=lambda _payload, category: {
			"job_id": "service-job-1",
			"status": "failed",
			"error": category,
		},
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=3600,
		failure_ttl_seconds=3600,
		work_lock_seconds=300,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)

	def committed_but_response_lost(_url, callback_payload):
		callback_commits.append(callback_payload["dispatch_generation"])
		raise TimeoutError("response lost after callback commit")

	with pytest.raises(CallbackDeliveryRetryableError):
		deliver_callback(
			redis=redis_conn,
			service_type=SERVICE_TYPE,
			stable_id=STABLE_ID,
			send_callback=committed_but_response_lost,
			result_ttl_seconds=3600,
			callback_max_attempts=3,
		)
	assert callback_commits == [1]
	assert work_calls == ["work"]
	completed = deliver_callback(
		redis=redis_conn,
		service_type=SERVICE_TYPE,
		stable_id=STABLE_ID,
		send_callback=lambda _url, _payload: response(200, {"ok": True, "idempotent": True}),
		result_ttl_seconds=3600,
		callback_max_attempts=3,
	)
	assert completed["callback_status"] == "complete"
	assert work_calls == ["work"]


class _RetryJob:
	def __init__(self, retries_left: int):
		self.retries_left = retries_left
		self.meta = {}

	def save_meta(self):
		return None


def _generic_failure_payload(payload, category):
	return {"job_id": payload.get("job_id"), "status": "failed", "error": category}


def test_retryable_work_error_uses_rq_retry_without_terminal_callback(monkeypatch, redis_conn, rq_queue):
	monkeypatch.setattr(lifecycle, "get_current_job", lambda: _RetryJob(2))

	def fail(_payload):
		raise lifecycle.RetryableWorkError("temporary")

	with pytest.raises(lifecycle.RetryableWorkError):
		lifecycle.execute_work_job(
			redis=redis_conn,
			queue=rq_queue,
			service_type=SERVICE_TYPE,
			payload=payload(),
			perform_work=fail,
			failure_payload=_generic_failure_payload,
			callback_worker_method="app.worker.deliver_callback_job",
			result_ttl_seconds=604800,
			failure_ttl_seconds=604800,
			work_lock_seconds=60,
			callback_timeout_seconds=120,
			callback_max_attempts=3,
		)
	record = lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["work_state"] == "retrying"
	assert record["work_error_classification"] == "retryable_work_error"
	assert record["callback_status"] == "not_ready"


def test_terminal_work_error_persists_failure_and_schedules_callback(monkeypatch, redis_conn, rq_queue):
	monkeypatch.setattr(lifecycle, "get_current_job", lambda: _RetryJob(0))

	def fail(_payload):
		raise lifecycle.TerminalWorkError("invalid")

	result = lifecycle.execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=fail,
		failure_payload=_generic_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=604800,
		failure_ttl_seconds=604800,
		work_lock_seconds=60,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert result["status"] == "failed"
	record = lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	assert record["work_state"] == "work_failed"
	assert record["work_error_classification"] == "terminal_work_error"
	assert record["callback_status"] == "pending"


def test_callback_state_conflict_is_retryable_not_superseded(redis_conn, rq_queue):
	lifecycle.execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: {"job_id": "service-job-1", "status": "ready"},
		failure_payload=_generic_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=604800,
		failure_ttl_seconds=604800,
		work_lock_seconds=60,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	conflict = response(409, {"error": "CALLBACK_STATE_CONFLICT"})
	with pytest.raises(lifecycle.CallbackDeliveryRetryableError):
		lifecycle.deliver_callback(
			redis=redis_conn,
			service_type=SERVICE_TYPE,
			stable_id=STABLE_ID,
			send_callback=lambda *_args: conflict,
			result_ttl_seconds=604800,
			callback_max_attempts=3,
		)
	assert lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["callback_status"] == "retrying"


def test_reconciliation_never_shortens_durable_result_ttl(redis_conn, rq_queue):
	lifecycle.execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: {"job_id": "service-job-1", "status": "ready"},
		failure_payload=_generic_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=604800,
		failure_ttl_seconds=604800,
		work_lock_seconds=60,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	before = redis_conn.ttl(lifecycle._result_key(SERVICE_TYPE, STABLE_ID))
	lifecycle.enqueue_or_reconcile(
		redis=redis_conn,
		queue=rq_queue,
		worker_method="app.worker.process_search_ranking_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type=SERVICE_TYPE,
		payload=payload(generation=2, token="b" * 32),
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
		durable_result_ttl_seconds=604800,
	)
	after = redis_conn.ttl(lifecycle._result_key(SERVICE_TYPE, STABLE_ID))
	assert before > 600000
	assert after >= before - 2


def test_operator_work_replay_archives_terminal_result_and_blocks_active_callback(redis_conn, rq_queue):
	lifecycle.execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: {"job_id": "service-job-1", "status": "ready"},
		failure_payload=_generic_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=604800,
		failure_ttl_seconds=604800,
		work_lock_seconds=60,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	record = lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)
	callback_job = rq_queue.fetch_job(record["callback_job_id"])
	assert callback_job is not None
	callback_job.set_status("started")
	with pytest.raises(lifecycle.DurableLifecycleError):
		lifecycle.authorize_work_replay(
			redis=redis_conn,
			queue=rq_queue,
			service_type=SERVICE_TYPE,
			stable_id=STABLE_ID,
			result_ttl_seconds=604800,
		)
	assert lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["work_state"] == "work_complete"

	callback_job.set_status("queued")
	result = lifecycle.authorize_work_replay(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		stable_id=STABLE_ID,
		result_ttl_seconds=604800,
	)
	assert result["outcome"] == "work_replay_authorized"
	assert (
		lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["work_state"] == "work_replay_authorized"
	)
	assert rq_queue.fetch_job(record["callback_job_id"]) is None


def test_newer_signed_generation_rearms_completed_callback_without_rerunning_work(redis_conn, rq_queue):
	work_calls = []
	lifecycle.execute_work_job(
		redis=redis_conn,
		queue=rq_queue,
		service_type=SERVICE_TYPE,
		payload=payload(),
		perform_work=lambda _payload: (
			work_calls.append("work") or {"job_id": "service-job-1", "status": "ready"}
		),
		failure_payload=_generic_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=604800,
		failure_ttl_seconds=604800,
		work_lock_seconds=60,
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	lifecycle.deliver_callback(
		redis=redis_conn,
		service_type=SERVICE_TYPE,
		stable_id=STABLE_ID,
		send_callback=lambda *_args: response(),
		result_ttl_seconds=604800,
		callback_max_attempts=3,
	)
	assert lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["callback_status"] == "complete"

	decision = lifecycle.enqueue_or_reconcile(
		redis=redis_conn,
		queue=rq_queue,
		worker_method="app.worker.process_search_ranking_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type=SERVICE_TYPE,
		payload=payload(generation=2, token="b" * 32),
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
		durable_result_ttl_seconds=604800,
	)
	assert decision.outcome == "callback_replay_scheduled"
	assert decision.authoritative_generation == 2
	assert lifecycle.load_result(redis_conn, SERVICE_TYPE, STABLE_ID)["callback_status"] == "pending"
	assert work_calls == ["work"]
