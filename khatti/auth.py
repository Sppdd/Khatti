"""Authentication and rate limiting.

- Tenants call the API with `Authorization: Bearer <api_key>` (role "service").
- Reviewers and the mobile app get short-lived JWTs minted by POST /v1/auth/token
  (roles "reviewer" and "app").
- Token-bucket rate limit per API key (per process); 429 with Retry-After.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import jwt

MAX_TOKEN_TTL_S = 3600
ROLES = {"service", "reviewer", "app"}
ALGORITHM = "HS256"


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    subject: str
    role: str
    key_id: str  # rate-limit bucket: API key id, or the key that minted the token

    def can(self, *roles: str) -> bool:
        return self.role == "service" or self.role in roles


def mint_token(secret: str, principal: Principal, subject: str, role: str, ttl_s: int) -> tuple[str, int]:
    if role not in ROLES - {"service"}:
        raise ValueError(f"role must be one of {sorted(ROLES - {'service'})}")
    ttl = max(60, min(ttl_s, MAX_TOKEN_TTL_S))
    exp = int(time.time()) + ttl
    claims = {"tid": str(principal.tenant_id), "sub": subject, "role": role, "kid": principal.key_id,
              "exp": exp, "iss": "khatti"}
    return jwt.encode(claims, secret, algorithm=ALGORITHM), exp


def verify_token(secret: str, token: str) -> Principal:
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], issuer="khatti", options={"require": ["exp", "tid", "role"]})
    if claims["role"] not in ROLES - {"service"}:
        raise jwt.InvalidTokenError("bad role")
    return Principal(uuid.UUID(claims["tid"]), claims.get("sub", ""), claims["role"], claims.get("kid", ""))


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and not token.startswith("kh_")


@dataclass
class RateLimiter:
    per_minute: int
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)  # key -> (tokens, updated)

    def take(self, key: str, now: float | None = None) -> float:
        """Consume one token. Returns 0 if allowed, else seconds until the next token."""
        if self.per_minute <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        rate = self.per_minute / 60.0
        tokens, updated = self._buckets.get(key, (float(self.per_minute), now))
        tokens = min(float(self.per_minute), tokens + (now - updated) * rate)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return 0.0
        self._buckets[key] = (tokens, now)
        return (1 - tokens) / rate
