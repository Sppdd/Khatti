import asyncio
import json
import os
import uuid

import httpx
import pytest

from khatti.config import EndpointConfig
from khatti.llm import ChatClient, EndpointUnavailable
from khatti.models import DocumentInput, ReaderStatus
from khatti.readers import Structurer, VisionChatReader
from khatti.registry import default_registry
from khatti.storage import LocalObjectStore
from khatti.webhooks import SIGNATURE_HEADER, deliver, sign, validate_callback_url

SPEC = default_registry().get("national_id")
DOC = DocumentInput(doc_type="national_id", image=b"img")


def endpoint(name="r", json_mode=True):
    return EndpointConfig(name=name, base_url="https://llm.test/v1", model="m", api_key="k", json_mode=json_mode)


def client_replying(handler, name="r", json_mode=True):
    return ChatClient(endpoint(name, json_mode), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def chat_reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ---------------------------------------------------------------- LLM client and readers

def test_json_mode_is_sent_only_when_enabled():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return chat_reply("{}")

    asyncio.run(client_replying(handler).complete([]))
    asyncio.run(client_replying(handler, json_mode=False).complete([]))
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in bodies[1]


def test_stopped_endpoint_is_unavailable_not_error():
    reader = VisionChatReader(client_replying(lambda req: httpx.Response(503), name="nvidia-omni"))
    result = asyncio.run(reader.read(DOC, SPEC))
    assert result.status is ReaderStatus.UNAVAILABLE

    with pytest.raises(EndpointUnavailable):
        asyncio.run(client_replying(lambda req: httpx.Response(504)).complete([]))


def test_bad_request_is_error():
    reader = VisionChatReader(client_replying(lambda req: httpx.Response(400)))
    assert asyncio.run(reader.read(DOC, SPEC)).status is ReaderStatus.ERROR


def test_prose_reply_is_structured_by_super():
    reader_client = client_replying(lambda req: chat_reply("The name is علي and the ID number is 199012345678."))
    seen = {}

    def structurer_handler(req):
        seen["payload"] = json.loads(json.loads(req.content)["messages"][1]["content"])
        return chat_reply('{"full_name_ar": "علي", "id_number": "199012345678", "invented": null}')

    reader = VisionChatReader(reader_client, Structurer(client_replying(structurer_handler, name="super")))
    result = asyncio.run(reader.read(DOC, SPEC))
    assert result.status is ReaderStatus.OK
    assert result.fields["id_number"] == "199012345678"
    assert seen["payload"]["fields"] == SPEC.field_names


def test_prose_reply_without_structurer_is_error():
    reader = VisionChatReader(client_replying(lambda req: chat_reply("no json here")))
    assert asyncio.run(reader.read(DOC, SPEC)).status is ReaderStatus.ERROR


def test_image_is_sent_as_base64_data_url():
    seen = {}

    def handler(req):
        seen["content"] = json.loads(req.content)["messages"][0]["content"]
        return chat_reply("{}")

    asyncio.run(VisionChatReader(client_replying(handler)).read(DOC, SPEC))
    assert seen["content"][0]["image_url"]["url"] == "data:image/jpeg;base64,aW1n"
    assert "mother_name_ar" in seen["content"][1]["text"]


# ---------------------------------------------------------------- webhooks and storage

def test_callback_url_validation():
    validate_callback_url("https://bank.example/hook", frozenset())
    with pytest.raises(ValueError):
        validate_callback_url("http://bank.example/hook", frozenset())
    with pytest.raises(ValueError):
        validate_callback_url("https://evil.example/hook", frozenset({"bank.example"}))
    validate_callback_url("http://bank.example/hook", frozenset({"bank.example"}))


def test_webhook_is_signed_and_retried():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500 if len(calls) < 2 else 200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok, status = asyncio.run(deliver(http, "https://bank.example/hook", {"a": 1}, "s3cret", backoff_s=0))
    assert ok and status == "HTTP 200" and len(calls) == 2
    assert calls[-1].headers[SIGNATURE_HEADER] == sign("s3cret", calls[-1].content)


def test_local_store_roundtrip_and_traversal(tmp_path):
    store = LocalObjectStore(tmp_path)
    asyncio.run(store.put("cases/x/0", b"data", "image/jpeg"))
    assert asyncio.run(store.get("cases/x/0")) == b"data"
    with pytest.raises(ValueError):
        asyncio.run(store.get("../../etc/passwd"))


# ---------------------------------------------------------------- Postgres queue (needs a database)

DB_URL = os.getenv("KHATTI_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="set KHATTI_TEST_DATABASE_URL to run Postgres tests")


async def fresh_store():
    from khatti.db import CaseStore

    store = await CaseStore.connect(DB_URL)
    async with store.pool.connection() as conn:
        await conn.execute("TRUNCATE cases CASCADE")
    return store


@needs_db
def test_async_case_end_to_end(tmp_path, id_fields, passport_fields):
    from khatti.api import create_app
    from khatti.pipeline import Pipeline
    from khatti.readers import StaticReader
    from khatti.services import Services
    from khatti.worker import process_one

    hooks = []

    def hook_handler(req):
        hooks.append(json.loads(req.content))
        return httpx.Response(200)

    async def scenario():
        readers = [
            StaticReader("arabic-vlm", {"national_id": id_fields, "passport": passport_fields}),
            StaticReader("nvidia-omni", {}, status=ReaderStatus.UNAVAILABLE),
        ]
        svc = Services(
            pipeline=Pipeline(readers),
            cases=await fresh_store(),
            objects=LocalObjectStore(tmp_path),
            http=httpx.AsyncClient(transport=httpx.MockTransport(hook_handler)),
            webhook_secret="s",
        )
        app = create_app(svc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as api:
            resp = await api.post(
                "/v1/cases",
                files=[("files", ("id.jpg", b"a", "image/jpeg")), ("files", ("pp.jpg", b"b", "image/jpeg"))],
                data={"doc_types": ["national_id", "passport"], "callback_url": "https://bank.example/hook"},
            )
            assert resp.status_code == 202, resp.text
            case_id = resp.json()["id"]
            assert (await api.get(f"/v1/cases/{case_id}")).json()["status"] == "queued"

            assert await process_one(svc) is True
            assert await process_one(svc) is False  # queue drained

            case = (await api.get(f"/v1/cases/{case_id}")).json()
        await svc.close()
        return case_id, case

    case_id, case = asyncio.run(scenario())
    assert case["status"] == "done"
    # One of two readers is down, so agreement is 0.5: the case must go to a human.
    assert case["decision"] == "human_review"
    events = [e["event"] for e in case["audit"]]
    assert events == ["created", "claimed", "decided", "webhook_delivered"]
    decided = case["audit"][2]["detail"]
    assert decided["readers"]["national_id:nvidia-omni"] == "unavailable"
    assert hooks[0]["case_id"] == case_id and hooks[0]["result"]["decision"] == "human_review"


@needs_db
def test_skip_locked_hands_each_case_to_one_worker():
    from khatti.db import StoredDocument

    async def scenario():
        store = await fresh_store()
        ids = [uuid.uuid4() for _ in range(5)]
        for cid in ids:
            await store.create_case(cid, [StoredDocument(0, "national_id", f"k/{cid}", "image/jpeg")], None)
        claims = await asyncio.gather(*(store.claim_next() for _ in range(8)))
        await store.close()
        return ids, claims

    ids, claims = asyncio.run(scenario())
    got = [c.id for c in claims if c]
    assert sorted(got) == sorted(ids)  # each case claimed exactly once, none twice
    assert claims.count(None) == 3


@needs_db
def test_failed_case_retries_then_fails():
    from khatti.db import StoredDocument

    async def scenario():
        store = await fresh_store()
        cid = uuid.uuid4()
        await store.create_case(cid, [StoredDocument(0, "national_id", "k", "image/jpeg")], None)
        c = await store.claim_next()
        await store.fail(c.id, "boom", retry=True)
        c = await store.claim_next()
        assert c.attempts == 2
        await store.fail(c.id, "boom", retry=False)
        assert await store.claim_next() is None
        case = await store.get_case(cid)
        await store.close()
        return case

    case = asyncio.run(scenario())
    assert case["status"] == "failed" and case["error"] == "boom"
    assert [e["event"] for e in case["audit"]] == ["created", "claimed", "retry", "claimed", "failed"]
