from datetime import date

from khatti.arabic import normalize, parse_date, similarity


def test_normalize_unifies_letter_variants_and_diacritics():
    assert normalize("أَحْمَد") == normalize("احمد")
    assert normalize("فاطمة") == normalize("فاطمه")
    assert normalize("مصطفى") == normalize("مصطفي")
    assert normalize("عـــلي") == normalize("علي")


def test_normalize_converts_eastern_digits():
    assert normalize("١٩٩٠١٢٣٤٥٦٧٨") == "199012345678"


def test_parse_date_layouts():
    assert parse_date("1990-05-14") == date(1990, 5, 14)
    assert parse_date("14/05/1990") == date(1990, 5, 14)
    assert parse_date("١٤/٠٥/١٩٩٠") == date(1990, 5, 14)
    assert parse_date("1990-13-01") is None
    assert parse_date("garbage") is None


def test_similarity():
    assert similarity("علي حسين", "علي حسين") == 1.0
    assert similarity("علي حسين", "على حسين") == 1.0  # ى/ي
    assert similarity("علي حسين", "محمد جواد") < 0.5
    assert similarity("", "x") == 0.0
