from __future__ import annotations

import time

import frappe

from aos.services.transactional_outbox import authorize_terminal_work_replay as _authorize_work_replay
from aos.services.transactional_outbox import dispatch_claimed_outbox as _dispatch
from aos.services.transactional_outbox import publish_outbox_records
from aos.services.transactional_outbox import requeue_dead_letter_outbox as _requeue_dead_letter


def publish_transactional_outbox(limit: int = 100):
	"""Publish durable outbox work with a tiny bounded deadlock retry.

	MariaDB deadlocks roll back the current transaction. The publisher is fully
	idempotent and recurring, so retrying after an explicit rollback is safer
	than surfacing a transient 1213 as a failed scheduled job.
	"""

	for attempt in range(3):
		try:
			return publish_outbox_records(limit=limit)
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt >= 2:
				raise
			time.sleep(0.05 * (attempt + 1))
	raise RuntimeError("Transactional outbox publisher retry loop exited unexpectedly.")


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
