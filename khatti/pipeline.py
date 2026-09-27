"""End-to-end KYC pipeline: read -> merge -> check -> route."""

from __future__ import annotations

import asyncio
from datetime import date

from .consensus import build_reading
from .models import CaseResult, DocumentInput
from .orchestrator import Router, route
from .readers import Reader
from .validation import check_cross_documents, check_document


class KycPipeline:
    def __init__(self, readers: list[Reader], router: Router | None = None, min_confidence: float = 0.85):
        if not readers:
            raise ValueError("at least one image reader is required")
        self.readers = readers
        self.router = router
        self.min_confidence = min_confidence

    async def run(self, documents: list[DocumentInput], today: date | None = None) -> CaseResult:
        today = today or date.today()

        # Every reader reads every document, all in parallel.
        results = await asyncio.gather(*(r.read(d) for d in documents for r in self.readers))
        n = len(self.readers)
        readings = [build_reading(d.doc_type, list(results[i * n : (i + 1) * n])) for i, d in enumerate(documents)]

        checks = [c for r in readings for c in check_document(r, today)]
        checks += check_cross_documents(readings)

        decision, rationale, confidence, who = await route(self.router, readings, checks, self.min_confidence)
        return CaseResult(
            decision=decision,
            rationale=rationale,
            confidence=round(confidence, 3),
            documents=readings,
            checks=checks,
            router=who,
        )
