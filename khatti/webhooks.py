"""Webhooks: HMAC-SHA256 signed (Khatti-Signature), retried with exponential backoff for 24h."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

SIGNATURE_HEADER = "Khatti-Signature"
EVENTS = frozenset({"session.completed", "session.needs_review", "session.retake_requested"})
RETRY_WINDOW = timedelta(hours=24)
# Backoff after attempt n (1-based); the last value repeats until the window closes.
BACKOFF = [timedelta(seconds=s) for s in (30, 120, 600, 1800, 3600, 7200, 14400)]


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """t=<unix>,v1=<hex hmac of "<t>.<body>">: the timestamp stops replays of old deliveries."""
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, body: bytes, header: str, tolerance_s: int = 300) -> bool:
    """Reference verifier for receivers."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, body, t), header)


def validate_url(url: str, allowed_hosts: frozenset[str]) -> None:
    """https only, and on the allowlist when one is configured."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError(f"webhook host '{host}' is not allowed")
    if parts.scheme != "https" and not (allowed_hosts and host in allowed_hosts):
        raise ValueError("webhook url must use https")


def next_backoff(attempts: int, created_at: datetime, now: datetime | None = None) -> timedelta | None:
    """Delay before the next attempt, or None once the 24h window is exhausted."""
    now = now or datetime.now(timezone.utc)
    delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
    return delay if now + delay - created_at <= RETRY_WINDOW else None


async def post(http: httpx.AsyncClient, url: str, payload: dict, secret: str) -> tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode()
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, body)}
    try:
        resp = await http.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__
    return resp.is_success, f"HTTP {resp.status_code}"
