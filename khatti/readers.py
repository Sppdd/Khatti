"""Image readers: transcribe every line of a document image, type-agnostic.

Readers never see a schema. They return lines (with ids and optional boxes) and a
one-line caption; mapping lines to fields is the structurer's job.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Protocol

from .llm import ChatClient, EndpointUnavailable, parse_json_object
from .models import Line, ReaderStatus, Transcript

TRANSCRIBE_PROMPT = """Transcribe this document photo line by line.
The first image is the original photo; a second image, if present, is a contrast-enhanced copy of the same photo.

Rules:
- Copy text exactly as printed or handwritten. Keep Arabic and Kurdish in their own script and keep
  Arabic-Indic digits as they appear. Do not translate, transliterate, correct or complete anything.
- If a character is not legible, output ? in its place. Never guess a character.
- One entry per visual line, in reading order (right-to-left for Arabic).
- bbox is [x0, y0, x1, y1] relative to the image (0..1), or null if unsure.
- caption: one short English line saying what kind of document this is and which side is shown.

Reply with JSON only:
{"caption": "...", "lines": [{"line_id": "L1", "text": "...", "bbox": [0.1, 0.1, 0.9, 0.2]}]}"""

TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "text": {"type": "string"},
                    "bbox": {"anyOf": [{"type": "array", "items": {"type": "number"}}, {"type": "null"}]},
                },
                "required": ["line_id", "text"],
            },
        },
    },
    "required": ["caption", "lines"],
}


class Reader(Protocol):
    name: str
    samples: int

    async def transcribe(self, images: list[tuple[bytes, str]]) -> list[Transcript]: ...


def parse_transcript(obj: dict, reader: str, sample: int) -> Transcript:
    lines = []
    seen: set[str] = set()
    for i, raw in enumerate(obj.get("lines") or [], 1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        line_id = str(raw.get("line_id") or f"L{i}")
        if line_id in seen:  # ids must be unique for span verification
            line_id = f"{line_id}_{i}"
        seen.add(line_id)
        bbox = raw.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox)):
            bbox = None
        lines.append(Line(line_id=line_id, text=text, bbox=bbox))
    return Transcript(reader=reader, sample=sample, caption=str(obj.get("caption") or "")[:300], lines=lines)


class VisionChatReader:
    """Reader backed by any OpenAI-compatible vision chat endpoint."""

    def __init__(self, client: ChatClient, samples: int = 1, sample_temperature: float = 0.7):
        self.client = client
        self.name = client.endpoint.name
        self.samples = max(1, samples)
        self.sample_temperature = sample_temperature

    async def _one(self, content: list[dict], sample: int) -> Transcript:
        temperature = 0.0 if self.samples == 1 else self.sample_temperature
        try:
            done = await self.client.complete_full(
                [{"role": "user", "content": content}],
                temperature=temperature,
                max_tokens=4000,
                json_schema=TRANSCRIPT_SCHEMA,
                purpose=f"transcribe#{sample}",
                logprobs=self.client.endpoint.logprobs,
            )
            t = parse_transcript(parse_json_object(done.text), self.name, sample)
            if done.token_logprobs:
                for line in t.lines:
                    line.conf = done.span_confidence(line.text)
            return t
        except EndpointUnavailable as exc:
            return Transcript(reader=self.name, sample=sample, status=ReaderStatus.UNAVAILABLE, error=str(exc))
        except Exception as exc:
            return Transcript(reader=self.name, sample=sample, status=ReaderStatus.ERROR, error=f"{type(exc).__name__}: {exc}")

    async def transcribe(self, images: list[tuple[bytes, str]]) -> list[Transcript]:
        content: list[dict] = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
            for data, mime in images
        ]
        content.append({"type": "text", "text": TRANSCRIBE_PROMPT})
        return list(await asyncio.gather(*(self._one(content, s) for s in range(self.samples))))


class StaticReader:
    """Returns canned transcripts. Used for tests and offline demos."""

    def __init__(self, name: str, by_image: dict[bytes, list[list[str]] | tuple[str, list[list[str]]]], status=ReaderStatus.OK):
        """by_image: image bytes -> (caption, samples) where each sample is a list of line texts."""
        self.name = name
        self._by_image = by_image
        self._status = status
        self.samples = max((len(v[1]) for v in by_image.values()), default=1)

    async def transcribe(self, images: list[tuple[bytes, str]]) -> list[Transcript]:
        if self._status is not ReaderStatus.OK:
            return [Transcript(reader=self.name, status=self._status, error="static failure")]
        caption, samples = self._by_image.get(images[0][0], ("", [[]]))
        return [
            Transcript(
                reader=self.name,
                sample=s,
                caption=caption,
                lines=[Line(line_id=f"L{i}", text=t) for i, t in enumerate(texts, 1)],
            )
            for s, texts in enumerate(samples)
        ]
