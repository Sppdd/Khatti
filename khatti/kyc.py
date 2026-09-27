"""KYC document types for Iraqi onboarding: schemas, validators, cross-document rules."""

from __future__ import annotations

import re
from datetime import date

from .arabic import normalize, parse_date, similarity, to_western_digits
from .models import CheckResult, DocumentReading, Severity
from .registry import DocTypeSpec, FieldSpec, Registry

DOMAIN = "kyc"
NAME_MATCH = 0.85
ADULT_AGE = 18
# A hard failure is only trusted when readers agree on the value it is based on;
# otherwise it may be an OCR slip and is downgraded to a human-review warning.
TRUSTED_AGREEMENT = 0.99

# Unified Iraqi national ID number: 12 digits.
_IQ_NID = re.compile(r"^\d{12}$")

_NAME_AR = FieldSpec("full_name_ar", "Holder's full name in Arabic script, as printed")
_DOB = FieldSpec("date_of_birth", "Date of birth, YYYY-MM-DD")
_SEX = FieldSpec("sex", "M or F")
_EXPIRY = FieldSpec("expiry_date", "Expiry date, YYYY-MM-DD")


# ---------------------------------------------------------------- helpers

def _check(reading: DocumentReading, code: str, severity: Severity, message: str) -> CheckResult:
    return CheckResult(code=code, severity=severity, message=message, doc_type=reading.doc_type)


def _hard(reading: DocumentReading, field: str) -> Severity:
    f = reading.fields.get(field)
    return Severity.FAIL if f and f.agreement >= TRUSTED_AGREEMENT else Severity.WARN


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


def _mrz_dob(yymmdd: str, today: date) -> date | None:
    try:
        yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    except ValueError:
        return None
    century = 2000 if 2000 + yy <= today.year else 1900
    try:
        return date(century + yy, mm, dd)
    except ValueError:
        return None


# ---------------------------------------------------------------- validators

def check_expiry(reading: DocumentReading, today: date) -> list[CheckResult]:
    raw = reading.value("expiry_date")
    if raw is None:
        return []
    exp = parse_date(raw)
    if exp is None:
        return [_check(reading, "date_unparseable", Severity.WARN, f"Cannot parse expiry date '{raw}'")]
    if exp < today:
        return [_check(reading, "document_expired", _hard(reading, "expiry_date"), f"Document expired on {exp.isoformat()}")]
    return []


def check_age(reading: DocumentReading, today: date) -> list[CheckResult]:
    raw = reading.value("date_of_birth")
    if raw is None:
        return []
    dob = parse_date(raw)
    if dob is None:
        return [_check(reading, "date_unparseable", Severity.WARN, f"Cannot parse date of birth '{raw}'")]
    if dob > today:
        return [_check(reading, "dob_in_future", Severity.WARN, "Date of birth is in the future")]
    if _age(dob, today) < ADULT_AGE:
        return [_check(reading, "underage", _hard(reading, "date_of_birth"), f"Applicant is under {ADULT_AGE}")]
    return []


def check_national_id_number(reading: DocumentReading, today: date) -> list[CheckResult]:
    nid = reading.value("id_number")
    if nid and not _IQ_NID.match(to_western_digits(nid).replace(" ", "")):
        return [_check(reading, "id_number_format", Severity.WARN, "National ID number is not 12 digits")]
    return []


def check_passport_mrz(reading: DocumentReading, today: date) -> list[CheckResult]:
    line2 = reading.value("mrz_line2")
    if not line2:
        return []
    out = [_check(reading, "mrz_checksum", Severity.WARN, e) for e in mrz_td3_errors(line2)]
    line2 = line2.replace(" ", "").upper()
    if len(line2) == 44:
        pno = reading.value("passport_number")
        if pno and normalize(pno) != normalize(line2[0:9].rstrip("<")):
            out.append(_check(reading, "mrz_mismatch", Severity.WARN, "Passport number differs between MRZ and visual zone"))
        dob = parse_date(reading.value("date_of_birth"))
        if dob and dob != _mrz_dob(line2[13:19], today):
            out.append(_check(reading, "mrz_mismatch", Severity.WARN, "Date of birth differs between MRZ and visual zone"))
    return out


def check_same_person(readings: list[DocumentReading]) -> list[CheckResult]:
    out: list[CheckResult] = []
    for i, a in enumerate(readings):
        for b in readings[i + 1 :]:
            pair = f"{a.doc_type} and {b.doc_type}"
            na, nb = a.value("full_name_ar"), b.value("full_name_ar")
            if na and nb and similarity(na, nb) < NAME_MATCH:
                out.append(CheckResult(code="name_mismatch", severity=Severity.WARN, message=f"Arabic name differs between {pair}"))
            da, db = parse_date(a.value("date_of_birth")), parse_date(b.value("date_of_birth"))
            if da and db and da != db:
                out.append(CheckResult(code="dob_mismatch", severity=Severity.WARN, message=f"Date of birth differs between {pair}"))
    return out


# ---------------------------------------------------------------- registry entries

NATIONAL_ID = DocTypeSpec(
    key="national_id",
    domain=DOMAIN,
    label="Iraqi unified national ID card (البطاقة الوطنية الموحدة)",
    fields=(
        _NAME_AR,
        FieldSpec("mother_name_ar", "Mother's name in Arabic script"),
        _DOB,
        FieldSpec("id_number", "12-digit national ID number"),
        _EXPIRY,
        _SEX,
    ),
    validators=(check_national_id_number, check_expiry, check_age),
)

PASSPORT = DocTypeSpec(
    key="passport",
    domain=DOMAIN,
    label="Iraqi passport (جواز سفر عراقي) data page",
    fields=(
        _NAME_AR,
        FieldSpec("full_name_en", "Name in Latin script, as printed"),
        _DOB,
        FieldSpec("passport_number", "Passport number"),
        _EXPIRY,
        _SEX,
        FieldSpec("mrz_line1", "First machine-readable line, verbatim including '<'"),
        FieldSpec("mrz_line2", "Second machine-readable line, verbatim including '<'"),
    ),
    validators=(check_passport_mrz, check_expiry, check_age),
)

RESIDENCE_CARD = DocTypeSpec(
    key="residence_card",
    domain=DOMAIN,
    label="Iraqi residence card (بطاقة السكن)",
    fields=(
        _NAME_AR,
        FieldSpec("address_ar", "Address in Arabic script"),
        FieldSpec("card_number", "Card number"),
        FieldSpec("issue_date", "Issue date, YYYY-MM-DD"),
    ),
)


def register(registry: Registry) -> None:
    for spec in (NATIONAL_ID, PASSPORT, RESIDENCE_CARD):
        registry.register(spec)
    registry.add_cross_rule(DOMAIN, check_same_person)
