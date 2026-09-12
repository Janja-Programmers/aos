from __future__ import annotations

import pytest
from app.config import Settings
from app.image_loader import ImageLoadError, _resolve_url, load_image_from_url


def _settings(monkeypatch) -> Settings:
	monkeypatch.setenv("IMAGE_SEARCH_FILE_BASE_URL", "https://api.example.test")
	monkeypatch.setenv("IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS", "cdn.example.test")
	return Settings()


def test_settings_accept_shared_internal_secret_and_build_exact_host_allowlist(monkeypatch):
	monkeypatch.delenv("IMAGE_SEARCH_INTERNAL_SECRET", raising=False)
	monkeypatch.setenv("SHORT_CLASSIFICATION_SECRET", "shared-internal-secret")
	settings = _settings(monkeypatch)

	assert settings.internal_secret == "shared-internal-secret"
	assert settings.allowed_image_hosts == ("api.example.test", "cdn.example.test")


def test_remote_image_fetch_is_limited_to_exact_trusted_hosts(monkeypatch):
	settings = _settings(monkeypatch)

	assert _resolve_url("/files/example.jpg", settings) == "https://api.example.test/files/example.jpg"
	assert _resolve_url("https://cdn.example.test/ad.jpg", settings) == "https://cdn.example.test/ad.jpg"

	for url in (
		"http://169.254.169.254/latest/meta-data/",
		"http://127.0.0.1:8000/private",
		"https://evil.example.test/ad.jpg",
		"https://cdn.example.test@evil.example.test/ad.jpg",
	):
		with pytest.raises(ImageLoadError):
			_resolve_url(url, settings)


def test_remote_image_redirects_are_rejected(monkeypatch):
	settings = _settings(monkeypatch)
	calls = []

	class RedirectResponse:
		status_code = 302

		def __init__(self):
			self.headers = {"location": "http://169.254.169.254/latest/meta-data/"}

		def raise_for_status(self):
			raise AssertionError("Redirects must be rejected before following or parsing")

	def fake_get(url, **kwargs):
		calls.append((url, kwargs))
		return RedirectResponse()

	monkeypatch.setattr("app.image_loader.requests.get", fake_get)
	with pytest.raises(ImageLoadError, match="redirects are not allowed"):
		load_image_from_url("https://cdn.example.test/ad.jpg", settings=settings)

	assert calls
	assert calls[0][1]["allow_redirects"] is False
