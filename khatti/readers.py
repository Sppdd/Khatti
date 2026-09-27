"""Image readers. Each one turns a document image into a flat field dict."""

from __future__ import annotations

import base64
import json
from typing import Protocol

from .llm import ChatClient, EndpointUnavailable, parse_json_object
from .models import DocumentInput, ReaderResult, ReaderStatus
from .registry import DocTypeSpec


class Reader(Protocol):
    name: str

    async def read(self, doc: DocumentInput, spec: DocTypeSpec) -> ReaderResult: ...


def build_prompt(spec: DocTypeSpec) -> str:
    fields = "\n".join(f"- {f.name}: {f.description}" for f in spec.fields)
    return (
        f"This image is an {spec.label}.\n"
        f"Transcribe these fields exactly as printed:\n{fields}\n"
        "Rules:\n"
        "- Keep Arabic text in Arabic script; do not translate or transliterate.\n"
        "- Dates as YYYY-MM-DD. Digits as Western digits 0-9.\n"
        "- Use null for any field you cannot read. A blank field is better than a guess.\n"
        "Reply with a single JSON object mapping field name to string or null."
    )


STRUCTURER_PROMPT = """You convert a document reader's free-text output into JSON.
Use only what the text states; never fill a field the text does not contain (use null).
Do not correct, translate or transliterate values. Reply with one JSON object whose keys
are exactly the requested field names."""


class Structurer:
    """Nemotron Super: maps reader output that is not valid JSON onto the schema."""

    def __init__(self, client: ChatClient):
        self.client = client

    async def structure(self, raw: str, spec: DocTypeSpec) -> dict:
        reply = await self.client.complete(
            [
                {"role": "system", "content": STRUCTURER_PROMPT},
                {"role": "user", "content": json.dumps({"fields": spec.field_names, "reader_output": raw}, ensure_ascii=False)},
            ]
        )
        return parse_json_object(reply)


class VisionChatReader:
    """Reader backed by any OpenAI-compatible vision chat endpoint."""

    def __init__(self, client: ChatClient, structurer: Structurer | None = None):
        self.client = client
        self.structurer = structurer
        self.name = client.endpoint.name

    async def read(self, doc: DocumentInput, spec: DocTypeSpec) -> ReaderResult:
        data_url = f"data:{doc.mime_type};base64,{base64.b64encode(doc.image).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": build_prompt(spec)},
                ],
            }
        ]
        try:
            raw = await self.client.complete(messages)
        except EndpointUnavailable as exc:
            return ReaderResult(reader=self.name, status=ReaderStatus.UNAVAILABLE, error=str(exc))
        except Exception as exc:
            return ReaderResult(reader=self.name, status=ReaderStatus.ERROR, error=f"{type(exc).__name__}: {exc}")

        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            if self.structurer is None:
                return ReaderResult(reader=self.name, status=ReaderStatus.ERROR, error=str(exc))
            try:
                obj = await self.structurer.structure(raw, spec)
            except Exception as exc2:
                return ReaderResult(reader=self.name, status=ReaderStatus.ERROR, error=f"structurer: {exc2}")

        fields = {k: (None if v is None else str(v).strip() or None) for k, v in obj.items()}
        return ReaderResult(reader=self.name, fields=fields)


class StaticReader:
    """Returns canned fields. Used for tests and offline demos."""

    def __init__(
        self,
        name: str,
        fields: dict[str, dict[str, str | None]],
        status: ReaderStatus = ReaderStatus.OK,
    ):
        self.name = name
        self._fields = fields  # doc_type -> fields
        self._status = status

    async def read(self, doc: DocumentInput, spec: DocTypeSpec) -> ReaderResult:
        if self._status is not ReaderStatus.OK:
            return ReaderResult(reader=self.name, status=self._status, error="static failure")
        return ReaderResult(reader=self.name, fields=dict(self._fields.get(doc.doc_type, {})))
