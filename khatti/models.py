"""Shared schemas for the document pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# The illegible-character marker readers are told to emit.
ILLEGIBLE = "?"


class DocumentInput(BaseModel):
    slot: str  # what the session asked for, e.g. "national_id_front"
    image: bytes
    mime_type: str = "image/jpeg"


# ---------------------------------------------------------------- perception

class ReaderStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"  # endpoint down / unreachable: expected, e.g. GPU endpoint stopped
    ERROR = "error"  # reachable but the reply was unusable


class Line(BaseModel):
    line_id: str
    text: str
    bbox: list[float] | None = None  # [x0, y0, x1, y1], relative 0..1
    conf: float | None = None


class Transcript(BaseModel):
    """One reader's reading of one image (one sample)."""

    reader: str
    sample: int = 0
    status: ReaderStatus = ReaderStatus.OK
    caption: str = ""  # one-line description of the document, used for classification
    lines: list[Line] = Field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------- structuring

class SourceSpan(BaseModel):
    line_id: str
    start: int
    end: int


class ExtractedValue(BaseModel):
    """A structurer's claim about one field, before it is verified against the lines."""

    value: str | None = None
    spans: list[SourceSpan] = Field(default_factory=list)
    reason: str | None = None  # why value is null: not_present | illegible | ...


class VerifiedValue(BaseModel):
    value: str | None
    source_line_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    raw_partial: str | None = None  # partial reading of an illegible field, reviewer-only
    bbox: list[float] | None = None  # union of the cited lines' boxes (relative to the original photo)
    line_conf: float | None = None  # mean token confidence of the cited lines, when logprobs exist


# ---------------------------------------------------------------- results

class FieldStatus(str, Enum):
    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    UNREADABLE = "unreadable"
    MISMATCH = "mismatch"
    INVALID_FORMAT = "invalid_format"
    EXPIRED = "expired"
    NOT_PRESENT = "not_present"


class FieldResult(BaseModel):
    name: str
    value: str | None
    status: FieldStatus
    confidence: float = Field(ge=0, le=1)
    reason: str | None = None
    raw_partial: str | None = None
    calendar: str | None = None  # for dates: gregorian | hijri
    calendar_converted: bool = False
    bbox: list[float] | None = None  # where the field is on the original photo, if a reader said
    candidates: dict[str, str | None] = Field(default_factory=dict, description="reader#sample -> verified value")
    sources: dict[str, list[str]] = Field(default_factory=dict, description="reader#sample -> line ids")
    features: dict[str, float] = Field(default_factory=dict)


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"  # blocks auto-pass
    FAIL = "fail"  # blocks auto-pass and is a strong reason


class CheckResult(BaseModel):
    code: str
    severity: Severity
    message: str
    slot: str | None = None
    field: str | None = None


class QualityReport(BaseModel):
    width: int
    height: int
    metrics: dict[str, float]
    issues: list[str] = Field(default_factory=list)  # machine codes, e.g. "blur", "corner_cut"
    guidance_ar: list[str] = Field(default_factory=list)
    guidance_en: list[str] = Field(default_factory=list)

    @property
    def retake(self) -> bool:
        return bool(self.issues)


class DocumentResult(BaseModel):
    slot: str
    doc_type: str  # what the document was classified as
    classification_confidence: float = 1.0
    quality: QualityReport | None = None
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    readers: dict[str, ReaderStatus] = Field(default_factory=dict)
    image_hashes: list[str] = Field(default_factory=list)


class CrossCheck(BaseModel):
    rule: str
    status: str  # match | partial_match | mismatch | transliteration_match | pass | fail | skipped
    score: float | None = None
    detail: str = ""


class RetakeRequest(BaseModel):
    """A specific, actionable retake message for the person holding the phone."""

    slot: str
    field: str | None = None
    reason: str  # glare | thumb | cut_off | blur | unreadable | <quality issue code>
    message_ar: str
    message_en: str


class Outcome(str, Enum):
    AUTO_PASS = "auto_pass"
    HUMAN_REVIEW = "human_review"


class Decision(BaseModel):
    outcome: Outcome
    session_confidence: float
    reasons: list[str] = Field(default_factory=list)
    decided_by: str = "rules"


class SessionResult(BaseModel):
    decision: Decision
    documents: list[DocumentResult]
    cross_checks: list[CrossCheck] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    review_summary: list[str] = Field(default_factory=list)
    retake_requests: list[RetakeRequest] = Field(default_factory=list)
    pipeline_version: str = ""
