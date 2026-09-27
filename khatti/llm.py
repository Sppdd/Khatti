"""Minimal client for OpenAI-compatible chat endpoints (Token Factory, Nebius Serverless)."""

from __future__ import annotations

import json
import re

import httpx

from .config import EndpointConfig

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class ChatClient:
    def __init__(self, endpoint: EndpointConfig, timeout_s: float = 60.0, http: httpx.AsyncClient | None = None):
        self.endpoint = endpoint
        self._http = http or httpx.AsyncClient(timeout=timeout_s)

    async def complete(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1500) -> str:
        resp = await self._http.post(
            f"{self.endpoint.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.endpoint.api_key}"},
            json={
                "model": self.endpoint.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""

    async def aclose(self) -> None:
        await self._http.aclose()


def parse_json_object(text: str) -> dict:
    """Extract a JSON object from a model reply (tolerates think blocks and code fences)."""
    text = _FENCE.sub("", _THINK.sub("", text)).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in model reply")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model reply is not a JSON object")
    return obj
