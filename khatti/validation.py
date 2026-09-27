"""Deterministic checks. These run without any LLM and set the routing floor."""

from __future__ import annotations

from datetime import date

from .models import CheckResult, DocumentReading, ReaderStatus, Severity
from .registry import DocTypeSpec, Registry

MIN_FIELD_AGREEMENT = 0.67


def check_document(reading: DocumentReading, spec: DocTypeSpec, today: date) -> list[CheckResult]:
    """Generic reading-quality checks, then the document type's own validators."""
    out: list[CheckResult] = []

    def add(code: str, sev: Severity, msg: str) -> None:
        out.append(CheckResult(code=code, severity=sev, message=msg, doc_type=reading.doc_type))

    for reader, status in reading.readers.items():
        if status is not ReaderStatus.OK:
            add(f"reader_{status.value}", Severity.INFO, f"Reader '{reader}' {status.value}")

    if reading.readers_ok == 0:
        add("no_reader_output", Severity.WARN, "No image reader returned a result")
        return out

    for f in spec.fields:
        c = reading.fields.get(f.name)
        if not c or not c.value:
            if f.required:
                add("missing_field", Severity.WARN, f"Field '{f.name}' could not be read")
        elif c.agreement < MIN_FIELD_AGREEMENT:
            add("low_agreement", Severity.WARN, f"Readers disagree on '{f.name}' (agreement {c.agreement:.2f})")

    for validator in spec.validators:
        out += validator(reading, today)
    return out


def check_case(readings: list[DocumentReading], registry: Registry, today: date) -> list[CheckResult]:
    checks = [c for r in readings for c in check_document(r, registry.get(r.doc_type), today)]
    by_domain: dict[str, list[DocumentReading]] = {}
    for r in readings:
        if r.readers_ok:
            by_domain.setdefault(registry.get(r.doc_type).domain, []).append(r)
    for domain, group in by_domain.items():
        for rule in registry.cross_rules.get(domain, []):
            checks += rule(group)
    return checks
