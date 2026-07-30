"""
Internal Valhalla client.

Valhalla is an internal routing service. Public API endpoints should use this
client instead of exposing Valhalla directly to Flutter clients.
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
    DEFAULT_VALHALLA_BASE_URL,
    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
    ROUTE_SHAPE_FORMAT,
    VALHALLA_BASE_URL_CONFIG_KEY,
    VALHALLA_REQUEST_TIMEOUT_SECONDS,
)


class ValhallaClientError(Exception):
    """Raised when the internal Valhalla service cannot satisfy a request."""


class ValhallaClient:
    """HTTP client for the internal Valhalla routing service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
    ):
        configured_url = (
            base_url
            or frappe.conf.get(
                VALHALLA_BASE_URL_CONFIG_KEY
            )
            or DEFAULT_VALHALLA_BASE_URL
        )

        self.base_url = _normalize_base_url(
            configured_url
        )

        self._session = requests.Session()

        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "AOS-Maps/1.0",
            }
        )

    def get_route(
        self,
        *,
        locations: list[dict],
        costing: str,
        units: str,
        language: str,
    ) -> dict:
        """
        Calculate a route between two or more locations.

        Returns the raw Valhalla response dictionary. Public response
        normalization is handled by the maps serializers.
        """

        payload = {
            "locations": [
                {
                    "lat": location["latitude"],
                    "lon": location["longitude"],
                    "type": (
                        "break"
                        if index in {
                            0,
                            len(locations) - 1,
                        }
                        else "through"
                    ),
                }
                for index, location in enumerate(
                    locations
                )
            ],
            "costing": costing,
            "units": units,
            "language": language,
            "directions_options": {
                "units": units,
                "language": language,
            },
            "shape_format": ROUTE_SHAPE_FORMAT,
        }

        response_data = self._post_json(
            endpoint="/route",
            payload=payload,
            operation="route calculation",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise ValhallaClientError(
                "Valhalla returned an invalid route response."
            )

        error_message = response_data.get(
            "error"
        )

        if error_message:
            raise ValhallaClientError(
                str(error_message)
            )

        trip = response_data.get(
            "trip"
        )

        if not isinstance(
            trip,
            dict,
        ):
            raise ValhallaClientError(
                "Valhalla did not return route data."
            )

        legs = trip.get(
            "legs"
        )

        if not isinstance(
            legs,
            list,
        ) or not legs:
            raise ValhallaClientError(
                "Valhalla did not return any route legs."
            )

        return response_data

    def status(self) -> dict:
        """Fetch the internal Valhalla status response."""

        response_data = self._get_json(
            endpoint="/status",
            operation="status check",
        )

        if not isinstance(
            response_data,
            dict,
        ):
            raise ValhallaClientError(
                "Valhalla returned an invalid status response."
            )

        return response_data

    def close(self):
        """Close the underlying HTTP session."""

        self._session.close()

    def _post_json(
        self,
        *,
        endpoint: str,
        payload: dict[str, Any],
        operation: str,
    ) -> Any:
        """Perform a POST request and decode its JSON response."""

        url = _build_url(
            base_url=self.base_url,
            endpoint=endpoint,
        )

        try:
            response = self._session.post(
                url,
                json=payload,
                allow_redirects=False,
                timeout=(
                    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
                    VALHALLA_REQUEST_TIMEOUT_SECONDS,
                ),
            )

        except requests.ConnectTimeout as ex:
            raise ValhallaClientError(
                "The routing service could not be reached."
            ) from ex

        except requests.ReadTimeout as ex:
            raise ValhallaClientError(
                "The routing service took too long to respond."
            ) from ex

        except requests.ConnectionError as ex:
            raise ValhallaClientError(
                "The routing service is unavailable."
            ) from ex

        except requests.RequestException as ex:
            raise ValhallaClientError(
                "The routing request failed."
            ) from ex

        return self._decode_response(
            response=response,
            operation=operation,
        )

    def _get_json(
        self,
        *,
        endpoint: str,
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
                allow_redirects=False,
                timeout=(
                    MAP_SERVICE_CONNECT_TIMEOUT_SECONDS,
                    VALHALLA_REQUEST_TIMEOUT_SECONDS,
                ),
            )

        except requests.ConnectTimeout as ex:
            raise ValhallaClientError(
                "The routing service could not be reached."
            ) from ex

        except requests.ReadTimeout as ex:
            raise ValhallaClientError(
                "The routing service took too long to respond."
            ) from ex

        except requests.ConnectionError as ex:
            raise ValhallaClientError(
                "The routing service is unavailable."
            ) from ex

        except requests.RequestException as ex:
            raise ValhallaClientError(
                "The routing request failed."
            ) from ex

        return self._decode_response(
            response=response,
            operation=operation,
        )

    def _decode_response(
        self,
        *,
        response: requests.Response,
        operation: str,
    ) -> Any:
        """Validate an HTTP response and decode its JSON body."""

        if response.status_code != 200:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            message = _extract_valhalla_error_message(
                response
            )

            if response.status_code == 400:
                raise ValhallaClientError(
                    message
                    or "The routing request is invalid."
                )

            if response.status_code == 404:
                raise ValhallaClientError(
                    message
                    or "No route could be found."
                )

            if response.status_code == 429:
                raise ValhallaClientError(
                    "The routing service is temporarily busy."
                )

            if 500 <= response.status_code <= 599:
                raise ValhallaClientError(
                    "The routing service is temporarily unavailable."
                )

            raise ValhallaClientError(
                message
                or "The routing service rejected the request."
            )

        try:
            return response.json()

        except requests.JSONDecodeError as ex:
            self._log_service_error(
                operation=operation,
                status_code=response.status_code,
                response_body=response.text,
            )

            raise ValhallaClientError(
                "The routing service returned an invalid response."
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
            title="AOS Valhalla Service Error",
        )


def get_valhalla_client() -> ValhallaClient:
    """Return a configured Valhalla client instance."""

    return ValhallaClient()


def _extract_valhalla_error_message(
    response: requests.Response,
) -> str | None:
    """Extract a safe error message from a Valhalla response."""

    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return None

    if not isinstance(
        payload,
        dict,
    ):
        return None

    error = payload.get(
        "error"
    )

    if error:
        return str(error)

    status = payload.get(
        "status"
    )

    if status:
        return str(status)

    return None


def _normalize_base_url(value: Any) -> str:
    try:
        return normalize_internal_maps_url(value, service="Valhalla")
    except InvalidInternalMapsURL as exc:
        raise ValhallaClientError(str(exc)) from exc


def _build_url(*, base_url: str, endpoint: str) -> str:
    try:
        return build_internal_maps_url(base_url, endpoint)
    except InvalidInternalMapsURL as exc:
        raise ValhallaClientError(str(exc)) from exc
