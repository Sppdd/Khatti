"""Runtime configuration from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1"

# Model tiers from the plan. Confirm the exact IDs with GET /v1/models?verbose=true.
DEFAULT_ROUTER_MODEL = "nvidia/Nemotron-3_5-Lightning"  # every case: fast routing call
DEFAULT_STRUCTURER_MODEL = "nvidia/nemotron-3-super-120b-a12b"  # maps free-text reader output to the schema
DEFAULT_REVIEWER_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"  # routed cases only: adjudication + reviewer brief


@dataclass(frozen=True)
class EndpointConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    json_mode: bool = True  # send response_format=json_object; support varies by model
    vendor: str = ""


@dataclass(frozen=True)
class StorageConfig:
    # S3-compatible (Nebius Object Storage, MinIO, ...) when bucket is set; else local directory.
    bucket: str = ""
    endpoint_url: str = ""
    region: str = "eu-north1"
    local_dir: str = "./data/objects"


@dataclass(frozen=True)
class Settings:
    router: EndpointConfig | None
    structurer: EndpointConfig | None
    reviewer: EndpointConfig | None
    readers: list[EndpointConfig] = field(default_factory=list)
    min_confidence: float = 0.85
    timeout_s: float = 60.0
    database_url: str = ""
    storage: StorageConfig = field(default_factory=StorageConfig)
    webhook_secret: str = ""
    webhook_allowed_hosts: frozenset[str] = frozenset()
    api_keys: frozenset[str] = frozenset()


def _csv(name: str) -> frozenset[str]:
    return frozenset(v.strip() for v in os.getenv(name, "").split(",") if v.strip())


def load_settings() -> Settings:
    tf_url = os.getenv("KHATTI_TOKEN_FACTORY_URL", TOKEN_FACTORY_URL)
    tf_key = os.getenv("KHATTI_TOKEN_FACTORY_KEY", "")

    def tier(name: str, env: str, default: str) -> EndpointConfig | None:
        model = os.getenv(env, default)
        if not tf_key or not model:
            return None
        return EndpointConfig(name=name, base_url=tf_url, model=model, api_key=tf_key, vendor="nvidia")

    # KHATTI_READERS is a JSON list of {"name", "base_url", "model", "api_key"?, "json_mode"?, "vendor"?}.
    # A reader without api_key falls back to the Token Factory key.
    readers = [
        EndpointConfig(
            name=r["name"],
            base_url=r["base_url"],
            model=r["model"],
            api_key=r.get("api_key") or tf_key,
            json_mode=r.get("json_mode", True),
            vendor=r.get("vendor", ""),
        )
        for r in json.loads(os.getenv("KHATTI_READERS", "[]"))
    ]

    return Settings(
        router=tier("router", "KHATTI_ROUTER_MODEL", DEFAULT_ROUTER_MODEL),
        structurer=tier("structurer", "KHATTI_STRUCTURER_MODEL", DEFAULT_STRUCTURER_MODEL),
        reviewer=tier("reviewer", "KHATTI_REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL),
        readers=readers,
        min_confidence=float(os.getenv("KHATTI_MIN_CONFIDENCE", "0.85")),
        timeout_s=float(os.getenv("KHATTI_TIMEOUT_S", "60")),
        database_url=os.getenv("KHATTI_DATABASE_URL", ""),
        storage=StorageConfig(
            bucket=os.getenv("KHATTI_S3_BUCKET", ""),
            endpoint_url=os.getenv("KHATTI_S3_ENDPOINT_URL", ""),
            region=os.getenv("KHATTI_S3_REGION", "eu-north1"),
            local_dir=os.getenv("KHATTI_LOCAL_STORAGE_DIR", "./data/objects"),
        ),
        webhook_secret=os.getenv("KHATTI_WEBHOOK_SECRET", ""),
        webhook_allowed_hosts=_csv("KHATTI_WEBHOOK_ALLOWED_HOSTS"),
        api_keys=_csv("KHATTI_API_KEYS"),
    )
