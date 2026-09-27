"""Shared schemas for the document pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocumentInput(BaseModel):
    doc_type: str
    image: bytes
    mime_type: str = "image/jpeg"


class ReaderStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"  # endpoint down / unreachable: expected, e.g. GPU endpoint stopped
    ERROR = "error"  # reachable but the reply was unusable


class ReaderResult(BaseModel):
    """What one image reader extracted from one document."""

    reader: str
    fields: dict[str, str | None] = Field(default_factory=dict)
    status: ReaderStatus = ReaderStatus.OK
    error: str | None = None


class FieldConsensus(BaseModel):
    name: str
    value: str | None
    agreement: float = Field(ge=0, le=1, description="Share of readers that agree with the winning value")
    candidates: dict[str, str | None] = Field(default_factory=dict, description="reader -> raw value")


class DocumentReading(BaseModel):
    doc_type: str
    fields: dict[str, FieldConsensus]
    readers: dict[str, ReaderStatus] = Field(default_factory=dict)

    @property
    def readers_ok(self) -> int:
        return sum(s is ReaderStatus.OK for s in self.readers.values())

    @property
    def confidence(self) -> float:
        if not self.fields:
            return 0.0
        return sum(f.agreement for f in self.fields.values()) / len(self.fields)

    def value(self, name: str) -> str | None:
        f = self.fields.get(name)
        return f.value if f else None


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"  # needs a human look
    FAIL = "fail"  # hard failure


class CheckResult(BaseModel):
    code: str
    severity: Severity
    message: str
    doc_type: str | None = None


class Decision(str, Enum):
    APPROVE = "approve"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class CaseResult(BaseModel):
    decision: Decision
    rationale: str
    confidence: float
    documents: list[DocumentReading]
    checks: list[CheckResult]
    router: str = Field(description="Which component made the final call")
    reviewer_summary: str | None = Field(None, description="Brief for the human reviewer (routed cases only)")
