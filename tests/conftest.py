from datetime import date

import pytest

from khatti.kyc import mrz_check_digit

TODAY = date(2026, 9, 27)


def make_mrz_line2(passport_no: str, dob: str, sex: str, expiry: str, nationality: str = "IRQ") -> str:
    pno = passport_no.ljust(9, "<")
    opt = "<" * 14
    line = (
        pno + str(mrz_check_digit(pno)) + nationality
        + dob + str(mrz_check_digit(dob)) + sex
        + expiry + str(mrz_check_digit(expiry))
        + opt + str(mrz_check_digit(opt))
    )
    return line + str(mrz_check_digit(line[0:10] + line[13:20] + line[21:43]))


@pytest.fixture
def id_fields() -> dict:
    return {
        "full_name_ar": "علي حسين كاظم",
        "mother_name_ar": "زينب جاسم",
        "date_of_birth": "1990-05-14",
        "id_number": "199012345678",
        "expiry_date": "2031-01-01",
        "sex": "M",
    }


@pytest.fixture
def passport_fields() -> dict:
    return {
        "full_name_ar": "علي حسين كاظم",
        "full_name_en": "ALI HUSSEIN KADHIM",
        "date_of_birth": "1990-05-14",
        "passport_number": "A12345678",
        "expiry_date": "2030-03-01",
        "sex": "M",
        "mrz_line1": "P<IRQKADHIM<<ALI<HUSSEIN<<<<<<<<<<<<<<<<<<<<",
        "mrz_line2": make_mrz_line2("A12345678", "900514", "M", "300301"),
    }
