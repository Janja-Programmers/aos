#!/usr/bin/env python3
"""Provision basemap read policy or publish a versioned PMTiles artifact.

The two operations intentionally use separate credentials:
- --policy-only requires a privileged policy-provisioning identity.
- normal publication uses the restricted Maps publisher identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from io import BytesIO
from pathlib import Path

from minio import Minio
from minio.error import S3Error


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _client(*, access_key: str, secret_key: str) -> Minio:
    raw_endpoint = env("MAPS_OBJECT_STORAGE_ENDPOINT")
    endpoint = raw_endpoint.removeprefix("https://").removeprefix("http://")
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=raw_endpoint.startswith("https://"),
        region=os.environ.get("MAPS_OBJECT_STORAGE_REGION") or None,
    )


def _public_basemap_policy(bucket: str) -> str:
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/basemap/*"],
        }],
    }
    return json.dumps(policy)


def _provision_policy(bucket: str) -> int:
    # Policy mutation is deliberately separated from the ordinary publisher.
    # Supply a narrowly controlled operator/admin identity only for this
    # explicit provisioning operation. Do not grant PutBucketPolicy to the
    # long-lived Maps publisher merely to make publication convenient.
    client = _client(
        access_key=env("MAPS_OBJECT_STORAGE_POLICY_ACCESS_KEY"),
        secret_key=env("MAPS_OBJECT_STORAGE_POLICY_SECRET_KEY"),
    )
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket, location=os.environ.get("MAPS_OBJECT_STORAGE_REGION") or None)
    client.set_bucket_policy(bucket, _public_basemap_policy(bucket))
    print(json.dumps({"bucket": bucket, "policy": "basemap-read-only"}, sort_keys=True))
    return 0


def _publish(root: Path, bucket: str) -> int:
    version = env("MAP_DATA_VERSION")
    filename = env("BASEMAP_PMTILES_FILENAME")
    source = root / "maps" / "basemap" / version / filename
    if not source.is_file() or source.stat().st_size <= 0:
        raise SystemExit(f"Missing basemap artifact: {source}")

    client = _client(
        access_key=env("MAPS_OBJECT_STORAGE_ACCESS_KEY"),
        secret_key=env("MAPS_OBJECT_STORAGE_SECRET_KEY"),
    )
    if not client.bucket_exists(bucket):
        raise SystemExit(
            f"Basemap bucket {bucket!r} is unavailable to the publisher. "
            "Provision it first with publish-basemap.py --policy-only."
        )

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
    pointer = json.dumps(
        {"version": version, "object": object_name, "sha256": digest},
        sort_keys=True,
    ).encode()
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


def main() -> int:
    args = sys.argv[1:]
    policy_only = args == ["--policy-only"]
    if args and not policy_only:
        raise SystemExit("Usage: publish-basemap.py [--policy-only]")

    root = Path(__file__).resolve().parents[3]
    bucket = env("MAPS_OBJECT_STORAGE_BUCKET")
    if policy_only:
        return _provision_policy(bucket)
    return _publish(root, bucket)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except S3Error as exc:
        raise SystemExit(f"Object storage publication failed: {exc.code}") from exc
