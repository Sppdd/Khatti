"""The KYC pipeline as a typed state machine (plan E):

quality -> preprocess -> transcribe (all readers, in parallel) -> classify -> structure
(copy-only, per transcript) -> merge + features + calibrated confidence -> validate ->
cross-document rules -> decide -> (routed only) reviewer brief.
"""

from __future__ import annotations

import asyncio
from datetime import date

from . import __version__
from .calibration import DEFAULT_CALIBRATOR, Calibrator
from .classify import OTHER, Classifier, RuleClassifier
from .crossdoc import RuleSet, evaluate
from .fields import Reading, merge_field
from .models import (
    CheckResult,
    CrossCheck,
    DocumentInput,
    DocumentResult,
    FieldStatus,
    Outcome,
    ReaderStatus,
    RetakeRequest,
    SessionResult,
    Severity,
    Transcript,
)
from .orchestrator import DEFAULT_TAU_DOC, Reviewer, decide, fallback_bullets, render_bullets, reviewer_payload
from .preprocess import prepare
from .quality import FIELD_MESSAGES, assess, field_obstruction
from .readers import Reader
from .registry import FieldGroup, Registry, default_registry
from .structuring import Structurer, verify
from .validation import apply_date_rules

WRONG_SLOT_CONFIDENCE = 0.7


class Pipeline:
    def __init__(
        self,
        readers: list[Reader],
        structurer: Structurer,
        registry: Registry | None = None,
        calibrator: Calibrator | None = None,
        classifier: Classifier | None = None,
        reviewer: Reviewer | None = None,
        primary_readers: dict[str, str] | None = None,
        tau_doc: float = DEFAULT_TAU_DOC,
    ):
        if not readers:
            raise ValueError("at least one image reader is required")
        self.readers = readers
        self.structurer = structurer
        self.registry = registry or default_registry()
        self.calibrator = calibrator or DEFAULT_CALIBRATOR
        self.classifier = classifier or RuleClassifier()
        self.reviewer = reviewer
        # Bake-off outcome: which reader is primary per field group, e.g. {"names": "arabic-vlm", "digits": "nvidia-omni"}
        self.primary_readers = primary_readers or {}
        self.tau_doc = tau_doc
        self.version = f"khatti-{__version__}+{self.calibrator.version}"

    # ------------------------------------------------------------ one document

    async def run_document(self, doc: DocumentInput, today: date) -> tuple[DocumentResult, list[CheckResult], list[RetakeRequest]]:
        spec = self.registry.get(doc.slot)
        ruleset = self.registry.rulesets.get(spec.domain)
        checks: list[CheckResult] = []

        quality, img, quad = await asyncio.to_thread(assess, doc.image)
        prepared = await asyncio.to_thread(prepare, doc.image, doc.mime_type, img, quad)

        per_reader = await asyncio.gather(*(r.transcribe(prepared.images) for r in self.readers))
        transcripts: list[Transcript] = [t for ts in per_reader for t in ts]
        status: dict[str, ReaderStatus] = {}
        for r, ts in zip(self.readers, per_reader):
            status[r.name] = ReaderStatus.OK if any(t.status is ReaderStatus.OK for t in ts) else ts[0].status
            if status[r.name] is not ReaderStatus.OK:
                checks.append(CheckResult(code=f"reader_{status[r.name].value}", severity=Severity.INFO,
                                          message=f"Reader '{r.name}' {status[r.name].value}", slot=doc.slot))
        ok = [t for t in transcripts if t.status is ReaderStatus.OK]

        classification = await self.classifier.classify(ok, prepared.warped_aspect, self.registry.of_domain(spec.domain))
        if classification.doc_type not in (spec.key, OTHER) and classification.confidence >= WRONG_SLOT_CONFIDENCE:
            checks.append(CheckResult(code="wrong_document_in_slot", severity=Severity.WARN, slot=doc.slot,
                                      message=f"Slot expects {spec.key} but the photo looks like {classification.doc_type}"))

        async def structure(t: Transcript):
            try:
                extracted = await self.structurer.structure(t, spec)
            except Exception as exc:
                checks.append(CheckResult(code="structurer_failed", severity=Severity.INFO, slot=doc.slot,
                                          message=f"Structuring {t.reader}#{t.sample} failed: {type(exc).__name__}"))
                return t, {}
            return t, {n: verify(ev, t) for n, ev in extracted.items()}

        structured = await asyncio.gather(*(structure(t) for t in ok))

        fields = {}
        for f in spec.fields:
            readings = [Reading(t.reader, t.sample, v[f.name]) for t, v in structured if f.name in v]
            fields[f.name] = merge_field(
                f, spec.key, readings, status, ruleset, self.calibrator, quality,
                primary_reader=self.primary_readers.get(f.group.value),
            )

        result = DocumentResult(
            slot=doc.slot,
            doc_type=classification.doc_type if classification.doc_type != OTHER else spec.key,
            classification_confidence=classification.confidence,
            quality=quality,
            fields=fields,
            readers=status,
            image_hashes=prepared.hashes,
        )
        checks += apply_date_rules(result, spec, ruleset, today)
        for validator in spec.validators:
            checks += validator(result, today)
        retakes = await asyncio.to_thread(self._retakes, result, img, quad)
        return result, checks, retakes

    # ------------------------------------------------------------ session

    async def run(self, documents: list[DocumentInput], today: date | None = None) -> SessionResult:
        today = today or date.today()
        for d in documents:
            self.registry.get(d.slot)  # fail fast on unknown slots

        done = await asyncio.gather(*(self.run_document(d, today) for d in documents))
        docs = [d for d, _, _ in done]
        checks = [c for _, cs, _ in done for c in cs]

        cross: list[CrossCheck] = []
        # Rules use the slot's type: a document in the wrong slot is already flagged.
        view = [d.model_copy(update={"doc_type": d.slot}) for d in docs]
        for domain in dict.fromkeys(self.registry.get(d.slot).domain for d in docs):
            ruleset = self.registry.rulesets.get(domain)
            if isinstance(ruleset, RuleSet):
                cross += evaluate(ruleset, [d for d in view if self.registry.get(d.slot).domain == domain], today)
                _mark_mismatches(ruleset, cross, docs)

        decision = decide(docs, cross, checks, self.registry, self.tau_doc)
        result = SessionResult(
            decision=decision,
            documents=docs,
            cross_checks=cross,
            checks=checks,
            retake_requests=[r for _, _, rs in done for r in rs],
            pipeline_version=self.version,
        )
        if decision.outcome is Outcome.HUMAN_REVIEW:
            result.review_summary = await self._brief(docs, cross, checks, decision)
        return result

    def _retakes(self, doc: DocumentResult, img, quad) -> list[RetakeRequest]:
        """Photo-level guidance plus a specific message per unreadable required field
        (glare over it, a finger on it, cut off, blurred), located from the reader's line box."""
        spec = self.registry.get(doc.slot)
        name_ar, name_en = spec.label_ar or doc.slot, doc.slot.replace("_", " ")
        out = []
        if doc.quality and doc.quality.retake:
            for code, ar, en in zip(doc.quality.issues, doc.quality.guidance_ar, doc.quality.guidance_en):
                out.append(RetakeRequest(slot=doc.slot, reason=code, message_ar=f"{name_ar}: {ar}", message_en=f"{name_en}: {en}"))
        for f in spec.fields:
            r = doc.fields.get(f.name)
            if not (f.required and r and r.status is FieldStatus.UNREADABLE):
                continue
            why = field_obstruction(img, r.bbox, quad)
            ar, en = FIELD_MESSAGES[why]
            out.append(RetakeRequest(
                slot=doc.slot, field=f.name, reason=why,
                message_ar=f"{name_ar}: " + ar.format(ar=f.label_ar or f.name),
                message_en=f"{name_en}: " + en.format(en=f.label_en or f.name),
            ))
        return out

    async def _brief(self, docs, cross, checks, decision) -> list[str]:
        if self.reviewer is not None:
            try:
                bullets = render_bullets(await self.reviewer.bullets(reviewer_payload(docs, cross, checks, decision)), docs)
                if bullets:
                    decision.decided_by = f"rules; brief by {self.reviewer.name}"
                    return bullets
            except Exception:
                pass  # the brief is a convenience; routing does not depend on it
        return fallback_bullets(docs, cross, checks, self.registry)


def _mark_mismatches(ruleset: RuleSet, cross: list[CrossCheck], docs: list[DocumentResult]) -> None:
    """Fields involved in a failed name/business match get status mismatch."""
    failing = {c.rule for c in cross if c.status == "mismatch"}
    for rule in ruleset.rules:
        if rule.id not in failing:
            continue
        for ref in (rule.left, rule.right):
            doc_key, _, field_name = (ref or "").partition(".")
            for d in docs:
                if (d.slot == doc_key or d.slot.startswith(doc_key + "_")) and field_name in d.fields:
                    d.fields[field_name].status = FieldStatus.MISMATCH
                    d.fields[field_name].reason = f"cross-check {rule.id} failed"


__all__ = ["Pipeline", "FieldGroup"]
