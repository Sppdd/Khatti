"""Week-0 model inventory (plan T): what does Token Factory actually serve on YOUR key?

    python -m jobs.inventory [--probe auto|all|<id,id,...>] [--out eval/inventory]

1. GET /v1/models?verbose=true and record every model.
2. Live-probe candidates: text chat, image input, JSON mode (json_object), json_schema output,
   and logprobs. Marketing pages and other builders disagree about image-input Nemotron; this
   settles it for the key in hand.

Writes inventory.json and inventory.md, including a KHATTI_READERS suggestion.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

from khatti.config import DEFAULT_FAST_MODEL, DEFAULT_REVIEWER_MODEL, DEFAULT_STRUCTURER_MODEL, TOKEN_FACTORY_URL
from khatti.llm import parse_json_object

PROBE_NUMBER = "481527"
VISION_HINT = re.compile(r"(vl|vision|omni|gemma-3|kimi|llava|pixtral|qwen2\.5-vl|qwen3-vl|deepseek.*(vl|ocr))", re.I)
PLAN_MODELS = [DEFAULT_FAST_MODEL, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", DEFAULT_STRUCTURER_MODEL, DEFAULT_REVIEWER_MODEL]


def probe_image() -> str:
    img = np.full((120, 420, 3), 255, np.uint8)
    cv2.putText(img, " ".join(PROBE_NUMBER), (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    return "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()


def looks_vision(model: dict) -> bool:
    blob = json.dumps(model).lower()
    return bool(VISION_HINT.search(model.get("id", ""))) or "image" in blob and "modalit" in blob


async def _chat(http: httpx.AsyncClient, url: str, key: str, body: dict) -> tuple[int, dict | str, float]:
    t = time.perf_counter()
    try:
        r = await http.post(f"{url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.HTTPError as exc:
        return 0, type(exc).__name__, time.perf_counter() - t
    try:
        payload = r.json()
    except ValueError:
        payload = r.text[:300]
    return r.status_code, payload, time.perf_counter() - t


def _content(payload) -> str:
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (TypeError, KeyError, IndexError):
        return ""


def _error(status: int, payload) -> str:
    if status == 200:
        return ""
    return (json.dumps(payload)[:200] if isinstance(payload, dict) else str(payload)[:200]) or f"HTTP {status}"


async def probe(http: httpx.AsyncClient, url: str, key: str, model: str, image: str) -> dict:
    base = {"model": model, "max_tokens": 200, "temperature": 0}
    out: dict = {"model": model}

    s, p, dt = await _chat(http, url, key, {**base, "messages": [{"role": "user", "content": "Reply with the word OK."}]})
    out["text"] = {"ok": s == 200 and bool(_content(p)), "latency_s": round(dt, 2), "error": _error(s, p)}
    if not out["text"]["ok"]:
        return out

    ask = f'Read the number in the image. Reply with JSON only: {{"number": "..."}}'
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image}}, {"type": "text", "text": ask}]}]
    s, p, dt = await _chat(http, url, key, {**base, "messages": msgs})
    got = re.sub(r"\D", "", _content(p))
    out["image"] = {"ok": s == 200 and PROBE_NUMBER in got, "accepted": s == 200, "latency_s": round(dt, 2),
                    "answer": _content(p)[:80], "error": _error(s, p)}

    jmsg = [{"role": "user", "content": 'Reply with JSON {"answer": "yes"}.'}]
    s, p, _ = await _chat(http, url, key, {**base, "messages": jmsg, "response_format": {"type": "json_object"}})
    out["json_object"] = _json_ok(s, p)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    s, p, _ = await _chat(http, url, key, {**base, "messages": jmsg,
                                            "response_format": {"type": "json_schema", "json_schema": {"name": "a", "schema": schema}}})
    out["json_schema"] = _json_ok(s, p)
    s, p, _ = await _chat(http, url, key, {**base, "messages": jmsg, "logprobs": True, "top_logprobs": 2})
    lp = (p.get("choices") or [{}])[0].get("logprobs") if isinstance(p, dict) else None
    out["logprobs"] = {"ok": s == 200 and bool(lp), "error": _error(s, p)}
    return out


def _json_ok(status: int, payload) -> dict:
    if status != 200:
        return {"ok": False, "error": _error(status, payload)}
    try:
        return {"ok": parse_json_object(_content(payload)).get("answer") is not None, "error": ""}
    except (ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "reply was not JSON"}


def markdown(models: list[dict], probes: list[dict], url: str) -> str:
    yes = lambda d, k: "✅" if d.get(k, {}).get("ok") else ("—" if k not in d else "❌")  # noqa: E731
    lines = [f"# Token Factory inventory ({url})", "", f"{len(models)} models listed.", "",
             "| model | text | image (read {}) | json_object | json_schema | logprobs |".format(PROBE_NUMBER),
             "|---|---|---|---|---|---|"]
    for p in probes:
        img = p.get("image", {})
        img_cell = "—" if not img else ("✅" if img.get("ok") else ("accepted, wrong answer" if img.get("accepted") else "❌"))
        lines.append(f"| `{p['model']}` | {yes(p, 'text')} | {img_cell} | {yes(p, 'json_object')} | {yes(p, 'json_schema')} | {yes(p, 'logprobs')} |")
    readers = [p["model"] for p in probes if p.get("image", {}).get("ok")]
    lines += ["", "## Suggested reader B candidates (image input verified)", ""]
    lines += [f"- `{m}` (json_mode: {'true' if next(p for p in probes if p['model'] == m).get('json_object', {}).get('ok') else 'false'})"
              for m in readers] or ["- none: only the self-hosted Omni endpoint can read images on this key"]
    lines += ["", "All listed models:", ""] + [f"- `{m.get('id')}`" for m in models]
    return "\n".join(lines)


async def run(args, transport: httpx.AsyncBaseTransport | None = None) -> Path:
    url = args.url.rstrip("/")
    key = args.key or os.getenv("KHATTI_TOKEN_FACTORY_KEY", "")
    if not key:
        raise SystemExit("set KHATTI_TOKEN_FACTORY_KEY")
    async with httpx.AsyncClient(timeout=120, transport=transport) as http:
        r = await http.get(f"{url}/models", params={"verbose": "true"}, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        models = r.json().get("data", [])
        ids = [m.get("id") for m in models]
        if args.probe == "all":
            targets = ids
        elif args.probe == "auto":
            targets = [m["id"] for m in models if looks_vision(m)] + [m for m in PLAN_MODELS if m in ids]
        else:
            targets = [t for t in args.probe.split(",") if t]
        image = probe_image()
        sem = asyncio.Semaphore(4)

        async def one(m):
            async with sem:
                return await probe(http, url, key, m, image)

        probes = await asyncio.gather(*(one(m) for m in dict.fromkeys(targets)))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "inventory.json").write_text(json.dumps({"url": url, "models": models, "probes": probes}, indent=2))
    (args.out / "inventory.md").write_text(markdown(models, probes, url))
    print((args.out / "inventory.md").read_text())
    return args.out / "inventory.md"


def main(argv: list[str] | None = None, transport=None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("KHATTI_TOKEN_FACTORY_URL", TOKEN_FACTORY_URL))
    ap.add_argument("--key", default="")
    ap.add_argument("--probe", default="auto", help="auto (vision-looking + plan models) | all | comma-separated ids")
    ap.add_argument("--out", type=Path, default=Path("eval/inventory"))
    return asyncio.run(run(ap.parse_args(argv), transport))


if __name__ == "__main__":
    main()
