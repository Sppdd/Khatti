"""Minimal client for OpenAI-compatible chat endpoints (Token Factory, Nebius Serverless, vLLM).

Every call is recorded in the active CallLog (if any) for the audit trail: model id,
prompt hash, input image hashes, output, latency and token usage.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field

import httpx

from .config import EndpointConfig

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_UNAVAILABLE_STATUS = {502, 503, 504}


class EndpointUnavailable(Exception):
    """The endpoint could not be reached or is not serving (e.g. a stopped GPU endpoint)."""


@dataclass
class CallRecord:
    endpoint: str
    model: str
    purpose: str
    prompt_sha256: str
    image_sha256: list[str]
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    ok: bool
    output: str | None = None
    error: str | None = None


@dataclass
class CallLog:
    records: list[CallRecord] = field(default_factory=list)


current_call_log: contextvars.ContextVar[CallLog | None] = contextvars.ContextVar("khatti_call_log", default=None)


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _hash_prompt(messages: list[dict]) -> tuple[str, list[str]]:
    """Hash the prompt with images replaced by their hashes (so the hash is stable and small)."""
    images: list[str] = []

    def strip(part):
        if isinstance(part, dict) and part.get("type") == "image_url":
            h = sha256(part["image_url"]["url"])
            images.append(h)
            return {"type": "image_url", "sha256": h}
        return part

    slim = [
        {**m, "content": [strip(p) for p in m["content"]]} if isinstance(m.get("content"), list) else m
        for m in messages
    ]
    return sha256(json.dumps(slim, ensure_ascii=False, sort_keys=True)), images


@dataclass
class Completion:
    text: str
    token_logprobs: list[tuple[str, float]] | None = None  # when requested and returned

    def span_confidence(self, fragment: str) -> float | None:
        """exp(mean logprob) of the tokens that produced `fragment` in the reply, if locatable."""
        if not self.token_logprobs or not fragment:
            return None
        start = self.text.find(fragment)
        if start < 0:
            start = self.text.find(json.dumps(fragment, ensure_ascii=False)[1:-1])
        if start < 0:
            return None
        end, pos, picked = start + len(fragment), 0, []
        for tok, lp in self.token_logprobs:
            nxt = pos + len(tok)
            if nxt > start and pos < end:
                picked.append(lp)
            pos = nxt
        return math.exp(sum(picked) / len(picked)) if picked else None


class ChatClient:
    def __init__(self, endpoint: EndpointConfig, timeout_s: float = 60.0, http: httpx.AsyncClient | None = None):
        self.endpoint = endpoint
        self._http = http or httpx.AsyncClient(timeout=timeout_s)

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_schema: dict | None = None,
        json_mode: bool = True,
        purpose: str = "",
    ) -> str:
        done = await self.complete_full(messages, temperature, max_tokens, json_schema, json_mode, purpose)
        return done.text

    async def complete_full(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_schema: dict | None = None,
        json_mode: bool = True,
        purpose: str = "",
        logprobs: bool = False,
    ) -> "Completion":
        body: dict = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None and self.endpoint.json_mode:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": json_schema}}
        elif json_mode and self.endpoint.json_mode:
            body["response_format"] = {"type": "json_object"}
        if logprobs:
            body["logprobs"] = True

        started = time.perf_counter()
        usage: dict = {}
        output: str | None = None
        error: str | None = None
        try:
            try:
                resp = await self._http.post(
                    f"{self.endpoint.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.endpoint.api_key}"},
                    json=body,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise EndpointUnavailable(f"{self.endpoint.name}: {type(exc).__name__}") from exc
            if resp.status_code in _UNAVAILABLE_STATUS:
                raise EndpointUnavailable(f"{self.endpoint.name}: HTTP {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
            usage = payload.get("usage") or {}
            choice = payload["choices"][0]
            output = choice["message"]["content"] or ""
            tokens = None
            lp = (choice.get("logprobs") or {}).get("content") if logprobs else None
            if lp:
                tokens = [(t.get("token", ""), float(t.get("logprob", 0.0))) for t in lp]
            return Completion(output, tokens)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            log = current_call_log.get()
            if log is not None:
                prompt_hash, images = _hash_prompt(messages)
                log.records.append(
                    CallRecord(
                        endpoint=self.endpoint.name,
                        model=self.endpoint.model,
                        purpose=purpose,
                        prompt_sha256=prompt_hash,
                        image_sha256=images,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        ok=error is None,
                        output=output,
                        error=error,
                    )
                )

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
