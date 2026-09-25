from __future__ import annotations

import base64
import hashlib
import io
import hmac
import json
import time
from typing import Any
from urllib.parse import urlparse

import requests
from minio import Minio
from PIL import Image, ImageOps, UnidentifiedImageError


class VisionDetectorError(RuntimeError):
    pass


def _signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _validated_url(url: str, *, allowed_hosts: tuple[str, ...], environment: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise VisionDetectorError("Invalid vision provider URL")
    if environment.lower() in {"production", "staging"} and allowed_hosts and parsed.hostname.lower() not in allowed_hosts:
        raise VisionDetectorError("Vision provider host is not allowed")
    return parsed.geturl()


def _load_image_bytes(client: Minio, item: dict[str, Any], *, max_bytes: int) -> bytes:
    bucket = str(item.get("bucket") or "").strip().strip("/")
    object_key = str(item.get("object_key") or "").strip().strip("/")
    if not bucket or not object_key:
        raise VisionDetectorError("Media storage reference is missing")
    response = None
    try:
        response = client.get_object(bucket, object_key)
        data = response.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            raise VisionDetectorError("Image exceeds moderation source limit")
        return data
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _prepare_inference_image(
    source: bytes,
    *,
    max_bytes: int,
    max_dimension: int,
    max_pixels: int,
) -> bytes:
    """Create a bounded, metadata-free derivative used only for inference transport."""
    try:
        with Image.open(io.BytesIO(source)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise VisionDetectorError("Image dimensions exceed moderation inference limit")
            if getattr(opened, "is_animated", False):
                opened.seek(0)
            image = ImageOps.exif_transpose(opened)
            image = _flatten_for_jpeg(image)
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            image.load()
    except VisionDetectorError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisionDetectorError("Invalid image for moderation inference") from exc

    # Quality reduction comes first; if an unusually complex image still exceeds
    # the transport budget, progressively reduce dimensions. OpenCLIP resizes to
    # its own model input, so retaining multi-megapixel originals here is wasteful.
    current = image
    for _dimension_pass in range(5):
        for quality in (88, 80, 72, 64, 56, 48, 40):
            output = io.BytesIO()
            current.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            encoded = output.getvalue()
            if encoded and len(encoded) <= max_bytes:
                return encoded
        new_width = max(224, int(current.width * 0.80))
        new_height = max(224, int(current.height * 0.80))
        if (new_width, new_height) == current.size:
            break
        current = current.resize((new_width, new_height), Image.Resampling.LANCZOS)

    raise VisionDetectorError("Image cannot be bounded for moderation inference")


def classify_images(
    *,
    client: Minio,
    items: list[dict[str, Any]],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    images = [item for item in items if str(item.get("content_type") or "").lower().startswith("image/")]
    if not images:
        return [], {}, []
    if not str(settings.vision_url or "").strip() or not str(settings.vision_secret or "").strip():
        raise VisionDetectorError("Vision moderation provider is not configured")

    url = _validated_url(
        settings.vision_url,
        allowed_hosts=tuple(settings.vision_allowed_hosts or ()),
        environment=settings.environment,
    )
    selected = images[: settings.max_images]
    per_image_budget = min(
        int(settings.vision_max_image_bytes),
        max(65536, int(settings.vision_max_total_bytes) // max(1, len(selected))),
    )
    encoded: list[str] = []
    total_prepared_bytes = 0
    for item in selected:
        source = _load_image_bytes(client, item, max_bytes=settings.max_media_bytes)
        prepared = _prepare_inference_image(
            source,
            max_bytes=per_image_budget,
            max_dimension=int(settings.vision_max_dimension),
            max_pixels=int(settings.vision_max_pixels),
        )
        total_prepared_bytes += len(prepared)
        if total_prepared_bytes > int(settings.vision_max_total_bytes):
            raise VisionDetectorError("Images exceed moderation inference transport limit")
        encoded.append(base64.b64encode(prepared).decode("ascii"))
    body = json.dumps({"images": encoded}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = timestamp.encode("ascii") + b"." + body
    response = requests.post(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AOS-Timestamp": timestamp,
            "X-AOS-Signature": _signature(settings.vision_secret, signed),
        },
        timeout=settings.vision_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json() if response.content else {}
    if str(data.get("status") or "") != "ready" or not isinstance(data.get("signals"), list):
        raise VisionDetectorError("Malformed vision moderation response")

    model = str(data.get("model") or "unknown")[:140]
    version = str(data.get("model_version") or "unknown")[:140]
    try:
        safe_confidence = max(0.0, min(float(data.get("safe_confidence") or 0.0), 1.0))
        top_confidence = max(0.0, min(float(data.get("top_confidence") or 0.0), 1.0))
        margin = max(0.0, min(float(data.get("margin") or 0.0), 1.0))
    except (TypeError, ValueError):
        raise VisionDetectorError("Malformed vision moderation confidence")
    top_category = str(data.get("top_category") or "").strip().lower()
    if not top_category:
        raise VisionDetectorError("Malformed vision moderation top category")
    signals: list[dict[str, Any]] = []
    for row in data["signals"][:32]:
        if not isinstance(row, dict):
            continue
        try:
            confidence = max(0.0, min(float(row.get("confidence") or 0), 1.0))
        except (TypeError, ValueError):
            continue
        signals.append(
            {
                "category": str(row.get("category") or "other")[:80],
                "severity": str(row.get("severity") or "medium")[:20],
                "confidence": confidence,
                "source": "image",
                "detector": model,
                "detector_version": version,
            }
        )
    uncertainty_evidence: list[dict[str, Any]] = []
    # The provider reports relative ranking evidence only. It never decides that
    # a near-tie must be reviewed. The canonical AOS policy owns the minimum
    # confidence/margin required for model uncertainty to become review-worthy.
    if top_category != "safe" and top_confidence >= safe_confidence:
        # Preserve the actual leading unsafe evidence for audit even when it is
        # below policy thresholds. This is not a synthetic score and does not by
        # itself force review.
        if not any(str(signal.get("category") or "").lower() == top_category for signal in signals):
            signals.append(
                {
                    "category": top_category,
                    "severity": "medium",
                    "confidence": top_confidence,
                    "source": "image",
                    "detector": model,
                    "detector_version": version,
                    "reason": "vision_top_category",
                    "margin": margin,
                }
            )
        uncertainty_evidence.append(
            {
                "top_category": top_category,
                "top_confidence": top_confidence,
                "safe_confidence": safe_confidence,
                "margin": margin,
                "source": "image",
                "detector": model,
                "detector_version": version,
            }
        )
    return signals, {"vision": f"{model}:{version}"}, uncertainty_evidence
