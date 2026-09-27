"""Nemotron router: reviews the evidence and decides, inside rule-based guardrails.

Guardrails:
- Rules set a floor. A hard failure rejects; any warning or low confidence goes to a human.
- Nemotron may escalate an otherwise clean case to human review, but can never
  approve a case the rules escalated, and never rejects on its own (a model
  "reject" becomes human review).
- If Nemotron is unavailable or replies badly, the case goes to human review.
"""

from __future__ import annotations

import json
from typing import Protocol

from .llm import ChatClient, parse_json_object
from .models import CheckResult, Decision, DocumentReading, Severity

SYSTEM_PROMPT = """You are the routing agent of Khatti, a KYC system for Iraqi banks.
You receive document fields extracted by an ensemble of image readers, how much the
readers agreed on each field, and the results of deterministic checks.
Decide one of: "approve", "human_review", "reject".
Prefer "human_review" whenever anything is uncertain, inconsistent or unusual,
for example names that look mis-transcribed, implausible dates, or fields that
the readers disagree on. Never invent facts not in the input.
Reply with JSON only: {"decision": "...", "rationale": "<2-4 sentences in English>"}"""


class Router(Protocol):
    name: str

    async def decide(self, payload: dict) -> tuple[Decision, str]: ...


class NemotronRouter:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.model

    async def decide(self, payload: dict) -> tuple[Decision, str]:
        reply = await self.client.complete(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
        )
        obj = parse_json_object(reply)
        return Decision(obj["decision"]), str(obj.get("rationale", "")).strip()


def case_confidence(readings: list[DocumentReading]) -> float:
    if not readings:
        return 0.0
    return min(r.confidence for r in readings)


def rule_decision(checks: list[CheckResult], confidence: float, min_confidence: float) -> Decision:
    if any(c.severity is Severity.FAIL for c in checks):
        return Decision.REJECT
    if confidence < min_confidence or any(c.severity is Severity.WARN for c in checks):
        return Decision.HUMAN_REVIEW
    return Decision.APPROVE


def rule_rationale(checks: list[CheckResult], confidence: float) -> str:
    issues = [c.message for c in checks if c.severity is not Severity.INFO]
    if not issues:
        return f"All checks passed; reader agreement {confidence:.2f}."
    return "; ".join(issues) + f". Reader agreement {confidence:.2f}."


def build_payload(readings: list[DocumentReading], checks: list[CheckResult], confidence: float) -> dict:
    return {
        "case_confidence": round(confidence, 3),
        "documents": [
            {
                "type": r.doc_type.value,
                "readers_ok": f"{r.readers_ok}/{r.readers_total}",
                "fields": {
                    n: {"value": f.value, "agreement": round(f.agreement, 2)} for n, f in r.fields.items()
                },
            }
            for r in readings
        ],
        "checks": [
            {"code": c.code, "severity": c.severity.value, "message": c.message}
            for c in checks
            if c.severity is not Severity.INFO
        ],
    }


async def route(
    router: Router | None,
    readings: list[DocumentReading],
    checks: list[CheckResult],
    min_confidence: float,
) -> tuple[Decision, str, float, str]:
    """Returns (decision, rationale, confidence, who_decided)."""
    confidence = case_confidence(readings)
    floor = rule_decision(checks, confidence, min_confidence)
    rationale = rule_rationale(checks, confidence)

    if router is None:
        return floor, rationale, confidence, "rules"

    try:
        llm_decision, llm_rationale = await router.decide(build_payload(readings, checks, confidence))
    except Exception as exc:
        if floor is Decision.APPROVE:
            return Decision.HUMAN_REVIEW, f"Router unavailable ({type(exc).__name__}); {rationale}", confidence, "rules"
        return floor, rationale, confidence, "rules"

    if floor is not Decision.APPROVE:
        # Rules already escalated; keep their decision but use the model's explanation too.
        return floor, f"{rationale} Router: {llm_rationale}", confidence, "rules"
    final = Decision.APPROVE if llm_decision is Decision.APPROVE else Decision.HUMAN_REVIEW
    return final, llm_rationale or rationale, confidence, router.name
