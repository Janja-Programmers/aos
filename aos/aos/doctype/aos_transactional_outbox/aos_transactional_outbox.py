from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AOSTransactionalOutbox(Document):
	def before_insert(self):
		if not self.status:
			self.status = "Queued"
		if not self.created_at:
			self.created_at = now_datetime()
		self.attempt_count = int(self.attempt_count or 0)
		self.max_attempts = max(1, int(self.max_attempts or 5))

	def validate(self):
		if self.job_doctype and self.job_name and not frappe.db.exists(self.job_doctype, self.job_name):
			frappe.throw("Outbox job target does not exist.")
		if not str(self.idempotency_key or "").strip():
			frappe.throw("Outbox idempotency key is required.")
		if not str(self.dispatch_method or "").strip():
			frappe.throw("Outbox dispatch method is required.")
