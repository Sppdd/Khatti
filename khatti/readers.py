"""Image readers. Each one turns a document image into a flat field dict."""

from __future__ import annotations

import base64
from typing import Protocol

from .llm import ChatClient, parse_json_object
from .models import EXPECTED_FIELDS, DocumentInput, ReaderResult

_DOC_NAMES = {
    "national_id": "Iraqi unified national ID card (البطاقة الوطنية الموحدة)",
    "passport": "Iraqi passport (جواز سفر عراقي) data page",
    "residence_card": "Iraqi residence card (بطاقة السكن)",
}


class Reader(Protocol):
    name: str

    async def read(self, doc: DocumentInput) -> ReaderResult: ...


def build_prompt(doc: DocumentInput) -> str:
    fields = ", ".join(EXPECTED_FIELDS[doc.doc_type])
    return (
        f"This image is an {_DOC_NAMES[doc.doc_type.value]}.\n"
        f"Transcribe these fields exactly as printed: {fields}.\n"
        "Rules:\n"
        "- Keep Arabic text in Arabic script; do not translate or transliterate.\n"
        "- Dates as YYYY-MM-DD. Digits as Western digits 0-9.\n"
        "- sex as M or F. MRZ lines verbatim including '<' fillers.\n"
        "- Use null for any field you cannot read. Never guess.\n"
        "Reply with a single JSON object mapping field name to string or null."
    )


class VisionChatReader:
    """Reader backed by any OpenAI-compatible vision chat endpoint."""

    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.name

    async def read(self, doc: DocumentInput) -> ReaderResult:
        data_url = f"data:{doc.mime_type};base64,{base64.b64encode(doc.image).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": build_prompt(doc)},
                ],
            }
        ]
        try:
            obj = parse_json_object(await self.client.complete(messages))
        except Exception as exc:  # network, HTTP or parse failure: reported, not raised
            return ReaderResult(reader=self.name, error=f"{type(exc).__name__}: {exc}")
        fields = {k: (None if v is None else str(v).strip() or None) for k, v in obj.items()}
        return ReaderResult(reader=self.name, fields=fields)


class StaticReader:
    """Returns canned fields. Used for tests and offline demos."""

    def __init__(self, name: str, fields: dict[str, dict[str, str | None]], error: str | None = None):
        self.name = name
        self._fields = fields  # doc_type value -> fields
        self._error = error

    async def read(self, doc: DocumentInput) -> ReaderResult:
        if self._error:
            return ReaderResult(reader=self.name, error=self._error)
        return ReaderResult(reader=self.name, fields=dict(self._fields.get(doc.doc_type.value, {})))
