"""Decision rules and the Nemotron Ultra reviewer brief.

The decision is deterministic (plan E.8): auto_pass only if every required field is ok,
every cross-document rule passes, no document is expired, no blocking check fired, and
the session probability >= tau_doc. Otherwise human_review, with reasons.

Nemotron Ultra runs only on routed sessions. It adjudicates residual ambiguity (e.g. a
missing grandfather name vs a different person) and writes 3-6 reviewer bullets. It
refers to values only through locked placeholders ({{field:<slot>.<field>}}); code fills
them from the verified results, and any bullet that contains a raw digit or Arabic text
is dropped, so the summary cannot introduce or alter a value.
"""

from __future__ import annotations

import json
import math
import re
from typing import Protocol

from .crossdoc import PASSING
from .llm import ChatClient, parse_json_object
from .models import CheckResult, CrossCheck, Decision, DocumentResult, FieldStatus, Outcome, Severity
from .registry import Registry

DEFAULT_TAU_DOC = 0.8

# Reason codes (stable, for filtering the review queue)
LOW_CONF_FIELD = "LOW_CONF_FIELD"
UNREADABLE_FIELD = "UNREADABLE_FIELD"
MISSING_FIELD = "MISSING_FIELD"
INVALID_FORMAT = "INVALID_FORMAT"
EXPIRED = "EXPIRED"
NAME_PARTIAL_MATCH = "NAME_PARTIAL_MATCH"
NAME_TRANSLIT_ONLY = "NAME_TRANSLITERATION_ONLY"
CROSS_DOC_MISMATCH = "CROSS_DOC_MISMATCH"
WRONG_DOCUMENT = "WRONG_DOCUMENT_IN_SLOT"
READER_UNAVAILABLE = "READER_UNAVAILABLE"
CHECK_FAILED = "CHECK_FAILED"
LOW_SESSION_CONF = "LOW_SESSION_CONFIDENCE"
RETAKE = "RETAKE_REQUESTED"

_STATUS_REASON = {
    FieldStatus.LOW_CONFIDENCE: LOW_CONF_FIELD,
    FieldStatus.UNREADABLE: UNREADABLE_FIELD,
    FieldStatus.NOT_PRESENT: MISSING_FIELD,
    FieldStatus.INVALID_FORMAT: INVALID_FORMAT,
    FieldStatus.EXPIRED: EXPIRED,
    FieldStatus.MISMATCH: CROSS_DOC_MISMATCH,
}


def session_confidence(docs: list[DocumentResult], registry: Registry) -> float:
    """P(every required field is correct), treating fields as independent."""
    logp = 0.0
    for d in docs:
        spec = registry.types.get(d.doc_type) or registry.types.get(d.slot)
        if spec is None:
            return 0.0
        for f in spec.fields:
            if f.required:
                r = d.fields.get(f.name)
                c = r.confidence if r and r.value is not None else 0.0
                logp += math.log(max(c, 1e-9))
    return math.exp(logp)


def decide(
    docs: list[DocumentResult],
    cross: list[CrossCheck],
    checks: list[CheckResult],
    registry: Registry,
    tau_doc: float = DEFAULT_TAU_DOC,
) -> Decision:
    reasons: list[str] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    for d in docs:
        spec = registry.get(d.slot)
        for f in spec.fields:
            r = d.fields.get(f.name)
            if r is None:
                continue
            if r.status is not FieldStatus.OK and (f.required or r.status in (FieldStatus.EXPIRED, FieldStatus.INVALID_FORMAT)):
                add(_STATUS_REASON[r.status])
        if d.quality and d.quality.retake:
            add(RETAKE)
    for c in cross:
        if c.status not in PASSING:
            add({"partial_match": NAME_PARTIAL_MATCH, "transliteration_match": NAME_TRANSLIT_ONLY, "fail": EXPIRED}.get(c.status, CROSS_DOC_MISMATCH))
    for c in checks:
        if c.severity is not Severity.INFO:
            add({"wrong_document_in_slot": WRONG_DOCUMENT, "document_expired": EXPIRED}.get(c.code, CHECK_FAILED))
        elif c.code == "reader_unavailable":
            pass  # recorded in the audit trail; the missing vote already lowers confidence

    conf = session_confidence(docs, registry)
    if conf < tau_doc:
        add(LOW_SESSION_CONF)
    outcome = Outcome.HUMAN_REVIEW if reasons else Outcome.AUTO_PASS
    return Decision(outcome=outcome, session_confidence=round(conf, 4), reasons=reasons)


# ---------------------------------------------------------------- reviewer brief (Ultra)

REVIEWER_PROMPT = """You brief a human KYC reviewer at an Iraqi bank on an onboarding session the automated
system could not auto-pass. Write 3 to 6 short English bullets: WHAT to check, WHY (the signal:
reader disagreement, unreadable field, failed rule, photo quality), and WHERE (which document).
Also adjudicate residual ambiguity, e.g. whether a missing grandfather name looks like the same
person or a different one; say what evidence supports each reading.

STRICT: never write any field value, name, number or date yourself. Refer to values ONLY with
placeholders of the form {{field:<slot>.<field>}} exactly as listed in "placeholders".
Do not write any Arabic text or any digits.
Reply with JSON only: {"bullets": ["...", "..."]}"""

_PLACEHOLDER = re.compile(r"\{\{field:([a-z0-9_]+)\.([a-z0-9_]+)\}\}")
_FORBIDDEN = re.compile(r"[0-9٠-٩۰-۹؀-ۿ]")


def reviewer_payload(docs: list[DocumentResult], cross: list[CrossCheck], checks: list[CheckResult], decision: Decision) -> dict:
    """What Ultra sees: statuses, reasons and scores, but no values (values stay locked)."""
    return {
        "reasons": decision.reasons,
        "session_confidence": decision.session_confidence,
        "documents": [
            {
                "slot": d.slot,
                "classified_as": d.doc_type,
                "photo_issues": d.quality.issues if d.quality else [],
                "readers": {k: v.value for k, v in d.readers.items()},
                "fields": {
                    n: {"status": f.status.value, "confidence": f.confidence, "reason": f.reason}
                    for n, f in d.fields.items()
                },
            }
            for d in docs
        ],
        "cross_checks": [c.model_dump() | {"detail": re.sub(_FORBIDDEN, "", c.detail)} for c in cross],
        "checks": [{"code": c.code, "severity": c.severity.value, "slot": c.slot, "field": c.field} for c in checks],
        "placeholders": [f"{{{{field:{d.slot}.{n}}}}}" for d in docs for n in d.fields],
    }


def render_bullets(bullets: list[str], docs: list[DocumentResult]) -> list[str]:
    values = {(d.slot, n): f for d in docs for n, f in d.fields.items()}
    out = []
    for b in bullets:
        if not isinstance(b, str) or not b.strip():
            continue
        refs = _PLACEHOLDER.findall(b)
        if any(r not in values for r in refs):
            continue  # unknown placeholder: drop the bullet
        if _FORBIDDEN.search(_PLACEHOLDER.sub("", b)):
            continue  # model wrote a value itself: drop the bullet
        out.append(
            _PLACEHOLDER.sub(
                lambda m: values[(m.group(1), m.group(2))].value or "[unreadable]",
                b.strip(),
            )
        )
    return out[:6]


class Reviewer(Protocol):
    name: str

    async def bullets(self, payload: dict) -> list[str]: ...


class NemotronReviewer:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.model

    async def bullets(self, payload: dict) -> list[str]:
        reply = await self.client.complete(
            [
                {"role": "system", "content": REVIEWER_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=1200,
            purpose="review_summary",
        )
        return [str(b) for b in parse_json_object(reply).get("bullets", [])]


def fallback_bullets(docs: list[DocumentResult], cross: list[CrossCheck], checks: list[CheckResult], registry: Registry) -> list[str]:
    """Deterministic brief used when Ultra is not configured or fails."""
    out = []
    for d in docs:
        spec = registry.get(d.slot)
        for n, f in d.fields.items():
            if f.status is not FieldStatus.OK and (spec.field(n).required or f.status is not FieldStatus.NOT_PRESENT):
                why = f.reason or f.status.value
                out.append(f"{spec.field(n).label_en or n} on {d.slot}: {f.status.value} ({why}).")
        if d.quality and d.quality.retake:
            out.append(f"{d.slot}: photo issues ({', '.join(d.quality.issues)}); consider requesting a retake.")
    for c in cross:
        if c.status not in PASSING:
            out.append(f"Cross-check {c.rule}: {c.status}. {re.sub(_FORBIDDEN, '', c.detail)}".strip())
    for c in checks:
        if c.severity is not Severity.INFO and c.field is None:
            out.append(f"{c.slot or 'session'}: {c.message}")
    return out[:6]
