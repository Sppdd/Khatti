"""Shared schemas for the KYC pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    NATIONAL_ID = "national_id"  # البطاقة الوطنية الموحدة
    PASSPORT = "passport"  # جواز السفر
    RESIDENCE_CARD = "residence_card"  # بطاقة السكن


# Fields each document type is expected to yield.
EXPECTED_FIELDS: dict[DocumentType, list[str]] = {
    DocumentType.NATIONAL_ID: [
        "full_name_ar",
        "mother_name_ar",
        "date_of_birth",
        "id_number",
        "expiry_date",
        "sex",
    ],
    DocumentType.PASSPORT: [
        "full_name_ar",
        "full_name_en",
        "date_of_birth",
        "passport_number",
        "expiry_date",
        "sex",
        "mrz_line1",
        "mrz_line2",
    ],
    DocumentType.RESIDENCE_CARD: [
        "full_name_ar",
        "address_ar",
        "card_number",
        "issue_date",
    ],
}


class DocumentInput(BaseModel):
    doc_type: DocumentType
    image: bytes
    mime_type: str = "image/jpeg"


class ReaderResult(BaseModel):
    """What one image reader extracted from one document."""

    reader: str
    fields: dict[str, str | None] = Field(default_factory=dict)
    error: str | None = None


class FieldConsensus(BaseModel):
    name: str
    value: str | None
    agreement: float = Field(ge=0, le=1, description="Share of readers that agree with the winning value")
    candidates: dict[str, str | None] = Field(default_factory=dict, description="reader -> raw value")


class DocumentReading(BaseModel):
    doc_type: DocumentType
    fields: dict[str, FieldConsensus]
    readers_ok: int
    readers_total: int

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
    doc_type: DocumentType | None = None


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
