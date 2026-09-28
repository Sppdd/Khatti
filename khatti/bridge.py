"""Model bridge: one gateway to Nebius Token Factory, Hugging Face and self-hosted models.

Every provider speaks the OpenAI HTTP API, so the bridge only has to pick the provider
and forward the request. Models are addressed as ``<provider>/<model id>``:

    nebius/meta-llama/Llama-3.3-70B-Instruct   Nebius Token Factory
    hf/Qwen/Qwen2.5-VL-7B-Instruct             Hugging Face Inference Providers router
    hfe/my-endpoint                            a dedicated Hugging Face Inference Endpoint
    <custom>/<model>                           any OpenAI-compatible server (vLLM, TGI, SGLang...)

An id whose first segment is not a provider name goes to the default provider.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field

# OpenAI-style paths the bridge forwards. Anything else is refused, so the bridge is never an open proxy.
FEATURE_PATHS = {
    "chat": "chat/completions",
    "completions": "completions",
    "embeddings": "embeddings",
    "images": "images/generations",
    "models": "models",
    "files": "files",
    "batches": "batches",
    "fine_tuning": "fine_tuning/jobs",
}

# Route segments under /v1/bridge that a provider may not be named after.
RESERVED_NAMES = {"openai", "hosting", "models", "providers"}

HF_ENDPOINTS_API = "https://api.endpoints.huggingface.cloud/v2"
HF_CATALOG_API = "https://endpoints.huggingface.co/api/catalog"
HF_WHOAMI = "https://huggingface.co/api/whoami-v2"


class BridgeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    api_key: str
    features: tuple[str, ...]
    kind: str = "openai"  # "openai" | "hf-endpoints"


def providers_from_env() -> list[Provider]:
    out: list[Provider] = []
    if key := os.getenv("KHATTI_TOKEN_FACTORY_KEY", ""):
        out.append(
            Provider(
                name="nebius",
                label="Nebius Token Factory",
                base_url=os.getenv("KHATTI_TOKEN_FACTORY_URL", "https://api.studio.nebius.com/v1"),
                api_key=key,
                features=tuple(FEATURE_PATHS),
            )
        )
    if hf_token := os.getenv("HF_TOKEN", ""):
        out.append(
            Provider(
                name="hf",
                label="Hugging Face Inference Providers",
                base_url=os.getenv("KHATTI_HF_ROUTER_URL", "https://router.huggingface.co/v1"),
                api_key=hf_token,
                features=("chat", "models"),
            )
        )
        out.append(
            Provider(
                name="hfe",
                label="Hugging Face Inference Endpoints (dedicated)",
                base_url=HF_ENDPOINTS_API,
                api_key=hf_token,
                features=("chat", "completions", "embeddings", "models"),
                kind="hf-endpoints",
            )
        )
    # Self-hosted or extra providers, e.g. vLLM serving a Hugging Face model on a Nebius VM:
    # KHATTI_PROVIDERS='[{"name":"myvllm","base_url":"http://10.0.0.5:8000/v1","api_key":"..."}]'
    for p in json.loads(os.getenv("KHATTI_PROVIDERS", "[]")):
        out.append(
            Provider(
                name=p["name"],
                label=p.get("label", p["name"]),
                base_url=p["base_url"],
                api_key=p.get("api_key", ""),
                features=tuple(p.get("features", ["chat", "completions", "embeddings", "models"])),
            )
        )
    return out


@dataclass
class Target:
    """Where one request goes: resolved provider URL plus the model id the upstream expects."""

    provider: Provider
    base_url: str
    api_key: str
    model: str


@dataclass
class _Cached:
    value: object
    at: float = field(default_factory=time.monotonic)


class Bridge:
    def __init__(
        self,
        providers: list[Provider],
        default: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 120.0,
        allow_hosting: bool = False,
        hf_namespace: str | None = None,
    ):
        clash = RESERVED_NAMES.intersection(p.name for p in providers)
        if clash:
            raise ValueError(f"Provider names {sorted(clash)} are reserved")
        self.providers = {p.name: p for p in providers}
        self.default = default if default in self.providers else next(iter(self.providers), None)
        self.http = http or httpx.AsyncClient(timeout=timeout_s)
        self.allow_hosting = allow_hosting
        self._namespace = hf_namespace
        self._endpoint_cache: dict[str, _Cached] = {}

    # ---------- routing ----------

    def provider(self, name: str) -> Provider:
        p = self.providers.get(name)
        if p is None:
            raise BridgeError(f"Unknown or unconfigured provider '{name}'", 404)
        return p

    def split(self, model: str) -> tuple[Provider, str]:
        head, _, rest = model.partition("/")
        if rest and head in self.providers:
            return self.providers[head], rest
        if self.default is None:
            raise BridgeError("No model providers configured", 503)
        return self.providers[self.default], model

    async def resolve(self, model: str) -> Target:
        p, upstream = self.split(model)
        if p.kind == "hf-endpoints":
            ep = await self.hf_endpoint(upstream, cached=True)
            state, url = ep.get("status", {}).get("state"), ep.get("status", {}).get("url")
            if state != "running" or not url:
                raise BridgeError(f"Endpoint '{upstream}' is {state or 'unavailable'}; resume it first", 409)
            # TGI and vLLM on Inference Endpoints expose the OpenAI API under /v1.
            return Target(p, f"{url.rstrip('/')}/v1", p.api_key, ep.get("model", {}).get("repository") or "tgi")
        return Target(p, p.base_url, p.api_key, upstream)

    # ---------- forwarding ----------

    def _headers(self, api_key: str, content_type: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if content_type:
            h["Content-Type"] = content_type
        return h

    async def send(
        self,
        target: Target,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        params: dict | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        feature = next((f for f, prefix in FEATURE_PATHS.items() if path == prefix or path.startswith(prefix + "/")), None)
        if feature is None:
            raise BridgeError(f"Path '{path}' is not bridged", 404)
        if feature not in target.provider.features:
            raise BridgeError(f"{target.provider.label} does not support '{feature}' through the bridge", 400)
        if json_body is not None and "model" in json_body:
            json_body = {**json_body, "model": target.model}
        req = self.http.build_request(
            method,
            f"{target.base_url.rstrip('/')}/{path}",
            headers=self._headers(target.api_key, None if json_body is not None else content_type),
            json=json_body,
            content=content,
            params=params,
        )
        return await self.http.send(req, stream=stream)

    async def call(self, model: str, path: str, body: dict, stream: bool = False) -> httpx.Response:
        """Forward an OpenAI-style JSON request, routed by its model id."""
        return await self.send(await self.resolve(model), "POST", path, json_body={**body, "model": model}, stream=stream)

    # ---------- discovery ----------

    async def list_models(self, provider: str | None = None) -> dict:
        names = [provider] if provider else list(self.providers)
        results = await asyncio.gather(*(self._models_of(self.provider(n)) for n in names), return_exceptions=True)
        data, errors = [], {}
        for name, res in zip(names, results):
            if isinstance(res, Exception):
                errors[name] = str(res)
            else:
                data.extend(res)
        return {"object": "list", "data": data, "errors": errors}

    async def _models_of(self, p: Provider) -> list[dict]:
        if p.kind == "hf-endpoints":
            return [
                {
                    "id": f"hfe/{e['name']}",
                    "object": "model",
                    "provider": p.name,
                    "owned_by": e.get("model", {}).get("repository"),
                    "state": e.get("status", {}).get("state"),
                }
                for e in await self.hf_endpoints()
            ]
        resp = await self.http.get(f"{p.base_url.rstrip('/')}/models", headers=self._headers(p.api_key))
        resp.raise_for_status()
        return [
            {**m, "id": f"{p.name}/{m['id']}", "upstream_id": m["id"], "provider": p.name}
            for m in resp.json().get("data", [])
            if isinstance(m, dict) and m.get("id")
        ]

    def describe(self) -> list[dict]:
        return [
            {"name": p.name, "label": p.label, "kind": p.kind, "features": list(p.features), "default": p.name == self.default}
            for p in self.providers.values()
        ]

    # ---------- Hugging Face Inference Endpoints (hosting any HF model) ----------

    def _hf(self) -> Provider:
        p = next((p for p in self.providers.values() if p.kind == "hf-endpoints"), None)
        if p is None:
            raise BridgeError("Set HF_TOKEN to host Hugging Face models", 503)
        return p

    async def _hf_request(self, method: str, url: str, body: dict | None = None) -> dict:
        resp = await self.http.request(method, url, headers=self._headers(self._hf().api_key), json=body)
        if resp.status_code >= 400:
            raise BridgeError(f"Hugging Face: {resp.text[:500]}", resp.status_code)
        return resp.json() if resp.content else {}

    async def hf_namespace(self) -> str:
        if not self._namespace:
            self._namespace = (await self._hf_request("GET", HF_WHOAMI))["name"]
        return self._namespace

    async def hf_endpoints(self) -> list[dict]:
        ns = await self.hf_namespace()
        return (await self._hf_request("GET", f"{HF_ENDPOINTS_API}/endpoint/{ns}")).get("items", [])

    async def hf_endpoint(self, name: str, cached: bool = False) -> dict:
        hit = self._endpoint_cache.get(name)
        if cached and hit and time.monotonic() - hit.at < 30:
            return hit.value  # type: ignore[return-value]
        ns = await self.hf_namespace()
        ep = await self._hf_request("GET", f"{HF_ENDPOINTS_API}/endpoint/{ns}/{name}")
        self._endpoint_cache[name] = _Cached(ep)
        return ep

    def _guard_hosting(self) -> None:
        if not self.allow_hosting:
            raise BridgeError("Hosting is disabled; set KHATTI_ALLOW_HOSTING=1 (it creates paid resources)", 403)

    async def hf_create(self, req: HostRequest) -> dict:
        self._guard_hosting()
        ns = await self.hf_namespace()
        return await self._hf_request("POST", f"{HF_ENDPOINTS_API}/endpoint/{ns}", req.payload())

    async def hf_deploy_from_catalog(self, repo_id: str, name: str | None, accelerator: str | None) -> dict:
        self._guard_hosting()
        body = {"namespace": await self.hf_namespace(), "repoId": repo_id}
        if name:
            body["endpointName"] = name
        if accelerator:
            body["accelerator"] = accelerator
        return (await self._hf_request("POST", f"{HF_CATALOG_API}/deploy", body)).get("endpoint", {})

    async def hf_catalog(self) -> list[str]:
        return (await self._hf_request("GET", f"{HF_CATALOG_API}/repo-list")).get("models", [])

    async def hf_hardware(self) -> dict:
        ns = await self.hf_namespace()
        return await self._hf_request("GET", f"{HF_ENDPOINTS_API}/provider/{ns}")

    async def hf_action(self, name: str, action: str) -> dict:
        if action not in {"pause", "resume", "scale-to-zero", "delete"}:
            raise BridgeError(f"Unknown action '{action}'", 404)
        if action in {"resume", "delete"}:
            self._guard_hosting()
        ns = await self.hf_namespace()
        self._endpoint_cache.pop(name, None)
        if action == "delete":
            return await self._hf_request("DELETE", f"{HF_ENDPOINTS_API}/endpoint/{ns}/{name}")
        return await self._hf_request("POST", f"{HF_ENDPOINTS_API}/endpoint/{ns}/{name}/{action}")


def _endpoint_name(repository: str) -> str:
    # HF endpoint names: lowercase letters, digits and dashes, max 32 chars.
    base = re.sub(r"[^a-z0-9-]+", "-", repository.split("/")[-1].lower()).strip("-")
    return (base or "khatti")[:26].rstrip("-") + "-" + format(int(time.time()) % 46656, "03x")


class HostRequest(BaseModel):
    """A dedicated Hugging Face Inference Endpoint for any Hub model."""

    repository: str = Field(description="Hub model id, e.g. Qwen/Qwen2.5-VL-7B-Instruct")
    name: str | None = None
    task: str | None = "text-generation"
    framework: str = "pytorch"
    revision: str | None = None
    accelerator: str = "gpu"
    vendor: str = "aws"
    region: str = "us-east-1"
    instance_type: str = "nvidia-l4"
    instance_size: str = "x1"
    min_replica: int = 0
    max_replica: int = 1
    scale_to_zero_timeout: int = 15  # minutes; keeps an idle endpoint from billing
    type: str = "authenticated"
    image: dict | None = Field(None, description='Serving image, e.g. {"vLLM": {}}; default lets HF choose')
    env: dict[str, str] | None = None

    def payload(self) -> dict:
        model: dict = {
            "framework": self.framework,
            "repository": self.repository,
            "revision": self.revision,
            "image": self.image or {"huggingface": {}},
        }
        if self.task:
            model["task"] = self.task
        if self.env:
            model["env"] = self.env
        return {
            "name": self.name or _endpoint_name(self.repository),
            "type": self.type,
            "provider": {"vendor": self.vendor, "region": self.region},
            "compute": {
                "accelerator": self.accelerator,
                "instanceType": self.instance_type,
                "instanceSize": self.instance_size,
                "scaling": {
                    "minReplica": self.min_replica,
                    "maxReplica": self.max_replica,
                    "scaleToZeroTimeout": self.scale_to_zero_timeout,
                },
            },
            "model": model,
        }


def self_host_recipe(repository: str, name: str | None = None, gpus: int = 1) -> dict:
    """Commands to serve any Hub model with vLLM on your own GPU (e.g. a Nebius VM) and bridge it."""
    provider = name or re.sub(r"[^a-z0-9]+", "", repository.split("/")[-1].lower())[:20] or "selfhosted"
    docker = (
        "docker run --gpus all -p 8000:8000 --ipc=host "
        "-v ~/.cache/huggingface:/root/.cache/huggingface -e HF_TOKEN=$HF_TOKEN "
        f"vllm/vllm-openai:latest --model {repository} --tensor-parallel-size {gpus} "
        "--api-key $KHATTI_UPSTREAM_KEY"
    )
    entry = {"name": provider, "label": f"{repository} (self-hosted)", "base_url": "http://<gpu-host>:8000/v1", "api_key": "<KHATTI_UPSTREAM_KEY>"}
    return {
        "provider": provider,
        "model": f"{provider}/{repository}",
        "docker": docker,
        "khatti_providers_entry": entry,
        "steps": [
            "Start a GPU VM (for example on Nebius AI Cloud) with Docker and the NVIDIA container toolkit.",
            "Run the docker command; vLLM downloads the model from the Hugging Face Hub and serves the OpenAI API.",
            "Add khatti_providers_entry to KHATTI_PROVIDERS and restart the Khatti API.",
            f"Call the model as '{provider}/{repository}' through the bridge.",
        ],
    }


def bridge_from_env(timeout_s: float = 120.0) -> Bridge:
    return Bridge(
        providers_from_env(),
        default=os.getenv("KHATTI_BRIDGE_DEFAULT") or None,
        timeout_s=timeout_s,
        allow_hosting=os.getenv("KHATTI_ALLOW_HOSTING", "") in {"1", "true", "yes"},
        hf_namespace=os.getenv("KHATTI_HF_NAMESPACE") or None,
    )


class BridgeCompleter:
    """Chat completion through the bridge, so any bridged model can power extraction or KYC reading."""

    def __init__(self, bridge: Bridge, model: str):
        self.bridge = bridge
        self.model = model

    async def complete(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1500) -> str:
        resp = await self.bridge.call(
            self.model,
            FEATURE_PATHS["chat"],
            {"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        )
        if resp.status_code >= 400:
            raise BridgeError(f"{self.model}: HTTP {resp.status_code}: {resp.text[:300]}", 502)
        return resp.json()["choices"][0]["message"]["content"] or ""
