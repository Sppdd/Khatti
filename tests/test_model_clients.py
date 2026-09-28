import asyncio
import json

import httpx

from khatti.classify import LightningClassifier
from khatti.config import EndpointConfig
from khatti.llm import CallLog, ChatClient, current_call_log
from khatti.models import Line, ReaderStatus, Transcript
from khatti.orchestrator import NemotronReviewer
from khatti.readers import VisionChatReader
from khatti.registry import default_registry
from khatti.structuring import NemotronStructurer, verify

REG = default_registry()


def client(handler, name="m", json_mode=True):
    ep = EndpointConfig(name=name, base_url="https://llm.test/v1", model=f"vendor/{name}", api_key="k", json_mode=json_mode)
    return ChatClient(ep, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def reply(content, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}})


def test_reader_sends_schema_images_and_samples():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return reply(json.dumps({"caption": "tax card", "lines": [{"line_id": "L1", "text": "الرقم الضريبي: 123456789", "bbox": [0, 0, 1, 0.1]}]}))

    reader = VisionChatReader(client(handler, "arabic-vlm"), samples=3)
    ts = asyncio.run(reader.transcribe([(b"orig", "image/png"), (b"enh", "image/jpeg")]))
    assert [t.sample for t in ts] == [0, 1, 2] and all(t.status is ReaderStatus.OK for t in ts)
    assert ts[0].lines[0].bbox == [0, 0, 1, 0.1]
    b = bodies[0]
    assert b["response_format"]["type"] == "json_schema"
    assert b["temperature"] == 0.7  # sampled for self-consistency
    parts = b["messages"][0]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,") and parts[1]["image_url"]["url"].startswith("data:image/jpeg")


def test_stopped_gpu_endpoint_is_unavailable():
    reader = VisionChatReader(client(lambda r: httpx.Response(503), "nvidia-omni"))
    assert asyncio.run(reader.transcribe([(b"x", "image/png")]))[0].status is ReaderStatus.UNAVAILABLE


def test_super_structurer_output_is_still_verified():
    spec = REG.get("tax_card")
    t = Transcript(reader="r", lines=[Line(line_id="L1", text="الرقم الضريبي: ١٢٣٤٥٦٧٨٩"), Line(line_id="L2", text="اسم المكلف: علي حسين")])

    def handler(req):
        body = json.loads(req.content)
        assert "image_url" not in json.dumps(body)  # Super never sees the image
        return reply(json.dumps({"fields": {
            "tax_number": {"value": "123456789", "line_ids": ["L1"]},  # digits converted: tolerated, stored as read
            "holder_name_ar": {"value": "علي حسن", "line_ids": ["L2"]},  # "corrected": rejected
            "business_name_ar": {"value": None, "reason": "not_present"},
            "invented_field": {"value": "x"},
        }}))

    extracted = asyncio.run(NemotronStructurer(client(handler, "super")).structure(t, spec))
    assert "invented_field" not in extracted
    assert verify(extracted["tax_number"], t).value == "١٢٣٤٥٦٧٨٩"
    assert verify(extracted["holder_name_ar"], t).reason == "not_verbatim"


def test_call_log_records_hashes_not_images():
    log = CallLog()
    token = current_call_log.set(log)
    try:
        reader = VisionChatReader(client(lambda r: reply('{"caption": "", "lines": []}'), "omni"))
        asyncio.run(reader.transcribe([(b"secret-image", "image/png")]))
    finally:
        current_call_log.reset(token)
    rec = log.records[0]
    assert rec.model == "vendor/omni" and rec.purpose == "transcribe#0" and rec.ok
    assert rec.prompt_tokens == 10 and len(rec.image_sha256) == 1
    assert "secret-image" not in rec.prompt_sha256


def test_lightning_classifier_respects_layout():
    t = [Transcript(reader="r", caption="commercial registration certificate", lines=[])]
    lightning = LightningClassifier(client(lambda r: reply('{"doc_type": "commercial_registration", "confidence": 0.9}')))
    specs = REG.of_domain("kyc")
    # A4-shaped photo: accepted
    assert asyncio.run(lightning.classify(t, 1.414, specs)).doc_type == "commercial_registration"
    # card-shaped photo cannot be an A4 certificate: falls back to rules
    assert asyncio.run(lightning.classify(t, 1.586, specs)).evidence != "lightning"


def test_reviewer_returns_bullets():
    ultra = NemotronReviewer(client(lambda r: reply('{"bullets": ["Check {{field:tax_card.tax_number}}."]}'), "ultra"))
    assert asyncio.run(ultra.bullets({})) == ["Check {{field:tax_card.tax_number}}."]
