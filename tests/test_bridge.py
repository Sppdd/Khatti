import json

import httpx
import pytest
from fastapi.testclient import TestClient

from khatti.api import create_app
from khatti.bridge import Bridge, HostRequest, Provider, self_host_recipe

NEBIUS = Provider("nebius", "Nebius Token Factory", "https://nebius.test/v1", "nb-key",
                  ("chat", "embeddings", "images", "models", "files", "batches"))
HF = Provider("hf", "HF router", "https://router.test/v1", "hf_key", ("chat", "models"))
HFE = Provider("hfe", "HF endpoints", "https://endpoints.test/v2", "hf_key", ("chat", "models"), kind="hf-endpoints")


class Upstream:
    """Fake of every upstream the bridge talks to; records requests."""

    def __init__(self, endpoint_state: str = "running"):
        self.requests: list[httpx.Request] = []
        self.endpoint_state = endpoint_state

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if url == "https://nebius.test/v1/models":
            return httpx.Response(200, json={"data": [{"id": "meta-llama/Llama-3.3-70B-Instruct", "object": "model"}]})
        if url == "https://router.test/v1/models":
            return httpx.Response(500, text="down")
        if url.endswith("/chat/completions"):
            body = json.loads(req.content)
            if body.get("stream"):
                return httpx.Response(200, content=b"data: {\"x\":1}\n\ndata: [DONE]\n\n",
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"message": {"content": f"echo {body['model']}"}}]})
        if url == "https://nebius.test/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        if url.startswith("https://nebius.test/v1/files"):
            return httpx.Response(200, json={"id": "file-1", "ctype": req.headers.get("content-type")})
        if url == "https://huggingface.co/api/whoami-v2":
            return httpx.Response(200, json={"name": "karrar"})
        if url == "https://api.endpoints.huggingface.cloud/v2/endpoint/karrar":
            if req.method == "POST":
                return httpx.Response(200, json=json.loads(req.content))
            return httpx.Response(200, json={"items": [self._endpoint()]})
        if url == "https://api.endpoints.huggingface.cloud/v2/endpoint/karrar/qwen-vl":
            return httpx.Response(200, json=self._endpoint())
        if url.endswith("/qwen-vl/pause"):
            return httpx.Response(200, json={"status": {"state": "paused"}})
        return httpx.Response(404, text=f"no route {req.method} {url}")

    def _endpoint(self) -> dict:
        return {
            "name": "qwen-vl",
            "model": {"repository": "Qwen/Qwen2.5-VL-7B-Instruct"},
            "status": {"state": self.endpoint_state, "url": "https://qwen-vl.endpoints.test"},
        }


def make(upstream: Upstream | None = None, allow_hosting: bool = False, api_key: str = ""):
    upstream = upstream or Upstream()
    bridge = Bridge([NEBIUS, HF, HFE], default="nebius", http=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
                    allow_hosting=allow_hosting)
    return TestClient(create_app(bridge=bridge, api_key=api_key)), upstream


def test_routes_by_provider_prefix_and_strips_it():
    client, up = make()
    r = client.post("/v1/bridge/openai/chat/completions",
                    json={"model": "hf/Qwen/Qwen2.5-7B-Instruct", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "echo Qwen/Qwen2.5-7B-Instruct"
    assert str(up.requests[-1].url) == "https://router.test/v1/chat/completions"
    assert up.requests[-1].headers["authorization"] == "Bearer hf_key"


def test_unprefixed_model_goes_to_default_provider():
    client, up = make()
    r = client.post("/v1/bridge/openai/embeddings", json={"model": "BAAI/bge-en-icl", "input": "x"})
    assert r.status_code == 200
    assert str(up.requests[-1].url) == "https://nebius.test/v1/embeddings"


def test_feature_not_supported_by_provider():
    client, _ = make()
    r = client.post("/v1/bridge/openai/embeddings", json={"model": "hf/some/model", "input": "x"})
    assert r.status_code == 400 and "does not support" in r.json()["detail"]


def test_unknown_path_is_not_proxied():
    client, _ = make()
    r = client.post("/v1/bridge/openai/admin/keys", json={"model": "nebius/x"})
    assert r.status_code == 404


def test_streaming_is_relayed():
    client, _ = make()
    r = client.post("/v1/bridge/openai/chat/completions", json={"model": "nebius/m", "stream": True, "messages": []})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.endswith("data: [DONE]\n\n")


def test_models_aggregate_and_report_provider_errors():
    client, _ = make()
    body = client.get("/v1/bridge/models").json()
    ids = [m["id"] for m in body["data"]]
    assert "nebius/meta-llama/Llama-3.3-70B-Instruct" in ids
    assert "hfe/qwen-vl" in ids
    assert "hf" in body["errors"]


def test_hf_endpoint_model_resolves_to_running_endpoint():
    client, up = make()
    r = client.post("/v1/bridge/openai/chat/completions", json={"model": "hfe/qwen-vl", "messages": []})
    assert r.status_code == 200, r.text
    assert str(up.requests[-1].url) == "https://qwen-vl.endpoints.test/v1/chat/completions"
    assert json.loads(up.requests[-1].content)["model"] == "Qwen/Qwen2.5-VL-7B-Instruct"


def test_paused_endpoint_is_409():
    client, _ = make(Upstream(endpoint_state="paused"))
    r = client.post("/v1/bridge/openai/chat/completions", json={"model": "hfe/qwen-vl", "messages": []})
    assert r.status_code == 409


def test_hosting_is_off_unless_enabled():
    client, _ = make()
    r = client.post("/v1/bridge/hosting/endpoints", json={"repository": "Qwen/Qwen2.5-VL-7B-Instruct"})
    assert r.status_code == 403


def test_create_endpoint_payload():
    client, up = make(allow_hosting=True)
    r = client.post("/v1/bridge/hosting/endpoints",
                    json={"repository": "Qwen/Qwen2.5-VL-7B-Instruct", "instance_type": "nvidia-a10g"})
    assert r.status_code == 200, r.text
    sent = json.loads(up.requests[-1].content)
    assert sent["model"]["repository"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert sent["model"]["image"] == {"huggingface": {}}
    assert sent["compute"]["instanceType"] == "nvidia-a10g"
    assert sent["compute"]["scaling"] == {"minReplica": 0, "maxReplica": 1, "scaleToZeroTimeout": 15}
    assert sent["type"] == "authenticated"


def test_pause_is_allowed_without_hosting_flag():
    client, _ = make()
    assert client.post("/v1/bridge/hosting/endpoints/qwen-vl/pause").json()["status"]["state"] == "paused"


def test_endpoint_name_is_valid():
    name = HostRequest(repository="Qwen/Qwen2.5-VL-7B-Instruct").payload()["name"]
    assert len(name) <= 32 and name == name.lower() and "." not in name


def test_passthrough_forwards_native_features():
    client, up = make()
    r = client.post("/v1/bridge/nebius/files", files={"file": ("b.jsonl", b"{}", "application/jsonl")},
                    data={"purpose": "batch"})
    assert r.status_code == 200
    assert r.json()["ctype"].startswith("multipart/form-data")
    assert up.requests[-1].headers["authorization"] == "Bearer nb-key"


def test_extract_with_bridged_model():
    reply = json.dumps({"kind": "note", "title": "Hello", "tags": ["x"]})

    class Vlm(Upstream):
        def __call__(self, req):
            if str(req.url).endswith("/chat/completions"):
                self.requests.append(req)
                return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
            return super().__call__(req)

    client, up = make(Vlm())
    r = client.post("/v1/extract", files={"file": ("a.jpg", b"img", "image/jpeg")},
                    data={"mode": "text", "model": "nebius/Qwen/Qwen2.5-VL-72B-Instruct"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Hello" and r.json()["model"] == "nebius/Qwen/Qwen2.5-VL-72B-Instruct"
    assert json.loads(up.requests[-1].content)["model"] == "Qwen/Qwen2.5-VL-72B-Instruct"


def test_bridge_requires_api_key():
    client, _ = make(api_key="s3cret")
    assert client.get("/v1/bridge/providers").status_code == 401
    ok = client.get("/v1/bridge/providers", headers={"Authorization": "Bearer s3cret"})
    assert ok.json()["default"] == "nebius"


def test_reserved_provider_names():
    with pytest.raises(ValueError):
        Bridge([Provider("openai", "x", "https://x/v1", "", ("chat",))])


def test_self_host_recipe():
    r = self_host_recipe("Qwen/Qwen2.5-VL-7B-Instruct")
    assert "--model Qwen/Qwen2.5-VL-7B-Instruct" in r["docker"]
    assert r["model"] == f"{r['provider']}/Qwen/Qwen2.5-VL-7B-Instruct"
