from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

from PIL import Image

from app import vision_detector


class _ObjectResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, size: int) -> bytes:
        return self._data[:size]

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _Minio:
    def __init__(self, data: bytes):
        self.data = data

    def get_object(self, _bucket: str, _object_key: str):
        return _ObjectResponse(self.data)


def _settings():
    return SimpleNamespace(
        vision_url="http://image-search:8000/internal/moderation/classify-images",
        vision_secret="secret",
        vision_allowed_hosts=("image-search",),
        environment="test",
        max_media_bytes=10 * 1024 * 1024,
        max_images=8,
        vision_max_image_bytes=786432,
        vision_max_total_bytes=4194304,
        vision_max_dimension=1024,
        vision_max_pixels=25000000,
        vision_timeout_seconds=10,
    )


def _large_source_image() -> bytes:
    # BMP is intentionally large on the wire despite simple visual content. This
    # reproduces a valid Media image that would exceed the provider's 1 MiB limit
    # if the original bytes were base64-forwarded unchanged.
    image = Image.new("RGB", (1600, 1600), (100, 120, 140))
    output = io.BytesIO()
    image.save(output, format="BMP")
    return output.getvalue()


def test_prepare_inference_image_bounds_large_valid_source():
    source = _large_source_image()
    assert len(source) > 1024 * 1024

    prepared = vision_detector._prepare_inference_image(
        source,
        max_bytes=512 * 1024,
        max_dimension=1024,
        max_pixels=25_000_000,
    )

    assert 0 < len(prepared) <= 512 * 1024
    with Image.open(io.BytesIO(prepared)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1024
        assert image.mode == "RGB"


def test_classify_images_sends_bounded_derivative_not_original(monkeypatch):
    source = _large_source_image()
    captured: dict[str, object] = {}

    class _HTTPResponse:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ready",
                "signals": [],
                "safe_confidence": 0.95,
                "top_category": "safe",
                "top_confidence": 0.95,
                "margin": 0.80,
                "model": "ViT-B-32",
                "model_version": "openclip-v1",
            }

    def _post(_url, *, data, headers, timeout):
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _HTTPResponse()

    monkeypatch.setattr(vision_detector.requests, "post", _post)
    signals, versions, review_reasons = vision_detector.classify_images(
        client=_Minio(source),
        items=[{"content_type": "image/bmp", "bucket": "media", "object_key": "large.bmp"}],
        settings=_settings(),
    )

    payload = json.loads(captured["data"])
    prepared = base64.b64decode(payload["images"][0], validate=True)
    assert len(source) > 1024 * 1024
    assert len(prepared) <= _settings().vision_max_image_bytes
    assert len(prepared) < len(source)
    assert signals == []
    assert versions == {"vision": "ViT-B-32:openclip-v1"}
    assert review_reasons == []


def test_prepare_inference_image_rejects_extreme_pixel_dimensions():
    source = _large_source_image()
    try:
        vision_detector._prepare_inference_image(
            source,
            max_bytes=512 * 1024,
            max_dimension=1024,
            max_pixels=1_000_000,
        )
    except vision_detector.VisionDetectorError as exc:
        assert "dimensions" in str(exc)
    else:
        raise AssertionError("expected oversized dimensions to fail closed")


def test_low_safe_softmax_does_not_create_fake_other_signal(monkeypatch):
    source = _large_source_image()

    class _HTTPResponse:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ready",
                "signals": [],
                "safe_confidence": 0.31,
                "top_category": "safe",
                "top_confidence": 0.31,
                "margin": 0.04,
                "model": "ViT-B-32",
                "model_version": "openclip-v1",
            }

    monkeypatch.setattr(vision_detector.requests, "post", lambda *_args, **_kwargs: _HTTPResponse())
    signals, _versions, review_reasons = vision_detector.classify_images(
        client=_Minio(source),
        items=[{"content_type": "image/bmp", "bucket": "media", "object_key": "ordinary.bmp"}],
        settings=_settings(),
    )

    assert signals == []
    assert review_reasons == []


def test_unsafe_leading_class_requests_review_without_fake_confidence(monkeypatch):
    source = _large_source_image()

    class _HTTPResponse:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ready",
                "signals": [],
                "safe_confidence": 0.18,
                "top_category": "weapons",
                "top_confidence": 0.19,
                "margin": 0.01,
                "model": "ViT-B-32",
                "model_version": "openclip-v1",
            }

    monkeypatch.setattr(vision_detector.requests, "post", lambda *_args, **_kwargs: _HTTPResponse())
    signals, _versions, review_reasons = vision_detector.classify_images(
        client=_Minio(source),
        items=[{"content_type": "image/bmp", "bucket": "media", "object_key": "ambiguous.bmp"}],
        settings=_settings(),
    )

    assert signals[0]["category"] == "weapons"
    assert signals[0]["confidence"] == 0.19
    assert review_reasons == ["vision uncertainty: weapons outranked safe"]
