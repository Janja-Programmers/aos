"""
Internal Photon client.

Photon is an internal infrastructure service used for fast place
search-as-you-type. Public API endpoints should use this client instead of
exposing Photon directly to Flutter clients.
"""

from __future__ import annotations

from typing import Any

import frappe
import requests

from ..constants import (
    DEFAULT_PHOTON_BASE_URL,
    KENYA_BBOX_EAST,
    KENYA_BBOX_NORTH,
    KENYA_BBOX_SOUTH,
    KENYA_BBOX_WEST,
    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
    PHOTON_BASE_URL_CONFIG_KEY,
    PHOTON_REQUEST_TIMEOUT_SECONDS,
    SUPPORTED_COUNTRY_CODE_LOWER,
)


class PhotonClientError(Exception):
    """Raised when the internal Photon service cannot satisfy a request."""


class PhotonClient:
    """HTTP client for the internal Photon service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
    ):
        configured_url = (
            base_url
            or frappe.conf.get(
                PHOTON_BASE_URL_CONFIG_KEY
            )
            or DEFAULT_PHOTON_BASE_URL
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

    def autocomplete_places(
        self,
        *,
        query: str,
        limit: int,
        latitude: float | None = None,
        longitude: float | None = None,
        country_codes: str | None = None,
        language: str | None = None,
    ) -> list[dict]:
        """
        Search places using Photon.

        Returns raw GeoJSON feature dictionaries. Public response
        normalization is handled by the maps serializers.
        """

        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "countrycode": _first_country_code(
                country_codes
            ) or SUPPORTED_COUNTRY_CODE_LOWER,
            "bbox": (
                f"{KENYA_BBOX_WEST},"
                f"{KENYA_BBOX_SOUTH},"
                f"{KENYA_BBOX_EAST},"
                f"{KENYA_BBOX_NORTH}"
            ),
        }

        if latitude is not None and longitude is not None:
            params["lat"] = latitude
            params["lon"] = longitude

        normalized_language = _normalize_optional_string(
            language
        )

        if normalized_language:
            params["lang"] = normalized_language

        response_data = self._get_json(
            endpoint="/api",
            params=params,
            operation="place autocomplete",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise PhotonClientError(
                "Photon returned an invalid autocomplete response."
            )

        features = response_data.get(
            "features"
        )

        if not isinstance(
            features,
            list,
        ):
            raise PhotonClientError(
                "Photon returned an invalid feature list."
            )

        return [
            feature
            for feature in features
            if isinstance(feature, dict)
        ]

    def status(self) -> dict:
        """Fetch the internal Photon status response."""

        response_data = self._get_json(
            endpoint="/status",
            params={},
            operation="status check",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise PhotonClientError(
                "Photon returned an invalid status response."
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
                timeout=(
                    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
                    PHOTON_REQUEST_TIMEOUT_SECONDS,
                ),
            )

        except requests.ConnectTimeout as ex:
            raise PhotonClientError(
                "The autocomplete service could not be reached."
            ) from ex

        except requests.ReadTimeout as ex:
            raise PhotonClientError(
                "The autocomplete service took too long to respond."
            ) from ex

        except requests.ConnectionError as ex:
            raise PhotonClientError(
                "The autocomplete service is unavailable."
            ) from ex

        except requests.RequestException as ex:
            raise PhotonClientError(
                "The autocomplete request failed."
            ) from ex

        if response.status_code != 200:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            if response.status_code == 404:
                raise PhotonClientError(
                    "No matching location was found."
                )

            if response.status_code == 429:
                raise PhotonClientError(
                    "The autocomplete service is temporarily busy."
                )

            if 500 <= response.status_code <= 599:
                raise PhotonClientError(
                    "The autocomplete service is temporarily unavailable."
                )

            raise PhotonClientError(
                "The autocomplete service rejected the request."
            )

        try:
            return response.json()

        except requests.JSONDecodeError as ex:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            raise PhotonClientError(
                "The autocomplete service returned an invalid response."
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

        safe_body = (
            response_body or ""
        )[:2000]

        frappe.log_error(
            message=(
                f"Operation: {operation}\n"
                f"Status code: {status_code}\n"
                f"Response body:\n{safe_body}"
            ),
            title="AOS Photon Service Error",
        )


def get_photon_client() -> PhotonClient:
    """Return a configured Photon client instance."""

    return PhotonClient()


def _normalize_base_url(
    value: Any,
) -> str:
    """Normalize and validate the configured internal base URL."""

    normalized = str(
        value or ""
    ).strip().rstrip("/")

    if not normalized:
        raise PhotonClientError(
            "Photon service URL is not configured."
        )

    if not (
        normalized.startswith("http://")
        or normalized.startswith("https://")
    ):
        raise PhotonClientError(
            "Photon service URL must use HTTP or HTTPS."
        )

    return normalized


def _build_url(
    *,
    base_url: str,
    endpoint: str,
) -> str:
    """Build an internal Photon endpoint URL."""

    normalized_endpoint = (
        endpoint
        if endpoint.startswith("/")
        else f"/{endpoint}"
    )

    return (
        f"{base_url}"
        f"{normalized_endpoint}"
    )


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


def _first_country_code(
    value: str | None,
) -> str | None:
    """Return the first normalized country code from a comma list."""

    normalized = _normalize_optional_string(
        value
    )

    if not normalized:
        return None

    first = normalized.split(",", 1)[0].strip().lower()

    return first or None
