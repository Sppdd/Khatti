"""End-to-end pipeline: read -> merge -> check -> route -> (brief reviewer)."""

from __future__ import annotations

import asyncio
from datetime import date

from .consensus import build_reading
from .models import CaseResult, Decision, DocumentInput
from .orchestrator import Reviewer, Router, build_payload, case_confidence, route
from .readers import Reader
from .registry import Registry, default_registry
from .validation import check_case


class Pipeline:
    def __init__(
        self,
        readers: list[Reader],
        router: Router | None = None,
        reviewer: Reviewer | None = None,
        registry: Registry | None = None,
        min_confidence: float = 0.85,
    ):
        if not readers:
            raise ValueError("at least one image reader is required")
        self.readers = readers
        self.router = router
        self.reviewer = reviewer
        self.registry = registry or default_registry()
        self.min_confidence = min_confidence

    async def run(self, documents: list[DocumentInput], today: date | None = None) -> CaseResult:
        today = today or date.today()
        specs = [self.registry.get(d.doc_type) for d in documents]

        # Every reader reads every document, all in parallel.
        results = await asyncio.gather(*(r.read(d, s) for d, s in zip(documents, specs) for r in self.readers))
        n = len(self.readers)
        readings = [
            build_reading(d.doc_type, s.field_names, list(results[i * n : (i + 1) * n]))
            for i, (d, s) in enumerate(zip(documents, specs))
        ]

        checks = check_case(readings, self.registry, today)
        confidence = case_confidence(readings)
        payload = build_payload(readings, checks, confidence)
        decision, rationale, who = await route(self.router, payload, checks, confidence, self.min_confidence)

        summary = None
        if self.reviewer is not None and decision is not Decision.APPROVE:
            try:
                summary = await self.reviewer.summarize(payload, decision)
            except Exception:
                summary = None  # the brief is a convenience; the case still routes

        return CaseResult(
            decision=decision,
            rationale=rationale,
            confidence=round(confidence, 3),
            documents=readings,
            checks=checks,
            router=who,
            reviewer_summary=summary,
        )
