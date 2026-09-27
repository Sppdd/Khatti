from khatti.consensus import build_reading
from khatti.models import DocumentType, ReaderResult, Severity
from khatti.validation import check_cross_documents, check_document, mrz_td3_errors

from .conftest import TODAY


def reading(doc_type, *field_sets):
    return build_reading(doc_type, [ReaderResult(reader=f"r{i}", fields=f) for i, f in enumerate(field_sets)])


def codes(checks, severity=None):
    return {c.code for c in checks if severity is None or c.severity is severity}


def test_icao_specimen_mrz_is_valid():
    assert mrz_td3_errors("L898902C36UTO7408122F1204159ZE184226B<<<<<10") == []


def test_mrz_detects_corrupted_digit():
    errors = mrz_td3_errors("L898902C36UTO7408123F1204159ZE184226B<<<<<10")
    assert any("date of birth" in e for e in errors)


def test_clean_documents_pass(id_fields, passport_fields):
    nid = reading(DocumentType.NATIONAL_ID, id_fields, id_fields)
    pp = reading(DocumentType.PASSPORT, passport_fields, passport_fields)
    checks = check_document(nid, TODAY) + check_document(pp, TODAY) + check_cross_documents([nid, pp])
    assert codes(checks, Severity.WARN) == set()
    assert codes(checks, Severity.FAIL) == set()


def test_expired_is_hard_fail_only_when_readers_agree(id_fields):
    expired = {**id_fields, "expiry_date": "2020-01-01"}
    assert "document_expired" in codes(check_document(reading(DocumentType.NATIONAL_ID, expired, expired), TODAY), Severity.FAIL)
    # One reader says expired, another does not: could be an OCR slip -> human review, not reject.
    split = check_document(reading(DocumentType.NATIONAL_ID, expired, id_fields), TODAY)
    assert "document_expired" in codes(split, Severity.WARN)
    assert codes(split, Severity.FAIL) == set()


def test_underage(id_fields):
    minor = {**id_fields, "date_of_birth": "2012-01-01"}
    assert "underage" in codes(check_document(reading(DocumentType.NATIONAL_ID, minor, minor), TODAY), Severity.FAIL)


def test_reader_disagreement_warns(id_fields):
    other = {**id_fields, "id_number": "199087654321"}
    checks = check_document(reading(DocumentType.NATIONAL_ID, id_fields, other), TODAY)
    assert "low_agreement" in codes(checks, Severity.WARN)


def test_bad_national_id_format(id_fields):
    bad = {**id_fields, "id_number": "12345"}
    assert "id_number_format" in codes(check_document(reading(DocumentType.NATIONAL_ID, bad), TODAY))


def test_mrz_vs_visual_mismatch(passport_fields):
    wrong = {**passport_fields, "passport_number": "A99999999"}
    assert "mrz_mismatch" in codes(check_document(reading(DocumentType.PASSPORT, wrong), TODAY))


def test_cross_document_name_mismatch(id_fields, passport_fields):
    nid = reading(DocumentType.NATIONAL_ID, id_fields)
    pp = reading(DocumentType.PASSPORT, {**passport_fields, "full_name_ar": "محمد جواد عباس"})
    assert "name_mismatch" in codes(check_cross_documents([nid, pp]))


def test_missing_field_warns(id_fields):
    partial = {k: v for k, v in id_fields.items() if k != "mother_name_ar"}
    assert "missing_field" in codes(check_document(reading(DocumentType.NATIONAL_ID, partial), TODAY), Severity.WARN)
