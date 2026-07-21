from __future__ import annotations

from aos.services.transactional_outbox import authorize_terminal_work_replay as _authorize_work_replay
from aos.services.transactional_outbox import dispatch_claimed_outbox as _dispatch
from aos.services.transactional_outbox import publish_outbox_records
from aos.services.transactional_outbox import requeue_dead_letter_outbox as _requeue_dead_letter


def publish_transactional_outbox(limit: int = 100):
	return publish_outbox_records(limit=limit)


def dispatch_claimed_outbox(outbox_name: str, claim_token: str):
	return _dispatch(outbox_name, claim_token)


def requeue_dead_letter_outbox(
	outbox_name: str,
	expected_idempotency_key: str,
	additional_attempts: int = 3,
):
	return _requeue_dead_letter(
		outbox_name=outbox_name,
		expected_idempotency_key=expected_idempotency_key,
		additional_attempts=additional_attempts,
	)


def authorize_terminal_work_replay(
	outbox_name: str,
	expected_idempotency_key: str,
	additional_attempts: int = 3,
):
	return _authorize_work_replay(
		outbox_name=outbox_name,
		expected_idempotency_key=expected_idempotency_key,
		additional_attempts=additional_attempts,
	)
