from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import fakeredis
import pytest
from rq import Queue


@pytest.fixture
def redis_conn():
	return fakeredis.FakeRedis(decode_responses=False)


@pytest.fixture
def rq_queue(redis_conn):
	return Queue("test", connection=redis_conn)


@pytest.fixture(autouse=True)
def isolated_companion_runtime(monkeypatch: pytest.MonkeyPatch, redis_conn, rq_queue):
	original_connect = socket.socket.connect
	test_redis_url = os.getenv("AOS_TEST_REDIS_URL", "").strip()
	parsed = urlparse(test_redis_url) if test_redis_url else None
	allowed_host = parsed.hostname if parsed else None
	allowed_port = parsed.port if parsed else None

	def guarded(sock, address):
		host, port = address[0], int(address[1])
		if allowed_host and port == allowed_port and host in {allowed_host, "127.0.0.1", "::1", "localhost"}:
			return original_connect(sock, address)
		raise AssertionError("network access is forbidden in unit tests")

	monkeypatch.setattr(socket.socket, "connect", guarded)

	from app import idempotent_dispatch, main, worker

	monkeypatch.setattr(idempotent_dispatch, "get_redis", lambda: redis_conn)
	monkeypatch.setattr(main, "get_redis", lambda: redis_conn)
	monkeypatch.setattr(main, "get_queue", lambda: rq_queue)
	monkeypatch.setattr(worker, "get_redis", lambda: redis_conn)
	monkeypatch.setattr(worker, "get_queue", lambda: rq_queue)
