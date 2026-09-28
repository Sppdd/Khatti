"""HTTP routes for the model bridge (see bridge.py)."""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .bridge import Bridge, BridgeError, HostRequest, Target, self_host_recipe

router = APIRouter(prefix="/v1/bridge", tags=["bridge"])

# Hop-by-hop and encoding headers must not be copied from the upstream response.
_DROP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def _bridge(request: Request) -> Bridge:
    b: Bridge | None = request.app.state.bridge
    if b is None:
        raise HTTPException(503, "Bridge not configured")
    return b


async def _relay(resp: httpx.Response, stream: bool) -> Response:
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP and k.lower() != "set-cookie"}
    if not stream:
        return Response(resp.content, status_code=resp.status_code, headers=headers)

    async def body():
        try:
            # Decoded bytes, since content-encoding is not forwarded.
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(body(), status_code=resp.status_code, headers=headers)


async def _guard(coro):
    try:
        return await coro
    except BridgeError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Upstream error: {type(exc).__name__}: {exc}") from exc


@router.get("/providers")
async def providers(request: Request) -> dict:
    b = _bridge(request)
    return {"providers": b.describe(), "default": b.default, "hosting": b.allow_hosting}


@router.get("/models")
async def models(request: Request, provider: str | None = None) -> dict:
    return await _guard(_bridge(request).list_models(provider))


# ---- OpenAI-compatible surface: point any OpenAI SDK at <khatti>/v1/bridge/openai ----


@router.get("/openai/models")
async def openai_models(request: Request) -> dict:
    return await _guard(_bridge(request).list_models())


@router.post("/openai/{path:path}")
async def openai_call(path: str, request: Request) -> Response:
    b = _bridge(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Body must be JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise HTTPException(422, "Body needs a 'model' like 'nebius/<model id>'")
    stream = bool(body.get("stream"))
    return await _relay(await _guard(b.call(body["model"], path, body, stream=stream)), stream)


# ---- Hosting: any Hugging Face model on a dedicated endpoint, or self-hosted ----


class CatalogDeploy(BaseModel):
    repo_id: str
    name: str | None = None
    accelerator: str | None = None


@router.get("/hosting/endpoints")
async def hf_endpoints(request: Request) -> dict:
    return {"items": await _guard(_bridge(request).hf_endpoints())}


@router.post("/hosting/endpoints")
async def hf_create(req: HostRequest, request: Request) -> dict:
    return await _guard(_bridge(request).hf_create(req))


@router.get("/hosting/endpoints/{name}")
async def hf_get(name: str, request: Request) -> dict:
    return await _guard(_bridge(request).hf_endpoint(name))


@router.post("/hosting/endpoints/{name}/{action}")
async def hf_action(name: str, action: str, request: Request) -> dict:
    return await _guard(_bridge(request).hf_action(name, action))


@router.delete("/hosting/endpoints/{name}")
async def hf_delete(name: str, request: Request) -> dict:
    return await _guard(_bridge(request).hf_action(name, "delete"))


@router.get("/hosting/catalog")
async def hf_catalog(request: Request) -> dict:
    return {"models": await _guard(_bridge(request).hf_catalog())}


@router.post("/hosting/catalog/deploy")
async def hf_catalog_deploy(req: CatalogDeploy, request: Request) -> dict:
    return await _guard(_bridge(request).hf_deploy_from_catalog(req.repo_id, req.name, req.accelerator))


@router.get("/hosting/hardware")
async def hf_hardware(request: Request) -> dict:
    return await _guard(_bridge(request).hf_hardware())


@router.get("/hosting/recipe")
async def recipe(repo: str, name: str | None = None, gpus: int = 1) -> dict:
    return self_host_recipe(repo, name, gpus)


# ---- Provider-native passthrough for everything else (files, batches, fine-tuning...) ----


@router.api_route("/{provider}/{path:path}", methods=["GET", "POST", "DELETE"])
async def passthrough(provider: str, path: str, request: Request) -> Response:
    b = _bridge(request)
    try:
        p = b.provider(provider)
    except BridgeError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if p.kind != "openai":
        raise HTTPException(400, f"Use /v1/bridge/openai with model '{p.name}/<endpoint>' for {p.label}")

    body = await request.body()
    ctype = request.headers.get("content-type")
    json_body: dict | None = None
    if body and ctype and ctype.startswith("application/json"):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "Body must be JSON") from exc
        if isinstance(parsed, dict):
            json_body = parsed
    # Model ids here are the provider's own (no "<provider>/" prefix).
    target = Target(p, p.base_url, p.api_key, str((json_body or {}).get("model", "")))
    stream = bool(json_body and json_body.get("stream"))
    resp = await _guard(
        b.send(
            target,
            request.method,
            path,
            json_body=json_body,
            content=None if json_body is not None else (body or None),
            content_type=ctype,
            params=dict(request.query_params),
            stream=stream,
        )
    )
    return await _relay(resp, stream)
