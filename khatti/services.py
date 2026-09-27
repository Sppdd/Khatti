"""Wires configuration into the pipeline, case store and object store."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .config import Settings, load_settings
from .db import CaseStore
from .llm import ChatClient
from .orchestrator import NemotronReviewer, NemotronRouter
from .pipeline import Pipeline
from .readers import Structurer, VisionChatReader
from .storage import ObjectStore, object_store_from_config


@dataclass
class Services:
    pipeline: Pipeline | None
    cases: CaseStore | None = None
    objects: ObjectStore | None = None
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=10))
    webhook_secret: str = ""
    webhook_allowed_hosts: frozenset[str] = frozenset()
    api_keys: frozenset[str] = frozenset()

    async def close(self) -> None:
        if self.cases:
            await self.cases.close()
        await self.http.aclose()


def build_pipeline(settings: Settings) -> Pipeline | None:
    if not settings.readers:
        return None
    t = settings.timeout_s
    structurer = Structurer(ChatClient(settings.structurer, t)) if settings.structurer else None
    readers = [VisionChatReader(ChatClient(r, t), structurer) for r in settings.readers]
    router = NemotronRouter(ChatClient(settings.router, t)) if settings.router else None
    reviewer = NemotronReviewer(ChatClient(settings.reviewer, t)) if settings.reviewer else None
    return Pipeline(readers, router, reviewer, min_confidence=settings.min_confidence)


async def services_from_env() -> Services:
    settings = load_settings()
    cases = await CaseStore.connect(settings.database_url) if settings.database_url else None
    return Services(
        pipeline=build_pipeline(settings),
        cases=cases,
        objects=object_store_from_config(settings.storage) if cases else None,
        webhook_secret=settings.webhook_secret,
        webhook_allowed_hosts=settings.webhook_allowed_hosts,
        api_keys=settings.api_keys,
    )
