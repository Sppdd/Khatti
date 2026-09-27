import asyncio

import pytest
from fastapi.testclient import TestClient

from khatti.api import create_app
from khatti.consensus import merge_field
from khatti.models import Decision, DocumentInput, DocumentType
from khatti.pipeline import KycPipeline
from khatti.readers import StaticReader

from .conftest import TODAY


class FakeRouter:
    name = "fake-nemotron"

    def __init__(self, decision=Decision.APPROVE, fail=False):
        self.decision, self.fail, self.calls = decision, fail, 0

    async def decide(self, payload):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.decision, "model says so"


def docs():
    return [
        DocumentInput(doc_type=DocumentType.NATIONAL_ID, image=b"img1"),
        DocumentInput(doc_type=DocumentType.PASSPORT, image=b"img2"),
    ]


def readers(id_fields, passport_fields, second_id=None):
    return [
        StaticReader("nvidia-vlm", {"national_id": id_fields, "passport": passport_fields}),
        StaticReader("arabic-vlm", {"national_id": second_id or id_fields, "passport": passport_fields}),
    ]


def run(pipeline):
    return asyncio.run(pipeline.run(docs(), today=TODAY))


def test_merge_field_majority_and_agreement():
    f = merge_field("name", {"a": "علي", "b": "على", "c": "عمر"})
    assert f.value in ("علي", "على")
    assert f.agreement == pytest.approx(2 / 3)
    assert merge_field("x", {"a": None, "b": None}).agreement == 0.0


def test_clean_case_approved(id_fields, passport_fields):
    router = FakeRouter()
    result = run(KycPipeline(readers(id_fields, passport_fields), router))
    assert result.decision is Decision.APPROVE
    assert result.router == "fake-nemotron"
    assert result.confidence == 1.0


def test_without_router_rules_decide(id_fields, passport_fields):
    result = run(KycPipeline(readers(id_fields, passport_fields)))
    assert result.decision is Decision.APPROVE
    assert result.router == "rules"


def test_router_can_escalate_but_not_reject(id_fields, passport_fields):
    result = run(KycPipeline(readers(id_fields, passport_fields), FakeRouter(Decision.REJECT)))
    assert result.decision is Decision.HUMAN_REVIEW


def test_router_cannot_override_rule_escalation(id_fields, passport_fields):
    disagreeing = {**id_fields, "id_number": "199087654321"}
    result = run(KycPipeline(readers(id_fields, passport_fields, disagreeing), FakeRouter(Decision.APPROVE)))
    assert result.decision is Decision.HUMAN_REVIEW
    assert result.router == "rules"


def test_router_failure_goes_to_human(id_fields, passport_fields):
    result = run(KycPipeline(readers(id_fields, passport_fields), FakeRouter(fail=True)))
    assert result.decision is Decision.HUMAN_REVIEW


def test_expired_rejected(id_fields, passport_fields):
    expired = {**id_fields, "expiry_date": "2020-01-01"}
    result = run(KycPipeline(readers(expired, passport_fields, expired), FakeRouter()))
    assert result.decision is Decision.REJECT


def test_failed_reader_lowers_confidence(id_fields, passport_fields):
    rs = [
        StaticReader("nvidia-vlm", {"national_id": id_fields, "passport": passport_fields}),
        StaticReader("arabic-vlm", {}, error="timeout"),
    ]
    result = run(KycPipeline(rs, FakeRouter()))
    assert result.confidence == 0.5
    assert result.decision is Decision.HUMAN_REVIEW


def test_api_end_to_end(id_fields, passport_fields):
    client = TestClient(create_app(KycPipeline(readers(id_fields, passport_fields))))
    assert client.get("/health").json()["readers"] == ["nvidia-vlm", "arabic-vlm"]
    resp = client.post(
        "/v1/kyc/cases",
        files=[("files", ("id.jpg", b"a", "image/jpeg")), ("files", ("pp.jpg", b"b", "image/jpeg"))],
        data={"doc_types": ["national_id", "passport"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"] in {"approve", "human_review"}


def test_api_rejects_mismatched_lengths(id_fields, passport_fields):
    client = TestClient(create_app(KycPipeline(readers(id_fields, passport_fields))))
    resp = client.post(
        "/v1/kyc/cases",
        files=[("files", ("id.jpg", b"a", "image/jpeg"))],
        data={"doc_types": ["national_id", "passport"]},
    )
    assert resp.status_code == 422
