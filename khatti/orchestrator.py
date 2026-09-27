"""Nemotron routing: decides inside rule-based guardrails, and briefs the human reviewer.

Guardrails:
- Rules set a floor. A hard failure rejects; any warning or low confidence goes to a human.
- The router model may escalate an otherwise clean case to human review, but can never
  approve a case the rules escalated, and never rejects on its own (a model
  "reject" becomes human review).
- If the router is unavailable or replies badly, the case goes to human review.
- The reviewer model (Ultra) only runs on routed cases and only writes a brief; it
  never changes the decision.
"""

from __future__ import annotations

import json
from typing import Protocol

from .llm import ChatClient, parse_json_object
from .models import CheckResult, Decision, DocumentReading, Severity

ROUTER_PROMPT = """You are the routing agent of Khatti, an Arabic-first document agent used by Iraqi banks.
You receive document fields extracted by an ensemble of image readers, how much the
readers agreed on each field, which readers were unavailable, and the results of
deterministic checks. Decide one of: "approve", "human_review", "reject".
Prefer "human_review" whenever anything is uncertain, inconsistent or unusual,
for example names that look mis-transcribed, implausible dates, or fields that
the readers disagree on. Never invent facts not in the input.
Reply with JSON only: {"decision": "...", "rationale": "<2-4 sentences in English>"}"""

REVIEWER_PROMPT = """You brief a human KYC reviewer at an Iraqi bank on a case the automated system
could not approve. Using only the input, adjudicate across documents: say which fields
to verify against the images and why (reader disagreement, failed checks, cross-document
conflicts), and what looks consistent. Be concise and concrete. Never invent facts.
Reply with JSON only: {"summary_en": "<=120 words", "summary_ar": "<=120 words, Arabic"}"""


class Router(Protocol):
    name: str

    async def decide(self, payload: dict) -> tuple[Decision, str]: ...


class Reviewer(Protocol):
    name: str

    async def summarize(self, payload: dict, decision: Decision) -> str: ...


class NemotronRouter:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.model

    async def decide(self, payload: dict) -> tuple[Decision, str]:
        reply = await self.client.complete(
            [
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
        )
        obj = parse_json_object(reply)
        return Decision(obj["decision"]), str(obj.get("rationale", "")).strip()


class NemotronReviewer:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.model

    async def summarize(self, payload: dict, decision: Decision) -> str:
        reply = await self.client.complete(
            [
                {"role": "system", "content": REVIEWER_PROMPT},
                {"role": "user", "content": json.dumps({"decision": decision.value, **payload}, ensure_ascii=False)},
            ],
            max_tokens=2000,
        )
        obj = parse_json_object(reply)
        return "\n\n".join(s for s in (obj.get("summary_en"), obj.get("summary_ar")) if s)


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
                "type": r.doc_type,
                "readers": {name: status.value for name, status in r.readers.items()},
                "fields": {n: {"value": f.value, "agreement": round(f.agreement, 2)} for n, f in r.fields.items()},
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
    payload: dict,
    checks: list[CheckResult],
    confidence: float,
    min_confidence: float,
) -> tuple[Decision, str, str]:
    """Returns (decision, rationale, who_decided)."""
    floor = rule_decision(checks, confidence, min_confidence)
    rationale = rule_rationale(checks, confidence)

    if router is None:
        return floor, rationale, "rules"

    try:
        llm_decision, llm_rationale = await router.decide(payload)
    except Exception as exc:
        if floor is Decision.APPROVE:
            return Decision.HUMAN_REVIEW, f"Router unavailable ({type(exc).__name__}); {rationale}", "rules"
        return floor, rationale, "rules"

    if floor is not Decision.APPROVE:
        # Rules already escalated; keep their decision but include the model's explanation.
        return floor, f"{rationale} Router: {llm_rationale}", "rules"
    final = Decision.APPROVE if llm_decision is Decision.APPROVE else Decision.HUMAN_REVIEW
    return final, llm_rationale or rationale, router.name
