#!/usr/bin/env python3
"""Publish a versioned PMTiles artifact atomically to S3-compatible object storage."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from io import BytesIO

from minio import Minio
from minio.error import S3Error


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    version = env("MAP_DATA_VERSION")
    filename = env("BASEMAP_PMTILES_FILENAME")
    source = root / "maps" / "basemap" / version / filename
    if not source.is_file() or source.stat().st_size <= 0:
        raise SystemExit(f"Missing basemap artifact: {source}")
    endpoint = env("MAPS_OBJECT_STORAGE_ENDPOINT").removeprefix("https://").removeprefix("http://")
    secure = os.environ["MAPS_OBJECT_STORAGE_ENDPOINT"].startswith("https://")
    bucket = env("MAPS_OBJECT_STORAGE_BUCKET")
    client = Minio(
        endpoint,
        access_key=env("MAPS_OBJECT_STORAGE_ACCESS_KEY"),
        secret_key=env("MAPS_OBJECT_STORAGE_SECRET_KEY"),
        secure=secure,
        region=os.environ.get("MAPS_OBJECT_STORAGE_REGION") or None,
    )
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket, location=os.environ.get("MAPS_OBJECT_STORAGE_REGION") or None)
    allow_anonymous_read = os.environ.get("MAPS_OBJECT_STORAGE_ALLOW_ANONYMOUS_READ", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if allow_anonymous_read:
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/basemap/*"],
            }],
        }
        client.set_bucket_policy(bucket, json.dumps(policy))
    hasher = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    object_name = f"basemap/{version}/{filename}"
    client.fput_object(
        bucket,
        object_name,
        str(source),
        content_type="application/vnd.pmtiles",
        metadata={"Cache-Control": "public,max-age=31536000,immutable", "sha256": digest},
    )
    pointer = json.dumps({"version": version, "object": object_name, "sha256": digest}, sort_keys=True).encode()
    client.put_object(
        bucket,
        "basemap/current.json",
        BytesIO(pointer),
        len(pointer),
        content_type="application/json",
        metadata={"Cache-Control": "public,max-age=60,must-revalidate"},
    )
    print(json.dumps({"bucket": bucket, "object": object_name, "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except S3Error as exc:
        raise SystemExit(f"Object storage publication failed: {exc.code}") from exc
