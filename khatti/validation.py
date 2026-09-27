"""Deterministic checks. These run without any LLM and set the routing floor."""

from __future__ import annotations

import re
from datetime import date

from .arabic import normalize, parse_date, similarity, to_western_digits
from .models import EXPECTED_FIELDS, CheckResult, DocumentReading, DocumentType, Severity

MIN_FIELD_AGREEMENT = 0.67
# A hard failure is only trusted when readers agree on the value it is based on;
# otherwise it may be an OCR slip and is downgraded to a human-review warning.
TRUSTED_AGREEMENT = 0.99
NAME_MATCH = 0.85
ADULT_AGE = 18

# Unified Iraqi national ID number: 12 digits.
_IQ_NID = re.compile(r"^\d{12}$")


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def mrz_check_digit(data: str) -> int:
    """ICAO 9303 check digit (weights 7, 3, 1)."""
    total = 0
    for i, ch in enumerate(data):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch.upper()) - 55
        else:  # '<' filler
            v = 0
        total += v * (7, 3, 1)[i % 3]
    return total % 10


def mrz_td3_errors(line2: str) -> list[str]:
    """Validate the check digits of a passport (TD3) MRZ second line."""
    line2 = line2.replace(" ", "").upper()
    if len(line2) != 44:
        return [f"MRZ line 2 has {len(line2)} characters, expected 44"]
    spans = {
        "passport number": (line2[0:9], line2[9]),
        "date of birth": (line2[13:19], line2[19]),
        "expiry date": (line2[21:27], line2[27]),
        "composite": (line2[0:10] + line2[13:20] + line2[21:43], line2[43]),
    }
    return [
        f"MRZ {label} check digit mismatch"
        for label, (data, digit) in spans.items()
        if not digit.isdigit() or mrz_check_digit(data) != int(digit)
    ]


def _mrz_date(yymmdd: str, future: bool, today: date) -> date | None:
    try:
        yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    except ValueError:
        return None
    century = 2000 if (future or 2000 + yy <= today.year) else 1900
    try:
        return date(century + yy, mm, dd)
    except ValueError:
        return None


def _hard(reading: DocumentReading, field: str) -> Severity:
    f = reading.fields.get(field)
    return Severity.FAIL if f and f.agreement >= TRUSTED_AGREEMENT else Severity.WARN


def check_document(reading: DocumentReading, today: date) -> list[CheckResult]:
    dt = reading.doc_type
    out: list[CheckResult] = []

    def add(code: str, sev: Severity, msg: str) -> None:
        out.append(CheckResult(code=code, severity=sev, message=msg, doc_type=dt))

    if reading.readers_ok == 0:
        add("no_reader_output", Severity.WARN, "No image reader returned a result")
        return out
    if reading.readers_ok < reading.readers_total:
        add("reader_failed", Severity.INFO, f"{reading.readers_total - reading.readers_ok} reader(s) failed")

    for name in EXPECTED_FIELDS[dt]:
        f = reading.fields.get(name)
        if not f or not f.value:
            add("missing_field", Severity.WARN, f"Field '{name}' could not be read")
        elif f.agreement < MIN_FIELD_AGREEMENT:
            add("low_agreement", Severity.WARN, f"Readers disagree on '{name}' (agreement {f.agreement:.2f})")

    if dt is DocumentType.NATIONAL_ID and (nid := reading.value("id_number")):
        if not _IQ_NID.match(to_western_digits(nid).replace(" ", "")):
            add("id_number_format", Severity.WARN, "National ID number is not 12 digits")

    if (raw := reading.value("expiry_date")) is not None:
        exp = parse_date(raw)
        if exp is None:
            add("date_unparseable", Severity.WARN, f"Cannot parse expiry date '{raw}'")
        elif exp < today:
            add("document_expired", _hard(reading, "expiry_date"), f"Document expired on {exp.isoformat()}")

    if (raw := reading.value("date_of_birth")) is not None:
        dob = parse_date(raw)
        if dob is None:
            add("date_unparseable", Severity.WARN, f"Cannot parse date of birth '{raw}'")
        elif dob > today:
            add("dob_in_future", Severity.WARN, "Date of birth is in the future")
        elif _age(dob, today) < ADULT_AGE:
            add("underage", _hard(reading, "date_of_birth"), f"Applicant is under {ADULT_AGE}")

    if dt is DocumentType.PASSPORT and (line2 := reading.value("mrz_line2")):
        for err in mrz_td3_errors(line2):
            add("mrz_checksum", Severity.WARN, err)
        line2 = line2.replace(" ", "").upper()
        if len(line2) == 44:
            pno = reading.value("passport_number")
            if pno and normalize(pno) != normalize(line2[0:9].rstrip("<")):
                add("mrz_mismatch", Severity.WARN, "Passport number differs between MRZ and visual zone")
            dob = parse_date(reading.value("date_of_birth"))
            if dob and dob != _mrz_date(line2[13:19], future=False, today=today):
                add("mrz_mismatch", Severity.WARN, "Date of birth differs between MRZ and visual zone")

    return out


def check_cross_documents(readings: list[DocumentReading]) -> list[CheckResult]:
    out: list[CheckResult] = []
    for i, a in enumerate(readings):
        for b in readings[i + 1 :]:
            na, nb = a.value("full_name_ar"), b.value("full_name_ar")
            if na and nb and similarity(na, nb) < NAME_MATCH:
                out.append(
                    CheckResult(
                        code="name_mismatch",
                        severity=Severity.WARN,
                        message=f"Arabic name differs between {a.doc_type.value} and {b.doc_type.value}",
                    )
                )
            da, db = parse_date(a.value("date_of_birth")), parse_date(b.value("date_of_birth"))
            if da and db and da != db:
                out.append(
                    CheckResult(
                        code="dob_mismatch",
                        severity=Severity.WARN,
                        message=f"Date of birth differs between {a.doc_type.value} and {b.doc_type.value}",
                    )
                )
    return out
