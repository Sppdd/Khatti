"""Signed webhook delivery to partner systems."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from urllib.parse import urlsplit

import httpx

SIGNATURE_HEADER = "X-Khatti-Signature"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def validate_callback_url(url: str, allowed_hosts: frozenset[str]) -> None:
    """Callbacks must be https, and on the allowlist when one is configured."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError(f"callback host '{host}' is not allowed")
    if parts.scheme != "https" and not (allowed_hosts and host in allowed_hosts):
        raise ValueError("callback_url must use https")


async def deliver(
    http: httpx.AsyncClient, url: str, payload: dict, secret: str, attempts: int = 3, backoff_s: float = 1.0
) -> tuple[bool, str]:
    """POST the payload; returns (delivered, last status/error)."""
    body = json.dumps(payload, ensure_ascii=False, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[SIGNATURE_HEADER] = sign(secret, body)
    last = ""
    for i in range(attempts):
        try:
            resp = await http.post(url, content=body, headers=headers)
            last = f"HTTP {resp.status_code}"
            if resp.is_success:
                return True, last
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        if i + 1 < attempts:
            await asyncio.sleep(backoff_s * 2**i)
    return False, last
