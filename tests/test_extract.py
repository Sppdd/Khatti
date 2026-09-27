import json

from fastapi.testclient import TestClient

from khatti.api import create_app
from khatti.extract import ExtractMode, PhotoExtractor, build_extract_prompt, coerce_result, to_number


class FakeClient:
    def __init__(self, reply: str):
        self.reply = reply
        self.messages: list[dict] = []

    async def complete(self, messages, temperature=0.0, max_tokens=1500):
        self.messages = messages
        return self.reply


RECEIPT = {
    "kind": "receipt",
    "title": "Grocery receipt",
    "summary": "Weekly shopping at a local market.",
    "language": "ar",
    "text": "سوق النخيل\nطماطم ١٬٢٥٠",
    "tags": ["Groceries", "Baghdad", ""],
    "fields": {"phone": "07701234567", "empty": None},
    "items": [
        {"name": "طماطم", "price": "١٬٢٥٠", "currency": "IQD", "quantity": "1", "unit": "kg"},
        {"name": "Milk", "price": 2000},
        {"name": "", "price": 5},
    ],
    "store": {"name": "سوق النخيل", "date": "2026-09-20", "total": "3,250 IQD"},
    "prompt": {"prompt": ""},
}


def test_to_number_handles_arabic_and_separators():
    assert to_number("١٬٢٥٠") == 1250
    assert to_number("3,500 IQD") == 3500
    assert to_number("2٫5") == 2.5
    assert to_number(7) == 7.0
    assert to_number("n/a") is None
    assert to_number(None) is None


def test_coerce_result_cleans_model_output():
    r = coerce_result(RECEIPT, ExtractMode.PRICES, "vlm")
    assert r.kind.value == "receipt"
    assert [i.name for i in r.items] == ["طماطم", "Milk"]
    assert r.items[0].price == 1250 and r.items[0].quantity == 1
    assert r.store and r.store.total == 3250
    assert r.tags == ["groceries", "baghdad"]
    assert r.fields == {"phone": "07701234567"}
    assert r.prompt is None  # empty prompt text is dropped
    assert r.model == "vlm"


def test_unknown_kind_and_bad_shapes_are_tolerated():
    r = coerce_result({"kind": "selfie", "tags": "food", "items": "none", "fields": []}, ExtractMode.AUTO)
    assert r.kind.value == "other"
    assert r.tags == [] and r.items == [] and r.fields == {}


def test_prompt_mentions_mode_focus():
    assert "creative director" in build_extract_prompt(ExtractMode.PROMPT)
    assert "Arabic" in build_extract_prompt(ExtractMode.TEXT)


def _app(reply: str, api_key: str = "") -> tuple[TestClient, FakeClient]:
    fake = FakeClient(reply)
    return TestClient(create_app(extractor=PhotoExtractor(fake, "vlm"), api_key=api_key)), fake


def test_extract_endpoint():
    client, fake = _app("```json\n" + json.dumps(RECEIPT, ensure_ascii=False) + "\n```")
    resp = client.post("/v1/extract", files={"file": ("r.jpg", b"img", "image/jpeg")}, data={"mode": "prices"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "prices" and body["items"][0]["price"] == 1250
    assert fake.messages[0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert client.get("/health").json()["extractor"] == "vlm"


def test_extract_bad_model_reply_is_502():
    client, _ = _app("sorry, I can't")
    resp = client.post("/v1/extract", files={"file": ("r.jpg", b"img", "image/jpeg")})
    assert resp.status_code == 502


def test_extract_requires_api_key_when_set():
    client, _ = _app(json.dumps(RECEIPT), api_key="s3cret")
    files = {"file": ("r.jpg", b"img", "image/jpeg")}
    assert client.post("/v1/extract", files=files).status_code == 401
    ok = client.post("/v1/extract", files=files, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_extract_unconfigured_is_503():
    client = TestClient(create_app(api_key=""))
    client.app.state.extractor = None
    resp = client.post("/v1/extract", files={"file": ("r.jpg", b"img", "image/jpeg")})
    assert resp.status_code == 503
