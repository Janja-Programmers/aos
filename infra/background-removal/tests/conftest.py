from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
	def blocked(*_args, **_kwargs):
		raise AssertionError("network access is forbidden in unit tests")

	monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture(autouse=True)
def configured_service_secret(monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_SERVICE_SECRET", "test-background-removal-secret-0123456789")
	from app.config import get_settings
	from app.service import get_service

	get_settings.cache_clear()
	get_service.cache_clear()
	yield
	get_settings.cache_clear()
	get_service.cache_clear()
