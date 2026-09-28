from datetime import date

from khatti.arabic import cer, jaro_winkler, match_names, normalize, parse_date, similarity


def test_normalize_unifies_variants_for_matching():
    assert normalize("أَحْمَد") == normalize("احمد")
    assert normalize("فاطمة") == normalize("فاطمه")
    assert normalize("مصطفى") == normalize("مصطفي")
    assert normalize("عـــلي") == normalize("علي")
    assert normalize("١٩٩٠١٢٣٤٥٦٧٨") == "199012345678"
    assert normalize("۱۲۳") == "123"


def test_abd_compounds_are_joined():
    assert normalize("عبد الله") == normalize("عبدالله")
    assert normalize("محمد عبد الرحمن") == normalize("محمد عبدالرحمن")


def test_kurdish_letters_are_not_folded():
    assert normalize("کەریم") == "کەریم"  # Kurdish: keheh and yeh kept
    assert normalize("کريم") == "كريم"  # Arabic context: keheh folded


def test_dates():
    assert parse_date("2031/04/12").value == date(2031, 4, 12)
    assert parse_date("12/04/2031").value == date(2031, 4, 12)
    assert parse_date("١٢/٠٤/٢٠٣١").value == date(2031, 4, 12)
    assert parse_date("12 نيسان 2031").value == date(2031, 4, 12)
    assert parse_date("3 تشرين الثاني 1990").value == date(1990, 11, 3)
    assert parse_date("5 أيلول 2030").value == date(2030, 9, 5)
    assert parse_date("garbage") is None
    assert parse_date("2031/13/40") is None


def test_hijri_dates_are_detected_and_converted():
    for text in ("١ رمضان ١٤٤٥ هـ", "1445/09/01", "01/09/1445 هـ"):
        p = parse_date(text)
        assert p.calendar == "hijri" and p.converted and p.value == date(2024, 3, 11), text


def test_names_exact_and_variant():
    assert match_names("علي حسين كاظم", "على حسين كاظم").status == "match"
    assert match_names("عبد الله حسن", "عبدالله حسن").status == "match"


def test_missing_grandfather_is_partial_not_match():
    m = match_names("مثال أحمد جاسم محمد", "مثال احمد محمد")
    assert m.status == "partial_match"
    assert "جاسم" in m.detail


def test_given_name_must_match_exactly():
    assert match_names("علي حسين كاظم", "عمر حسين كاظم").status == "mismatch"


def test_order_is_preserved():
    assert match_names("علي حسين كاظم", "علي كاظم حسين").status in {"partial_match", "mismatch"}
    assert match_names("علي حسين كاظم", "علي كاظم حسين").status != "match"


def test_transliteration_only_match_is_flagged():
    m = match_names("علي حسين كاظم", "Ali Hussein Kadhim")
    assert m.status == "transliteration_match"
    assert match_names("علي حسين كاظم", "Omar Hussein Kadhim").status == "mismatch"


def test_similarity_and_cer():
    assert similarity("علي حسين", "على حسين") == 1.0
    assert similarity("علي حسين", "محمد جواد") < 0.5
    assert cer("علي", "علي") == 0.0
    assert cer("علي", "على") == 0.0  # normalised
    assert 0 < cer("كاظم", "كاظ") < 0.5
    assert jaro_winkler("martha", "marhta") > 0.95
