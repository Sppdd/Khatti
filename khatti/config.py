"""Runtime configuration from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1"

# Model tiers from the plan. Confirm the exact IDs with GET /v1/models?verbose=true.
DEFAULT_FAST_MODEL = "nvidia/Nemotron-3_5-Lightning"  # classification over reader captions
DEFAULT_STRUCTURER_MODEL = "nvidia/nemotron-3-super-120b-a12b"  # copy-only structuring
DEFAULT_REVIEWER_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"  # routed sessions only


@dataclass(frozen=True)
class EndpointConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    json_mode: bool = True  # send response_format; support varies by model
    samples: int = 1  # >1: self-consistency sampling at temperature 0.7
    logprobs: bool = False  # request token logprobs (only where the inventory shows support)
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
    fast: EndpointConfig | None
    structurer: EndpointConfig | None
    reviewer: EndpointConfig | None
    readers: list[EndpointConfig] = field(default_factory=list)
    primary_readers: dict[str, str] = field(default_factory=dict)
    calibrator_path: str = ""
    tau_doc: float = 0.8
    timeout_s: float = 90.0
    database_url: str = ""
    storage: StorageConfig = field(default_factory=StorageConfig)
    public_base_url: str = "http://localhost:8000"
    data_key: str = ""  # base64 32 bytes; envelope encryption KEK + HMAC key
    jwt_secret: str = ""
    retention_days: int = 30
    reminder_lead_days: int = 30
    rate_limit_per_minute: int = 120
    webhook_allowed_hosts: frozenset[str] = frozenset()


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

    # KHATTI_READERS: JSON list of {"name", "base_url", "model", "api_key"?, "json_mode"?, "samples"?, "vendor"?}.
    # A reader without api_key falls back to the Token Factory key.
    readers = [
        EndpointConfig(
            name=r["name"],
            base_url=r["base_url"],
            model=r["model"],
            api_key=r.get("api_key") or tf_key,
            json_mode=r.get("json_mode", True),
            samples=int(r.get("samples", 1)),
            logprobs=bool(r.get("logprobs", False)),
            vendor=r.get("vendor", ""),
        )
        for r in json.loads(os.getenv("KHATTI_READERS", "[]"))
    ]

    return Settings(
        fast=tier("lightning", "KHATTI_FAST_MODEL", DEFAULT_FAST_MODEL),
        structurer=tier("super", "KHATTI_STRUCTURER_MODEL", DEFAULT_STRUCTURER_MODEL),
        reviewer=tier("ultra", "KHATTI_REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL),
        readers=readers,
        # Bake-off outcome, e.g. {"names": "arabic-vlm", "digits": "nvidia-omni", "dates": "nvidia-omni"}
        primary_readers=json.loads(os.getenv("KHATTI_PRIMARY_READERS", "{}")),
        calibrator_path=os.getenv("KHATTI_CALIBRATOR_PATH", ""),
        tau_doc=float(os.getenv("KHATTI_TAU_DOC", "0.8")),
        timeout_s=float(os.getenv("KHATTI_TIMEOUT_S", "90")),
        database_url=os.getenv("KHATTI_DATABASE_URL", ""),
        storage=StorageConfig(
            bucket=os.getenv("KHATTI_S3_BUCKET", ""),
            endpoint_url=os.getenv("KHATTI_S3_ENDPOINT_URL", ""),
            region=os.getenv("KHATTI_S3_REGION", "eu-north1"),
            local_dir=os.getenv("KHATTI_LOCAL_STORAGE_DIR", "./data/objects"),
        ),
        public_base_url=os.getenv("KHATTI_PUBLIC_BASE_URL", "http://localhost:8000"),
        data_key=os.getenv("KHATTI_DATA_KEY", ""),
        jwt_secret=os.getenv("KHATTI_JWT_SECRET", ""),
        retention_days=int(os.getenv("KHATTI_RETENTION_DAYS", "30")),
        reminder_lead_days=int(os.getenv("KHATTI_REMINDER_LEAD_DAYS", "30")),
        rate_limit_per_minute=int(os.getenv("KHATTI_RATE_LIMIT_PER_MINUTE", "120")),
        webhook_allowed_hosts=_csv("KHATTI_WEBHOOK_ALLOWED_HOSTS"),
    )
