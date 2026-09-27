"""Runtime configuration from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EndpointConfig:
    name: str
    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True)
class Settings:
    # Nemotron (text-only) on Nebius Token Factory: reasoning, validation review, routing.
    router: EndpointConfig | None
    # Image readers. At least one should be the NVIDIA VLM on a Nebius Serverless Endpoint.
    readers: list[EndpointConfig] = field(default_factory=list)
    min_confidence: float = 0.85
    timeout_s: float = 60.0


def load_settings() -> Settings:
    token_factory_url = os.getenv("KHATTI_TOKEN_FACTORY_URL", "https://api.studio.nebius.com/v1")
    token_factory_key = os.getenv("KHATTI_TOKEN_FACTORY_KEY", "")

    router = None
    if token_factory_key:
        router = EndpointConfig(
            name="nemotron",
            base_url=token_factory_url,
            model=os.getenv("KHATTI_ROUTER_MODEL", "nvidia/Llama-3_3-Nemotron-Super-49B-v1_5"),
            api_key=token_factory_key,
        )

    # KHATTI_READERS is a JSON list: [{"name", "base_url", "model", "api_key"?}]
    # A reader without api_key falls back to the Token Factory key.
    readers = [
        EndpointConfig(
            name=r["name"],
            base_url=r["base_url"],
            model=r["model"],
            api_key=r.get("api_key") or token_factory_key,
        )
        for r in json.loads(os.getenv("KHATTI_READERS", "[]"))
    ]

    return Settings(
        router=router,
        readers=readers,
        min_confidence=float(os.getenv("KHATTI_MIN_CONFIDENCE", "0.85")),
        timeout_s=float(os.getenv("KHATTI_TIMEOUT_S", "60")),
    )
