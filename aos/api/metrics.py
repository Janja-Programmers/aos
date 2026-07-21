"""Private Prometheus/OpenMetrics endpoints."""

from __future__ import annotations

import frappe

from aos.utils.metrics import (
	metrics_access_allowed,
	render_background_metrics,
	render_backup_metrics,
	render_metrics,
)


def _serve(renderer):
	if not metrics_access_allowed():
		frappe.local.response["http_status_code"] = 403
		return {"ok": False, "error": "PERMISSION_DENIED"}
	payload = renderer().encode("utf-8")
	frappe.local.response.update(
		{
			"type": "binary",
			"filename": "metrics",
			"filecontent": payload,
			"content_type": "application/openmetrics-text; version=1.0.0; charset=utf-8",
		}
	)
	return None


@frappe.whitelist(allow_guest=True)
def prometheus():
	return _serve(render_metrics)


@frappe.whitelist(allow_guest=True)
def background_jobs():
	return _serve(render_background_metrics)


@frappe.whitelist(allow_guest=True)
def backup_readiness():
	return _serve(render_backup_metrics)
