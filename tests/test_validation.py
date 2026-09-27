from khatti.consensus import build_reading
from khatti.kyc import mrz_td3_errors
from khatti.models import ReaderResult, ReaderStatus, Severity
from khatti.registry import DocTypeSpec, FieldSpec, Registry, default_registry
from khatti.validation import check_case, check_document

from .conftest import TODAY

REGISTRY = default_registry()


def reading(doc_type, *field_sets):
    spec = REGISTRY.get(doc_type)
    return build_reading(doc_type, spec.field_names, [ReaderResult(reader=f"r{i}", fields=f) for i, f in enumerate(field_sets)])


def check(r):
    return check_document(r, REGISTRY.get(r.doc_type), TODAY)


def codes(checks, severity=None):
    return {c.code for c in checks if severity is None or c.severity is severity}


def test_icao_specimen_mrz_is_valid():
    assert mrz_td3_errors("L898902C36UTO7408122F1204159ZE184226B<<<<<10") == []


def test_mrz_detects_corrupted_digit():
    errors = mrz_td3_errors("L898902C36UTO7408123F1204159ZE184226B<<<<<10")
    assert any("date of birth" in e for e in errors)


def test_clean_documents_pass(id_fields, passport_fields):
    nid = reading("national_id", id_fields, id_fields)
    pp = reading("passport", passport_fields, passport_fields)
    checks = check_case([nid, pp], REGISTRY, TODAY)
    assert codes(checks, Severity.WARN) == set()
    assert codes(checks, Severity.FAIL) == set()


def test_expired_is_hard_fail_only_when_readers_agree(id_fields):
    expired = {**id_fields, "expiry_date": "2020-01-01"}
    assert "document_expired" in codes(check(reading("national_id", expired, expired)), Severity.FAIL)
    # One reader says expired, another does not: could be an OCR slip -> human review, not reject.
    split = check(reading("national_id", expired, id_fields))
    assert "document_expired" in codes(split, Severity.WARN)
    assert codes(split, Severity.FAIL) == set()


def test_underage(id_fields):
    minor = {**id_fields, "date_of_birth": "2012-01-01"}
    assert "underage" in codes(check(reading("national_id", minor, minor)), Severity.FAIL)


def test_reader_disagreement_warns(id_fields):
    other = {**id_fields, "id_number": "199087654321"}
    assert "low_agreement" in codes(check(reading("national_id", id_fields, other)), Severity.WARN)


def test_bad_national_id_format(id_fields):
    assert "id_number_format" in codes(check(reading("national_id", {**id_fields, "id_number": "12345"})))


def test_mrz_vs_visual_mismatch(passport_fields):
    assert "mrz_mismatch" in codes(check(reading("passport", {**passport_fields, "passport_number": "A99999999"})))


def test_cross_document_name_mismatch(id_fields, passport_fields):
    nid = reading("national_id", id_fields)
    pp = reading("passport", {**passport_fields, "full_name_ar": "محمد جواد عباس"})
    assert "name_mismatch" in codes(check_case([nid, pp], REGISTRY, TODAY))


def test_missing_field_warns(id_fields):
    partial = {k: v for k, v in id_fields.items() if k != "mother_name_ar"}
    assert "missing_field" in codes(check(reading("national_id", partial)), Severity.WARN)


def test_extra_reader_keys_are_dropped(id_fields):
    r = reading("national_id", {**id_fields, "invented": "x"})
    assert "invented" not in r.fields


def test_unavailable_reader_is_recorded(id_fields):
    spec = REGISTRY.get("national_id")
    r = build_reading(
        "national_id",
        spec.field_names,
        [
            ReaderResult(reader="arabic-vlm", fields=id_fields),
            ReaderResult(reader="nvidia-omni", status=ReaderStatus.UNAVAILABLE, error="HTTP 503"),
        ],
    )
    assert r.readers == {"arabic-vlm": ReaderStatus.OK, "nvidia-omni": ReaderStatus.UNAVAILABLE}
    assert "reader_unavailable" in codes(check(r), Severity.INFO)


def test_registry_accepts_new_domains():
    registry = Registry()
    registry.register(
        DocTypeSpec(key="receipt", domain="cashier", label="shop receipt", fields=(FieldSpec("total_iqd", "Total"),))
    )
    r = build_reading("receipt", ["total_iqd"], [ReaderResult(reader="a", fields={"total_iqd": "5000"})])
    assert check_case([r], registry, TODAY) == []
