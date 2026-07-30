"""
Internal Nominatim client.

Nominatim is an internal infrastructure service. Public API endpoints should
use this client instead of exposing Nominatim directly to Flutter clients.
"""

from __future__ import annotations

from typing import Any

import frappe
import requests

from aos.services.maps.internal_url import (
    InvalidInternalMapsURL,
    build_internal_maps_url,
    normalize_internal_maps_url,
    safe_provider_body,
)

from ..constants import (
    DEFAULT_NOMINATIM_BASE_URL,
    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
    KENYA_VIEWBOX,
    NOMINATIM_BASE_URL_CONFIG_KEY,
    NOMINATIM_REQUEST_TIMEOUT_SECONDS,
    REVERSE_GEOCODE_ADDRESS_DETAILS,
    REVERSE_GEOCODE_DEFAULT_ZOOM,
    SEARCH_ADDRESS_DETAILS,
)


class NominatimClientError(Exception):
    """Raised when the internal Nominatim service cannot satisfy a request."""


class NominatimClient:
    """HTTP client for the internal Nominatim service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
    ):
        configured_url = (
            base_url
            or frappe.conf.get(
                NOMINATIM_BASE_URL_CONFIG_KEY
            )
            or DEFAULT_NOMINATIM_BASE_URL
        )

        self.base_url = _normalize_base_url(
            configured_url
        )

        self._session = requests.Session()

        self._session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "AOS-Maps/1.0",
            }
        )

    def search_places(
        self,
        *,
        query: str,
        limit: int,
        bounded: bool,
        country_codes: str,
        language: str | None = None,
    ) -> list[dict]:
        """
        Search for places using the configured Kenya viewbox.

        Returns raw Nominatim result dictionaries. Public response
        normalization is handled by the maps serializers.
        """

        params: dict[str, Any] = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": (
                1
                if SEARCH_ADDRESS_DETAILS
                else 0
            ),
            "limit": limit,
            "countrycodes": country_codes,
            "viewbox": KENYA_VIEWBOX,
            "bounded": 1 if bounded else 0,
        }

        normalized_language = _normalize_optional_string(
            language
        )

        if normalized_language:
            params["accept-language"] = normalized_language

        response_data = self._get_json(
            endpoint="/search",
            params=params,
            operation="place search",
        )

        if not isinstance(
            response_data,
            list,
        ):
            raise NominatimClientError(
                "Nominatim returned an invalid search response."
            )

        return [
            item
            for item in response_data
            if isinstance(item, dict)
        ]

    def reverse_geocode(
        self,
        *,
        latitude: float,
        longitude: float,
        language: str | None = None,
    ) -> dict:
        """
        Resolve coordinates into an address.

        Returns the raw Nominatim response dictionary. Public response
        normalization is handled by the maps serializers.
        """

        params: dict[str, Any] = {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "addressdetails": (
                1
                if REVERSE_GEOCODE_ADDRESS_DETAILS
                else 0
            ),
            "zoom": REVERSE_GEOCODE_DEFAULT_ZOOM,
        }

        normalized_language = _normalize_optional_string(
            language
        )

        if normalized_language:
            params["accept-language"] = normalized_language

        response_data = self._get_json(
            endpoint="/reverse",
            params=params,
            operation="reverse geocoding",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise NominatimClientError(
                "Nominatim returned an invalid reverse-geocoding response."
            )

        error_message = response_data.get(
            "error"
        )

        if error_message:
            raise NominatimClientError(
                str(error_message)
            )

        return response_data

    def status(self) -> dict:
        """Fetch the internal Nominatim status response."""

        response_data = self._get_json(
            endpoint="/status",
            params={
                "format": "json",
            },
            operation="status check",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise NominatimClientError(
                "Nominatim returned an invalid status response."
            )

        return response_data

    def close(self):
        """Close the underlying HTTP session."""

        self._session.close()

    def _get_json(
        self,
        *,
        endpoint: str,
        params: dict[str, Any],
        operation: str,
    ) -> Any:
        """Perform a GET request and decode its JSON response."""

        url = _build_url(
            base_url=self.base_url,
            endpoint=endpoint,
        )

        try:
            response = self._session.get(
                url,
                params=params,
                allow_redirects=False,
                timeout=(
                    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
                    NOMINATIM_REQUEST_TIMEOUT_SECONDS,
                ),
            )

        except requests.ConnectTimeout as ex:
            raise NominatimClientError(
                "The geocoding service could not be reached."
            ) from ex

        except requests.ReadTimeout as ex:
            raise NominatimClientError(
                "The geocoding service took too long to respond."
            ) from ex

        except requests.ConnectionError as ex:
            raise NominatimClientError(
                "The geocoding service is unavailable."
            ) from ex

        except requests.RequestException as ex:
            raise NominatimClientError(
                "The geocoding request failed."
            ) from ex

        if response.status_code != 200:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            if response.status_code == 404:
                raise NominatimClientError(
                    "No matching location was found."
                )

            if response.status_code == 429:
                raise NominatimClientError(
                    "The geocoding service is temporarily busy."
                )

            if 500 <= response.status_code <= 599:
                raise NominatimClientError(
                    "The geocoding service is temporarily unavailable."
                )

            raise NominatimClientError(
                "The geocoding service rejected the request."
            )

        try:
            return response.json()

        except requests.JSONDecodeError as ex:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            raise NominatimClientError(
                "The geocoding service returned an invalid response."
            ) from ex

    def _log_service_error(
        self,
        *,
        operation: str,
        status_code: int,
        response_body: str,
    ):
        """
        Log internal service failures without exposing the service URL
        to public API consumers.
        """

        safe_body = safe_provider_body(response_body)

        frappe.log_error(
            message=(
                f"Operation: {operation}\n"
                f"Status code: {status_code}\n"
                f"Response body:\n{safe_body}"
            ),
            title="AOS Nominatim Service Error",
        )


def get_nominatim_client() -> NominatimClient:
    """Return a configured Nominatim client instance."""

    return NominatimClient()


def _normalize_base_url(value: Any) -> str:
    try:
        return normalize_internal_maps_url(value, service="Nominatim")
    except InvalidInternalMapsURL as exc:
        raise NominatimClientError(str(exc)) from exc


def _build_url(*, base_url: str, endpoint: str) -> str:
    try:
        return build_internal_maps_url(base_url, endpoint)
    except InvalidInternalMapsURL as exc:
        raise NominatimClientError(str(exc)) from exc


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    return normalized or None
