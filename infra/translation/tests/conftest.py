from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setenv("TRANSLATION_INTERNAL_TOKEN", "unit-test-translation-token")
	def blocked(*_args, **_kwargs):
		raise AssertionError("network access is forbidden in unit tests")

	monkeypatch.setattr(socket.socket, "connect", blocked)
