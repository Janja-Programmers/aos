"""Private Photon client used behind AOS's provider-neutral Maps API."""

from __future__ import annotations

from typing import Any

import frappe
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from aos.services.maps.internal_url import (
	InvalidInternalMapsURL,
	build_internal_maps_url,
	normalize_internal_maps_url,
)

from ..constants import (
	DEFAULT_PHOTON_BASE_URL,
	MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
	PHOTON_BASE_URL_CONFIG_KEY,
	PHOTON_REQUEST_TIMEOUT_SECONDS,
)


class PhotonClientError(Exception):
	"""Raised when the private Photon service cannot satisfy a request."""


class PhotonClient:
	"""Bounded HTTP client for Photon forward and reverse geocoding."""

	def __init__(self, *, base_url: str | None = None):
		configured_url = base_url or frappe.conf.get(PHOTON_BASE_URL_CONFIG_KEY) or DEFAULT_PHOTON_BASE_URL
		self.base_url = _normalize_base_url(configured_url)
		self._session = requests.Session()
		self._session.headers.update({"Accept": "application/json", "User-Agent": "AOS-Maps/1.0"})
		retry = Retry(
			total=2,
			connect=2,
			read=1,
			status=1,
			backoff_factor=0.2,
			status_forcelist=(502, 503, 504),
			allowed_methods=frozenset({"GET"}),
			raise_on_status=False,
		)
		self._session.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=50))
		self._session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=50))

	def autocomplete_places(
		self,
		*,
		query: str,
		limit: int,
		latitude: float | None = None,
		longitude: float | None = None,
		country_code: str | None = None,
		language: str | None = None,
	) -> list[dict]:
		params: dict[str, Any] = {"q": query, "limit": limit}
		if latitude is not None and longitude is not None:
			params.update({"lat": latitude, "lon": longitude})
		if country_code:
			params["countrycode"] = country_code.lower()
		if language:
			params["lang"] = language
		payload = self._get_json(endpoint="/api", params=params, operation="place search")
		return _features(payload, operation="place search")

	def reverse_geocode(
		self,
		*,
		latitude: float,
		longitude: float,
		language: str | None = None,
	) -> list[dict]:
		params: dict[str, Any] = {"lat": latitude, "lon": longitude, "limit": 1}
		if language:
			params["lang"] = language
		payload = self._get_json(endpoint="/reverse", params=params, operation="reverse geocoding")
		return _features(payload, operation="reverse geocoding")

	def status(self) -> dict:
		payload = self._get_json(endpoint="/status", params={}, operation="status check")
		if not isinstance(payload, dict):
			raise PhotonClientError("Photon returned an invalid status response.")
		return payload

	def close(self) -> None:
		self._session.close()

	def _get_json(self, *, endpoint: str, params: dict[str, Any], operation: str) -> Any:
		url = _build_url(base_url=self.base_url, endpoint=endpoint)
		try:
			response = self._session.get(
				url,
				params=params,
				allow_redirects=False,
				timeout=(MAP_SERVICE_CONNECT_TIMEOUT_SECONDS, PHOTON_REQUEST_TIMEOUT_SECONDS),
			)
		except requests.ConnectTimeout as exc:
			raise PhotonClientError("The geocoding service could not be reached.") from exc
		except requests.ReadTimeout as exc:
			raise PhotonClientError("The geocoding service took too long to respond.") from exc
		except requests.ConnectionError as exc:
			raise PhotonClientError("The geocoding service is unavailable.") from exc
		except requests.RequestException as exc:
			raise PhotonClientError("The geocoding request failed.") from exc

		if response.status_code != 200:
			self._log_service_error(operation=operation, status_code=response.status_code, response_body=response.text)
			if response.status_code == 404:
				return {"features": []}
			if response.status_code == 429:
				raise PhotonClientError("The geocoding service is temporarily busy.")
			if 500 <= response.status_code <= 599:
				raise PhotonClientError("The geocoding service is temporarily unavailable.")
			raise PhotonClientError("The geocoding service rejected the request.")
		try:
			return response.json()
		except requests.JSONDecodeError as exc:
			self._log_service_error(operation=operation, status_code=response.status_code, response_body=response.text)
			raise PhotonClientError("The geocoding service returned an invalid response.") from exc

	@staticmethod
	def _log_service_error(*, operation: str, status_code: int, response_body: str) -> None:
		# Provider payloads may contain addresses/coordinates; never log them.
		frappe.log_error(
			message=f"Operation: {operation}\nStatus code: {status_code}",
			title="AOS Photon Service Error",
		)


def get_photon_client() -> PhotonClient:
	return PhotonClient()


def _features(payload: Any, *, operation: str) -> list[dict]:
	if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
		raise PhotonClientError(f"Photon returned an invalid {operation} response.")
	return [item for item in payload["features"] if isinstance(item, dict)]


def _normalize_base_url(value: Any) -> str:
	try:
		return normalize_internal_maps_url(value, service="Photon")
	except InvalidInternalMapsURL as exc:
		raise PhotonClientError(str(exc)) from exc


def _build_url(*, base_url: str, endpoint: str) -> str:
	try:
		return build_internal_maps_url(base_url, endpoint)
	except InvalidInternalMapsURL as exc:
		raise PhotonClientError(str(exc)) from exc
