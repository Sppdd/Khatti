"""End-to-end API tests against a real Postgres (set KHATTI_TEST_DATABASE_URL to a superuser URL).

The app connects as a separate non-superuser role so Row-Level Security is actually enforced.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

from khatti.api import create_app
from khatti.auth import RateLimiter
from khatti.crypto import Keyring
from khatti.pipeline import Pipeline
from khatti.readers import StaticReader
from khatti.registry import default_registry
from khatti.service import KycService
from khatti.services import Services
from khatti.storage import LocalObjectStore
from khatti.store import Store
from khatti.structuring import LabelStructurer
from khatti.webhooks import SIGNATURE_HEADER, verify
from khatti.worker import process_one

from .conftest import LICENSE, a4_image, labelled

ADMIN_URL = os.getenv("KHATTI_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set KHATTI_TEST_DATABASE_URL to run Postgres tests")
APP_ROLE = "khatti_app_test"


def app_url() -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(ADMIN_URL)
    netloc = f"{APP_ROLE}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def reset_database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
                     f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute(f"CREATE SCHEMA public AUTHORIZATION {APP_ROLE}")


class Env:
    """Everything a scenario needs, built inside the test's event loop."""

    async def start(self, tmp_path, images, pages, rate_limit=1000):
        reset_database()
        self.hooks: list[httpx.Request] = []
        self.store = await Store.connect(app_url())
        self.keys, _ = Keyring.generate()
        registry = default_registry()
        by_image = {img: (pages[slot][0], [labelled(pages[slot][1])]) for slot, img in images.items()}
        # A second license photo where the grandfather name is missing (routes to review).
        self.bad_license = a4_image(99)
        by_image[self.bad_license] = (pages["commercial_registration"][0],
                                      [labelled({**LICENSE, "اسم المالك": "مثال أحمد محمد"})])
        readers = [StaticReader("nvidia-omni", by_image), StaticReader("arabic-vlm", by_image)]
        pipeline = Pipeline(readers, LabelStructurer(), registry)
        self.objects = LocalObjectStore(tmp_path, "http://khatti.test", self.keys.hmac_key)
        hook_http = httpx.AsyncClient(transport=httpx.MockTransport(self._hook))
        self.service = KycService(self.store, self.objects, self.keys, registry, pipeline, hook_http,
                                  webhook_allowed_hosts=frozenset({"bank.test"}), retention_days=30)
        self.services = Services(registry=registry, pipeline=pipeline, service=self.service, objects=self.objects,
                                 jwt_secret="test-secret-" + "x" * 32, limiter=RateLimiter(rate_limit))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.services)), base_url="http://khatti.test")
        self.tenant_a = await self.store.create_tenant("bank-a")
        self.tenant_b = await self.store.create_tenant("bank-b")
        self.key_a = await self.store.create_api_key(self.tenant_a, "server")
        self.key_b = await self.store.create_api_key(self.tenant_b, "server")
        return self

    def _hook(self, request: httpx.Request) -> httpx.Response:
        self.hooks.append(request)
        return httpx.Response(200)

    async def close(self):
        await self.api.aclose()
        await self.store.close()

    def h(self, key, idem=None):
        headers = {"Authorization": f"Bearer {key}"}
        if idem:
            headers["Idempotency-Key"] = idem
        return headers

    async def upload(self, uploads, images):
        for u in uploads:
            r = await self.api.put(u["url"], content=images[u["slot"]], headers=u["headers"])
            assert r.status_code == 204, r.text


ALL = ["national_id_front", "national_id_back", "commercial_registration", "tax_card"]


def test_full_kyc_flow(tmp_path, images, pages):
    async def scenario():
        env = await Env().start(tmp_path, images, pages)
        api, A = env.api, env.key_a
        try:
            # webhook registration (secret shown once)
            r = await api.post("/v1/webhooks", json={"url": "http://bank.test/hook", "events": ["session.completed", "session.needs_review", "session.retake_requested"]}, headers=env.h(A, "w1"))
            assert r.status_code == 201, r.text
            secret = r.json()["secret"]

            # create -> upload -> submit, with the bad license so the session routes to review
            r = await api.post("/v1/kyc/sessions", json={"slots": ALL, "external_ref": "merchant-42"}, headers=env.h(A, "s1"))
            assert r.status_code == 201, r.text
            created = r.json()
            sid = created["session_id"]
            replay = await api.post("/v1/kyc/sessions", json={"slots": ALL, "external_ref": "merchant-42"}, headers=env.h(A, "s1"))
            assert replay.json() == created and replay.headers["Idempotent-Replayed"] == "true"
            conflict = await api.post("/v1/kyc/sessions", json={"slots": ALL[:1]}, headers=env.h(A, "s1"))
            assert conflict.status_code == 422

            early = await api.post(f"/v1/kyc/sessions/{sid}/submit", headers=env.h(A, "sub0"))
            assert early.status_code == 422 and "no upload" in early.text
            await env.upload(created["uploads"], {**images, "commercial_registration": env.bad_license})
            r = await api.post(f"/v1/kyc/sessions/{sid}/submit", headers=env.h(A, "sub1"))
            assert r.status_code == 202, r.text

            assert await process_one(env.service) and not await process_one(env.service)
            view = (await api.get(f"/v1/kyc/sessions/{sid}", headers=env.h(A))).json()
            assert view["status"] == "needs_review"
            assert "NAME_PARTIAL_MATCH" in view["decision"]["reasons"]
            front = next(d for d in view["documents"] if d["slot"] == "national_id_front")
            assert front["fields"]["id_number"] == {"value": "١٩٩٠١٢٣٤٥٦٧٨", "confidence": front["fields"]["id_number"]["confidence"], "status": "ok"}
            assert "candidates" not in front["fields"]["id_number"]  # reviewer-only
            assert view["review_summary"]

            # reviewer token, queue, item, image
            r = await api.post("/v1/auth/token", json={"subject": "reviewer-1", "role": "reviewer"}, headers=env.h(A))
            rev = r.json()["access_token"]
            app_tok = (await api.post("/v1/auth/token", json={"subject": "phone-1", "role": "app"}, headers=env.h(A))).json()["access_token"]
            assert (await api.get("/v1/review/queue", headers=env.h(app_tok))).status_code == 403
            queue = (await api.get("/v1/review/queue?reason=NAME_PARTIAL_MATCH", headers=env.h(rev))).json()
            assert [q["session_id"] for q in queue] == [sid]
            item = (await api.get(f"/v1/review/items/{queue[0]['id']}", headers=env.h(rev))).json()
            lic = next(d for d in item["session"]["documents"] if d["slot"] == "commercial_registration")
            assert "candidates" in lic["fields"]["owner_name_ar"]
            img = await api.get(f"/v1/review/documents/{lic['document_id']}/image", headers=env.h(rev))
            assert img.status_code == 200 and img.content == env.bad_license

            # reviewer requests a retake of the license
            r = await api.post(f"/v1/review/items/{queue[0]['id']}/decision",
                               json={"action": "request_retake", "retake_slots": ["commercial_registration"], "note": "owner name incomplete"},
                               headers=env.h(rev, "d1"))
            assert r.json()["status"] == "retake_requested", r.text
            view = (await api.get(f"/v1/kyc/sessions/{sid}", headers=env.h(app_tok))).json()
            assert [u["slot"] for u in view["uploads"]] == ["commercial_registration"]
            await env.upload(view["uploads"], images)
            assert (await api.post(f"/v1/kyc/sessions/{sid}/submit", headers=env.h(app_tok, "sub2"))).status_code == 202
            assert await process_one(env.service)
            view = (await api.get(f"/v1/kyc/sessions/{sid}", headers=env.h(A))).json()
            assert view["status"] == "completed" and view["decision"]["outcome"] == "auto_pass", view["decision"]

            # webhooks: needs_review, retake_requested, completed — signed
            while await env.service.deliver_one():
                pass
            events = [json.loads(h.content)["event"] for h in env.hooks]
            assert events == ["session.needs_review", "session.retake_requested", "session.completed"]
            assert all(verify(secret, h.content, h.headers[SIGNATURE_HEADER]) for h in env.hooks)
            assert "مثال" not in "".join(h.content.decode() for h in env.hooks)  # no PII in webhooks

            # tenant isolation (API and RLS)
            assert (await api.get(f"/v1/kyc/sessions/{sid}", headers=env.h(env.key_b))).status_code == 404
            async with env.store.tx(env.tenant_b) as conn:
                assert (await (await conn.execute("SELECT count(*) AS n FROM sessions")).fetchone())["n"] == 0
                assert (await (await conn.execute("SELECT count(*) AS n FROM fields")).fetchone())["n"] == 0

            # encryption at rest + HMAC lookup
            async with env.store.tx(env.tenant_a) as conn:
                row = await (await conn.execute("SELECT result_enc FROM sessions WHERE id = %s", (sid,))).fetchone()
                assert "مثال".encode() not in bytes(row["result_enc"])
                mac = env.keys.lookup_hmac("id_number", "199012345678")  # digit script does not matter
                hit = await (await conn.execute("SELECT session_id FROM fields WHERE name = 'id_number' AND value_hmac = %s", (mac,))).fetchone()
                assert hit["session_id"] == sid
                calls = await (await conn.execute("SELECT count(*) AS n FROM model_calls")).fetchone()
                assert calls["n"] == 0  # static readers make no model calls

            # audit: hash chain verifies and is append-only
            assert await env.store.verify_audit_chain(env.tenant_a)
            with pytest.raises(psycopg.Error):
                async with env.store.tx(env.tenant_a) as conn:
                    await conn.execute("UPDATE audit_log SET actor = 'x'")

            # retention: originals deleted 30 days after completion
            # 4 current originals + the superseded license photo from before the retake
            assert await env.service.retention_sweep(datetime.now(timezone.utc) + timedelta(days=31)) == 5
            async with env.store.tx(env.tenant_a) as conn:
                left = await (await conn.execute("SELECT count(*) AS n FROM documents WHERE object_key IS NOT NULL")).fetchone()
                assert left["n"] == 0
            assert not any(p.is_file() for p in (tmp_path / "originals").rglob("*"))
        finally:
            await env.close()

    asyncio.run(scenario())


def test_auth_rate_limit_and_idempotency_required(tmp_path, images, pages):
    async def scenario():
        env = await Env().start(tmp_path, images, pages, rate_limit=3)
        api = env.api
        try:
            assert (await api.get("/v1/webhooks")).status_code == 401
            assert (await api.get("/v1/webhooks", headers=env.h("kh_wrong"))).status_code == 401
            r = await api.post("/v1/kyc/sessions", json={"slots": ALL}, headers=env.h(env.key_a))
            assert r.status_code == 400 and "Idempotency-Key" in r.text
            codes = [(await api.get("/v1/webhooks", headers=env.h(env.key_a))).status_code for _ in range(4)]
            assert codes[-1] == 429
            r = await api.get("/v1/webhooks", headers=env.h(env.key_a))
            assert int(r.headers["Retry-After"]) >= 1
        finally:
            await env.close()

    asyncio.run(scenario())


def test_single_document_endpoint_and_bad_upload_token(tmp_path, images, pages):
    async def scenario():
        env = await Env().start(tmp_path, images, pages)
        api, A = env.api, env.key_a
        try:
            r = await api.post("/v1/documents", json={"type": "tax_card"}, headers=env.h(A, "doc1"))
            assert r.status_code == 202, r.text
            doc = r.json()
            bad = doc["upload"]["url"][:-3] + "abc"
            assert (await api.put(bad, content=b"x")).status_code == 403
            await env.upload([doc["upload"]], images)
            assert (await api.post(f"/v1/documents/{doc['document_id']}/submit", headers=env.h(A, "doc1s"))).status_code == 202
            await process_one(env.service)
            got = (await api.get(f"/v1/documents/{doc['document_id']}", headers=env.h(A))).json()
            assert got["document"]["fields"]["tax_number"]["value"] == "123456789"
        finally:
            await env.close()

    asyncio.run(scenario())
