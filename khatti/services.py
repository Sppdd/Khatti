"""Wires configuration into the pipeline, store, object store and service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from .auth import RateLimiter
from .calibration import Calibrator
from .classify import LightningClassifier, RuleClassifier
from .config import Settings, load_settings
from .crypto import Keyring
from .llm import ChatClient
from .orchestrator import NemotronReviewer
from .pipeline import Pipeline
from .readers import VisionChatReader
from .registry import Registry, default_registry
from .service import KycService
from .storage import ObjectStore, object_store_from_config
from .store import Store
from .structuring import LabelStructurer, NemotronStructurer

log = logging.getLogger("khatti")


@dataclass
class Services:
    registry: Registry
    pipeline: Pipeline | None = None
    service: KycService | None = None
    objects: ObjectStore | None = None
    jwt_secret: str = ""
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(120))
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=10))

    async def close(self) -> None:
        if self.service:
            await self.service.store.close()
        await self.http.aclose()


def build_pipeline(settings: Settings, registry: Registry) -> Pipeline | None:
    if not settings.readers:
        return None
    t = settings.timeout_s
    readers = [VisionChatReader(ChatClient(r, t), samples=r.samples) for r in settings.readers]
    if settings.structurer:
        structurer = NemotronStructurer(ChatClient(settings.structurer, t))
    else:
        log.warning("no structurer model configured: using the deterministic label structurer")
        structurer = LabelStructurer()
    classifier = LightningClassifier(ChatClient(settings.fast, t)) if settings.fast else RuleClassifier()
    reviewer = NemotronReviewer(ChatClient(settings.reviewer, t)) if settings.reviewer else None
    calibrator = Calibrator.load(settings.calibrator_path) if settings.calibrator_path else None
    return Pipeline(readers, structurer, registry, calibrator, classifier, reviewer,
                    primary_readers=settings.primary_readers, tau_doc=settings.tau_doc)


async def services_from_env() -> Services:
    settings = load_settings()
    registry = default_registry()
    pipeline = build_pipeline(settings, registry)
    svc = Services(registry=registry, pipeline=pipeline, jwt_secret=settings.jwt_secret,
                   limiter=RateLimiter(settings.rate_limit_per_minute))
    if settings.database_url:
        if not settings.data_key or not settings.jwt_secret:
            raise SystemExit("KHATTI_DATA_KEY and KHATTI_JWT_SECRET are required with a database")
        keys = Keyring.from_env_value(settings.data_key)
        objects = object_store_from_config(settings.storage, settings.public_base_url, keys.hmac_key)
        svc.objects = objects
        svc.service = KycService(
            store=await Store.connect(settings.database_url),
            objects=objects,
            keys=keys,
            registry=registry,
            pipeline=pipeline,
            http=svc.http,
            webhook_allowed_hosts=settings.webhook_allowed_hosts,
            retention_days=settings.retention_days,
            reminder_lead_days=settings.reminder_lead_days,
        )
    return svc
