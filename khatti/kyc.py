"""KYC document types for Iraqi merchant onboarding (fictional "Iraqi-style" specs)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .arabic import parse_date
from .crossdoc import RuleSet
from .models import CheckResult, DocumentResult, Severity
from .registry import DocTypeSpec, FieldGroup as G, FieldSpec, Registry, Shape

DOMAIN = "kyc"
RULESET_PATH = Path(__file__).parent / "rules" / "kyc.yaml"


def F(name, description, group, label_ar, label_en, required=True, handwritten=False) -> FieldSpec:
    return FieldSpec(name, description, group, required, handwritten, label_ar, label_en)


_EXPIRY = F("expiry_date", "Expiry date as printed", G.DATES, "تاريخ النفاذ", "expiry date")
_ISSUE = F("issue_date", "Issue date as printed", G.DATES, "تاريخ الإصدار", "issue date")

NATIONAL_ID_FRONT = DocTypeSpec(
    key="national_id_front",
    domain=DOMAIN,
    label="front of an Iraqi National Card (البطاقة الوطنية), Iraqi-style specimen",
    label_ar="وجه البطاقة الوطنية",
    shape=Shape.ID1_CARD,
    fields=(
        F("full_name_ar", "Holder's full name in Arabic: given, father, grandfather (, great-grandfather)",
          G.NAMES, "الاسم الكامل", "full name"),
        F("surname_ar", "Family or tribal surname (اللقب)", G.NAMES, "اللقب", "surname", required=False),
        F("mother_name_ar", "Mother's name", G.NAMES, "اسم الأم", "mother's name"),
        F("sex", "Sex as printed", G.ENUMS, "الجنس", "sex"),
        F("blood_type", "Blood type as printed", G.ENUMS, "فصيلة الدم", "blood type", required=False),
        F("id_number", "National Card number", G.DIGITS, "الرقم الوطني", "ID number"),
    ),
)

NATIONAL_ID_BACK = DocTypeSpec(
    key="national_id_back",
    domain=DOMAIN,
    label="back of an Iraqi National Card (البطاقة الوطنية), Iraqi-style specimen",
    label_ar="ظهر البطاقة الوطنية",
    shape=Shape.ID1_CARD,
    fields=(
        F("date_of_birth", "Date of birth as printed", G.DATES, "تاريخ الولادة", "date of birth"),
        F("place_of_birth_ar", "Place of birth", G.TEXT, "محل الولادة", "place of birth"),
        _ISSUE,
        _EXPIRY,
        F("issuing_authority_ar", "Issuing authority", G.TEXT, "جهة الإصدار", "issuing authority", required=False),
        F("family_number", "Family number", G.DIGITS, "الرقم العائلي", "family number", required=False),
    ),
)

COMMERCIAL_REGISTRATION = DocTypeSpec(
    key="commercial_registration",
    domain=DOMAIN,
    label="Iraqi-style commercial registration certificate (شهادة تسجيل تجاري), fictional specimen",
    label_ar="شهادة التسجيل التجاري",
    shape=Shape.A4,
    fields=(
        F("owner_name_ar", "Owner's name", G.NAMES, "اسم المالك", "owner name", handwritten=True),
        F("business_name_ar", "Business name", G.NAMES, "الاسم التجاري", "business name", handwritten=True),
        F("registration_number", "Registration number", G.DIGITS, "رقم التسجيل", "registration number"),
        F("activity_ar", "Business activity", G.TEXT, "النشاط", "activity", required=False, handwritten=True),
        F("governorate_ar", "Governorate", G.TEXT, "المحافظة", "governorate"),
        F("address_ar", "Address", G.TEXT, "العنوان", "address", required=False, handwritten=True),
        _ISSUE,
        _EXPIRY,
    ),
)

TAX_CARD = DocTypeSpec(
    key="tax_card",
    domain=DOMAIN,
    label="Iraqi-style tax card (بطاقة ضريبية), fictional specimen",
    label_ar="البطاقة الضريبية",
    shape=Shape.ID1_CARD,
    fields=(
        F("holder_name_ar", "Holder's name", G.NAMES, "اسم المكلف", "holder name", handwritten=True),
        F("business_name_ar", "Business name", G.NAMES, "الاسم التجاري", "business name", handwritten=True),
        F("tax_number", "Tax number", G.DIGITS, "الرقم الضريبي", "tax number"),
        _ISSUE,
        _EXPIRY,
    ),
)

ALL = (NATIONAL_ID_FRONT, NATIONAL_ID_BACK, COMMERCIAL_REGISTRATION, TAX_CARD)


def lifetime_check(ruleset: RuleSet):
    """Warn when expiry - issue differs from the document's expected lifetime."""

    def check(doc: DocumentResult, today: date) -> list[CheckResult]:
        for prefix, spec in ruleset.lifetimes.items():
            if doc.doc_type == prefix or doc.doc_type.startswith(prefix + "_"):
                issue = parse_date(doc.fields.get("issue_date") and doc.fields["issue_date"].value)
                expiry = parse_date(doc.fields.get("expiry_date") and doc.fields["expiry_date"].value)
                if not issue or not expiry:
                    return []
                try:
                    expected = issue.value.replace(year=issue.value.year + int(spec["years"]))
                except ValueError:  # 29 Feb
                    expected = issue.value.replace(year=issue.value.year + int(spec["years"]), day=28)
                if abs((expiry.value - expected).days) > int(spec.get("tolerance_days", 30)):
                    return [
                        CheckResult(
                            code="unexpected_lifetime",
                            severity=Severity.WARN,
                            message=f"Expiry is not {spec['years']} years after issue",
                            slot=doc.slot,
                        )
                    ]
        return []

    return check


def register(registry: Registry, ruleset_path: Path = RULESET_PATH) -> None:
    ruleset = RuleSet.load(ruleset_path)
    registry.rulesets[DOMAIN] = ruleset
    for spec in ALL:
        if spec.key.startswith("national_id"):
            spec = DocTypeSpec(**{**spec.__dict__, "validators": (lifetime_check(ruleset),)})
        registry.register(spec)
