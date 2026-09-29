"""Minimal infrastructure health probes for load balancers and orchestrators.

These are deliberately not AOS client APIs. They return no dependency detail,
configuration, exception text, hostnames, URLs, credentials, or user data.
Detailed operational diagnostics remain bench/operator-only utilities.
"""

from __future__ import annotations

import frappe

from aos.utils.operational_health import validate_liveness, validate_readiness


def _status_code(value: int) -> None:
	try:
		frappe.local.response["http_status_code"] = int(value)
	except Exception:
		pass


@frappe.whitelist(allow_guest=True, methods=["GET"])
def liveness() -> dict[str, object]:
	"""Cheap process liveness. Never probes dependencies or shared state."""

	report = validate_liveness()
	_status_code(200)
	return {"ok": bool(report.get("alive")), "status": str(report.get("status") or "healthy")}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def readiness() -> dict[str, object]:
	"""Cheap traffic readiness using only MariaDB and Frappe Redis roles."""

	try:
		report = validate_readiness()
		ready = bool(report.get("ready"))
		status = str(report.get("status") or ("healthy" if ready else "unhealthy"))
	except Exception:
		ready = False
		status = "unhealthy"
	_status_code(200 if ready else 503)
	return {"ok": ready, "status": status}
