"""Fictional "Iraqi-style" identities and onboarding sessions with deliberate variants.

Everything here is invented. Names are combinations of common given names, so a small
blocklist keeps well-known public figures' name combinations out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from khatti.arabic import normalize

MALE = [
    "محمد", "علي", "حسين", "حسن", "أحمد", "عمر", "مصطفى", "عباس", "جاسم", "كاظم", "حيدر", "سجاد",
    "مرتضى", "عبد الله", "عبد الرحمن", "يوسف", "إبراهيم", "خالد", "سعد", "ياسر", "قاسم", "مهدي", "رضا",
    "زيد", "سلام", "ماجد", "نزار", "وليد", "طارق", "رعد", "باسم", "عادل", "صباح", "فلاح", "ضياء", "أيمن",
    "منتظر", "أنور", "هاشم", "جعفر", "سالم", "كريم", "لؤي", "أسامة", "بشير", "نبيل", "هادي", "عدنان",
]
FEMALE = [
    "فاطمة", "زينب", "مريم", "نور", "سارة", "رقية", "هدى", "زهراء", "آمنة", "إيمان", "رنا", "شيماء",
    "سجى", "دعاء", "بتول", "أسماء", "حوراء", "خديجة", "سلمى", "ليلى", "رحاب", "وفاء", "نادية", "سهى",
]
SURNAMES = [
    "الجبوري", "العبيدي", "الموسوي", "الحسيني", "التميمي", "الربيعي", "الدليمي", "الشمري", "الزبيدي",
    "الخزرجي", "العزاوي", "الساعدي", "الكعبي", "اللامي", "البياتي", "النعيمي", "الطائي", "الحمداني",
    "السامرائي", "الموصلي", "العاني", "الراوي", "الكبيسي", "الجنابي",
]
GOVERNORATES = [
    "بغداد", "نينوى", "البصرة", "أربيل", "السليمانية", "دهوك", "كركوك", "الأنبار", "ديالى", "صلاح الدين",
    "بابل", "كربلاء", "النجف", "واسط", "ميسان", "ذي قار", "المثنى", "القادسية",
]
BUSINESS_KINDS = [
    ("أسواق", "بيع المواد الغذائية بالمفرد"), ("صيدلية", "بيع الأدوية"), ("مخبز", "صناعة الخبز"),
    ("مكتبة", "بيع القرطاسية"), ("محلات", "بيع الملابس"), ("مركز", "صيانة الهواتف"),
    ("مطعم", "تقديم الوجبات"), ("معرض", "بيع الأثاث"),
]
BUSINESS_WORDS = ["النور", "الأمل", "الرافدين", "دجلة", "الفرات", "الحدباء", "السلام", "الوفاء", "البركة",
                  "الربيع", "الياسمين", "الواحة", "الضياء", "الأصالة", "الغد"]
STREETS = ["شارع الجامعة", "حي الزهور", "حي المنصور", "شارع الكورنيش", "حي الجامعة", "شارع السوق", "حي النصر"]

# Given-name + father-name pairs of well-known public figures (kept out of the data).
BLOCKLIST = {("صدام", "حسين"), ("نوري", "كامل"), ("حيدر", "جواد"), ("مصطفى", "عبد اللطيف"), ("محمد", "شياع"),
             ("عادل", "عبد المهدي"), ("برهم", "أحمد"), ("مقتدى", "محمد"), ("علي", "السيستاني")}

# Session-level variants. "human" = a correct system must route this session to a person.
VARIANTS = {
    "clean": False,
    "license_missing_grandfather": True,   # partial name match
    "alef_variant": False,                 # أحمد vs احمد: normalisation must match
    "ta_marbuta_variant": False,           # فاطمة vs فاطمه
    "swapped_business_name": True,         # license vs tax business names differ
    "expired_license": True,
    "different_person": True,              # tax card belongs to someone else
    "hijri_dates": False,                  # license dates printed in Hijri
    "arabic_indic_digits": False,          # digits printed ٠-٩
    "underage": True,
}


@dataclass
class Person:
    given: str
    father: str
    grandfather: str
    great_grandfather: str
    surname: str
    mother: str
    sex: str  # ذكر | أنثى
    dob: date
    birthplace: str

    @property
    def four_part(self) -> str:
        return f"{self.given} {self.father} {self.grandfather} {self.great_grandfather}"

    @property
    def three_part(self) -> str:
        return f"{self.given} {self.father} {self.grandfather}"


@dataclass
class Session:
    session_id: str
    identity_id: str
    variants: list[str]
    human_expected: bool
    documents: dict[str, dict[str, str | None]]  # slot -> field -> value exactly as printed
    handwritten: dict[str, list[str]] = field(default_factory=dict)
    digits: str = "western"


def _person(rng: random.Random, as_of: date, adult: bool = True) -> Person:
    while True:
        female = rng.random() < 0.35
        given = rng.choice(FEMALE if female else MALE)
        father, grandfather, great = rng.choice(MALE), rng.choice(MALE), rng.choice(MALE)
        if (given, father) not in BLOCKLIST and len({given, father, grandfather}) == 3:
            break
    age = rng.randint(19, 66) if adult else rng.randint(12, 16)
    dob = as_of - timedelta(days=age * 365 + rng.randint(0, 364))
    return Person(given, father, grandfather, great, rng.choice(SURNAMES),
                  f"{rng.choice(FEMALE)} {rng.choice(MALE)}", "أنثى" if female else "ذكر", dob, rng.choice(GOVERNORATES))


def _digits(n: int, rng: random.Random) -> str:
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(n - 1))


def _alnum(n: int, rng: random.Random) -> str:
    return "".join(rng.choice("0123456789ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(n))


def _fmt(d: date) -> str:
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"


def _hijri(d: date) -> str:
    from hijridate import Gregorian

    h = Gregorian(d.year, d.month, d.day).to_hijri()
    return f"{h.year:04d}/{h.month:02d}/{h.day:02d} هـ"


_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _variant_alef(name: str) -> str:
    return name.replace("أ", "ا").replace("إ", "ا")


def _variant_ta(name: str) -> str:
    return name.replace("ة", "ه")


def make_session(rng: random.Random, as_of: date, index: int, variants: list[str]) -> Session:
    underage = "underage" in variants
    p = _person(rng, as_of, adult=not underage)
    kind, activity = rng.choice(BUSINESS_KINDS)
    business = f"{kind} {rng.choice(BUSINESS_WORDS)}"

    id_issue = as_of - timedelta(days=rng.randint(200, 9 * 365))
    id_expiry = id_issue.replace(year=id_issue.year + 10) if not (id_issue.month == 2 and id_issue.day == 29) else id_issue + timedelta(days=3652)
    lic_issue = as_of - timedelta(days=rng.randint(30, 700))
    lic_expiry = lic_issue + timedelta(days=3 * 365)
    if "expired_license" in variants:
        lic_issue = as_of - timedelta(days=4 * 365)
        lic_expiry = as_of - timedelta(days=rng.randint(10, 300))
    tax_issue = as_of - timedelta(days=rng.randint(30, 500))
    tax_expiry = tax_issue + timedelta(days=2 * 365)

    owner = p.four_part
    if "license_missing_grandfather" in variants:
        owner = f"{p.given} {p.father} {p.great_grandfather}"
    if "alef_variant" in variants:
        owner = _variant_alef(owner) if owner != _variant_alef(owner) else owner
    if "ta_marbuta_variant" in variants:
        owner = _variant_ta(owner)
    holder = p.four_part
    if "different_person" in variants:
        other = _person(rng, as_of)
        holder = other.four_part
    tax_business = business
    if "swapped_business_name" in variants:
        while normalize(tax_business) == normalize(business):
            tax_business = f"{rng.choice(BUSINESS_KINDS)[0]} {rng.choice(BUSINESS_WORDS)}"

    date_fmt = _hijri if "hijri_dates" in variants else _fmt
    docs = {
        "national_id_front": {
            "full_name_ar": p.four_part, "surname_ar": p.surname, "mother_name_ar": p.mother, "sex": p.sex,
            "blood_type": rng.choice(["A+", "B+", "O+", "AB+", "O-", "A-"]), "id_number": _digits(12, rng),
        },
        "national_id_back": {
            "date_of_birth": _fmt(p.dob), "place_of_birth_ar": p.birthplace, "issue_date": _fmt(id_issue),
            "expiry_date": _fmt(id_expiry), "issuing_authority_ar": f"دائرة الأحوال المدنية النموذجية - {p.birthplace}",
            "family_number": _alnum(18, rng),
        },
        "commercial_registration": {
            "owner_name_ar": owner, "business_name_ar": business,
            "registration_number": f"{rng.randint(1, 18):02d}/{_digits(6, rng)}", "activity_ar": activity,
            "governorate_ar": p.birthplace, "address_ar": f"{p.birthplace} - {rng.choice(STREETS)}",
            "issue_date": date_fmt(lic_issue), "expiry_date": date_fmt(lic_expiry),
        },
        "tax_card": {
            "holder_name_ar": holder, "business_name_ar": tax_business, "tax_number": _digits(9, rng),
            "issue_date": _fmt(tax_issue), "expiry_date": _fmt(tax_expiry),
        },
    }
    digits = "western"
    if "arabic_indic_digits" in variants:
        digits = "arabic_indic"
        for slot, fields in docs.items():
            for k, v in fields.items():
                if v and any(ch.isdigit() for ch in v) and k not in ("blood_type", "family_number"):
                    fields[k] = v.translate(_INDIC)
    handwritten = {
        "commercial_registration": ["owner_name_ar", "business_name_ar", "activity_ar", "address_ar"],
        "tax_card": ["holder_name_ar", "business_name_ar"],
    }
    return Session(
        session_id=f"syn{index:05d}",
        identity_id=f"idn{index:05d}",
        variants=variants,
        human_expected=any(VARIANTS[v] for v in variants),
        documents=docs,
        handwritten=handwritten,
        digits=digits,
    )


def plan_variants(rng: random.Random, n: int) -> list[list[str]]:
    """About 40% clean, the rest one or two variants, every variant well represented."""
    names = [v for v in VARIANTS if v != "clean"]
    out = []
    for i in range(n):
        if i % 5 in (0, 1):
            out.append(["clean"])
        else:
            k = 1 if rng.random() < 0.7 else 2
            out.append(sorted(rng.sample(names, k)))
    return out
