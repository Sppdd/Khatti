"""Deterministic validation (no LLM): field formats, enums, dates, and document-level date rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .arabic import normalize, parse_date, to_western_digits
from .crossdoc import RuleSet
from .models import CheckResult, DocumentResult, FieldStatus, Severity
from .registry import DocTypeSpec, FieldGroup, FieldSpec


@dataclass(frozen=True)
class FormatResult:
    valid: bool | None  # None: no validator applies to this field
    reason: str | None = None


def check_format(field: FieldSpec, doc_type: str, value: str | None, ruleset: RuleSet | None) -> FormatResult:
    """Value-only validation used both to pick between readers and to set invalid_format."""
    if value is None:
        return FormatResult(None)
    if field.group is FieldGroup.DATES:
        return FormatResult(True) if parse_date(value) else FormatResult(False, "unparseable date")
    if ruleset is None:
        return FormatResult(None)
    fmt = ruleset.format_for(doc_type, field.name)
    if fmt is not None:
        compact = to_western_digits(value).replace(" ", "")
        if fmt.pattern.fullmatch(compact):
            return FormatResult(True)
        return FormatResult(False, f"does not match expected format{'' if fmt.verified else ' (unverified spec)'}")
    allowed = ruleset.enum_for(doc_type, field.name)
    if allowed is not None:
        ok = normalize(value) in {normalize(a) for a in allowed}
        return FormatResult(ok, None if ok else "not an allowed value")
    return FormatResult(None)


def apply_date_rules(doc: DocumentResult, spec: DocTypeSpec, ruleset: RuleSet | None, today: date) -> list[CheckResult]:
    """expiry > today, issue <= today, issue < expiry, age >= adult. Sets field statuses in place."""
    out: list[CheckResult] = []

    def add(code: str, sev: Severity, msg: str, field: str | None = None) -> None:
        out.append(CheckResult(code=code, severity=sev, message=msg, slot=doc.slot, field=field))

    def parsed(name: str):
        f = doc.fields.get(name)
        return parse_date(f.value) if f and f.value else None

    issue, expiry, dob = parsed("issue_date"), parsed("expiry_date"), parsed("date_of_birth")
    for name, p in (("issue_date", issue), ("expiry_date", expiry), ("date_of_birth", dob)):
        if p:
            doc.fields[name].calendar = p.calendar
            doc.fields[name].calendar_converted = p.converted

    if expiry and expiry.value <= today:
        doc.fields["expiry_date"].status = FieldStatus.EXPIRED
        add("document_expired", Severity.FAIL, f"{spec.key} expired on {expiry.value.isoformat()}", "expiry_date")
    if issue and issue.value > today:
        doc.fields["issue_date"].status = FieldStatus.INVALID_FORMAT
        add("issue_in_future", Severity.WARN, "Issue date is in the future", "issue_date")
    if issue and expiry and issue.value >= expiry.value:
        add("issue_after_expiry", Severity.WARN, "Issue date is not before expiry date", "issue_date")
    if dob:
        age = today.year - dob.value.year - ((today.month, today.day) < (dob.value.month, dob.value.day))
        adult = ruleset.adult_age if ruleset else 18
        if dob.value > today:
            doc.fields["date_of_birth"].status = FieldStatus.INVALID_FORMAT
            add("dob_in_future", Severity.WARN, "Date of birth is in the future", "date_of_birth")
        elif age < adult:
            add("underage", Severity.FAIL, f"Applicant is under {adult}", "date_of_birth")
    return out
