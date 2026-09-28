"""Image storage over the S3 API only (Nebius Object Storage, MinIO on-prem, ...), or a local
directory for development. Clients upload directly with short-lived presigned PUT URLs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Protocol

from .config import StorageConfig

UPLOAD_TTL_S = 600


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    def presign_put(self, key: str, content_type: str, ttl_s: int = UPLOAD_TTL_S) -> dict: ...


class LocalObjectStore:
    """Development store. Presigned uploads go through the API's PUT /v1/uploads/{token}."""

    def __init__(self, root: str | Path, upload_base_url: str = "http://localhost:8000", secret: bytes = b"dev"):
        self.root = Path(root).resolve()
        self.upload_base_url = upload_base_url.rstrip("/")
        self._secret = secret

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

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    # ------------------------------------------------------------ signed upload tokens

    def presign_put(self, key: str, content_type: str, ttl_s: int = UPLOAD_TTL_S) -> dict:
        body = base64.urlsafe_b64encode(json.dumps({"k": key, "t": content_type, "e": int(time.time()) + ttl_s}).encode())
        sig = base64.urlsafe_b64encode(hmac.new(self._secret, body, hashlib.sha256).digest()[:16])
        token = (body + b"." + sig).decode()
        return {"url": f"{self.upload_base_url}/v1/uploads/{token}", "method": "PUT", "headers": {"Content-Type": content_type},
                "expires_in": ttl_s}

    def verify_upload_token(self, token: str) -> tuple[str, str]:
        try:
            body, sig = token.encode().split(b".")
            want = base64.urlsafe_b64encode(hmac.new(self._secret, body, hashlib.sha256).digest()[:16])
            claims = json.loads(base64.urlsafe_b64decode(body))
        except Exception as exc:
            raise ValueError("malformed upload token") from exc
        if not hmac.compare_digest(sig, want):
            raise ValueError("bad upload token signature")
        if claims["e"] < time.time():
            raise ValueError("upload token expired")
        return claims["k"], claims["t"]


class S3ObjectStore:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str):
        import boto3  # imported lazily: only needed when S3 is configured
        from botocore.config import Config

        self.bucket = bucket
        # Credentials come from the standard AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY variables.
        self._s3 = boto3.client(
            "s3", endpoint_url=endpoint_url or None, region_name=region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        resp = await asyncio.to_thread(self._s3.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(resp["Body"].read)

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(self._s3.head_object, Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._s3.delete_object, Bucket=self.bucket, Key=key)

    def presign_put(self, key: str, content_type: str, ttl_s: int = UPLOAD_TTL_S) -> dict:
        url = self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=ttl_s
        )
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}, "expires_in": ttl_s}


def object_store_from_config(cfg: StorageConfig, upload_base_url: str, secret: bytes) -> ObjectStore:
    if cfg.bucket:
        return S3ObjectStore(cfg.bucket, cfg.endpoint_url, cfg.region)
    return LocalObjectStore(cfg.local_dir, upload_base_url, secret)
