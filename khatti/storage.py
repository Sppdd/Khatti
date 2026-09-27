"""Image storage. S3 API only (Nebius Object Storage, MinIO on-prem, ...), or a local dir for dev."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from .config import StorageConfig


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"invalid object key '{key}'")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)


class S3ObjectStore:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str):
        import boto3  # imported lazily: only needed when S3 is configured

        self.bucket = bucket
        # Credentials come from the standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY variables.
        self._s3 = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        resp = await asyncio.to_thread(self._s3.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(resp["Body"].read)


def object_store_from_config(cfg: StorageConfig) -> ObjectStore:
    if cfg.bucket:
        return S3ObjectStore(cfg.bucket, cfg.endpoint_url, cfg.region)
    return LocalObjectStore(cfg.local_dir)
