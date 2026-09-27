"""General photo-to-data extraction: any photo in, one structured JSON record out.

Used by the Khatti mobile app. The same schema serves price collection (receipts,
shelf tags), a personal memory library, and prompts for generative media tools.
"""

from __future__ import annotations

import base64
import re
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from .arabic import to_western_digits
from .llm import parse_json_object


class ExtractMode(str, Enum):
    AUTO = "auto"  # let the model decide what matters in the photo
    PRICES = "prices"  # receipts, shelf tags, menus: every item with its price
    MIND = "mind"  # save-for-later memory: what is this, why might it matter
    PROMPT = "prompt"  # turn a real-world scene into a generative-media prompt
    TEXT = "text"  # faithful transcription of any text in the photo


class Kind(str, Enum):
    RECEIPT = "receipt"
    PRICE_TAG = "price_tag"
    MENU = "menu"
    DOCUMENT = "document"
    NOTE = "note"
    SCREENSHOT = "screenshot"
    PRODUCT = "product"
    SCENE = "scene"
    OTHER = "other"


class PriceItem(BaseModel):
    name: str
    price: float | None = None
    currency: str | None = None
    quantity: float | None = None
    unit: str | None = None


class StoreInfo(BaseModel):
    name: str | None = None
    location: str | None = None
    date: str | None = None
    total: float | None = None
    currency: str | None = None


class VisualPrompt(BaseModel):
    subject: str = ""
    style: str = ""
    mood: str = ""
    lighting: str = ""
    colors: list[str] = Field(default_factory=list)
    composition: str = ""
    camera: str = ""
    prompt: str = ""  # ready-to-paste prompt for image/video models
    negative_prompt: str = ""


class ExtractResult(BaseModel):
    mode: ExtractMode
    kind: Kind = Kind.OTHER
    title: str = ""
    summary: str = ""
    language: str | None = None  # "ar", "en", "mixed", ...
    text: str | None = None  # verbatim transcription, reading order
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)  # any other key facts
    items: list[PriceItem] = Field(default_factory=list)
    store: StoreInfo | None = None
    prompt: VisualPrompt | None = None
    model: str | None = None


_SCHEMA = """{
  "kind": "receipt|price_tag|menu|document|note|screenshot|product|scene|other",
  "title": "short title, max 8 words",
  "summary": "1-3 sentences: what this is and why it matters",
  "language": "main language code of text in the photo (ar, en, mixed) or null",
  "text": "all readable text verbatim in reading order, or null",
  "tags": ["3-8 lowercase keywords"],
  "fields": {"key": "value for any other useful fact (dates, phone numbers, brands, sizes...)"},
  "items": [{"name": "item", "price": 0.0, "currency": "IQD", "quantity": 1, "unit": "kg"}],
  "store": {"name": null, "location": null, "date": "YYYY-MM-DD", "total": null, "currency": null},
  "prompt": {"subject": "", "style": "", "mood": "", "lighting": "", "colors": ["#hex or name"],
             "composition": "", "camera": "", "prompt": "one paragraph prompt", "negative_prompt": ""}
}"""

_FOCUS = {
    ExtractMode.AUTO: "Decide what matters most in the photo and fill the fields that apply.",
    ExtractMode.PRICES: (
        "Focus on prices. List every product with its price in items. Put the shop name, "
        "location, date and total in store. Keep product names in their original script."
    ),
    ExtractMode.MIND: (
        "This photo is being saved to a personal memory library. Make title, summary and "
        "tags useful for finding it again months later."
    ),
    ExtractMode.PROMPT: (
        "Describe the scene as a creative director would, so an image or video model can "
        "recreate its look. Fill prompt completely; prompt.prompt must be self-contained."
    ),
    ExtractMode.TEXT: "Transcribe every piece of text exactly as printed into text; keep line breaks.",
}


def build_extract_prompt(mode: ExtractMode) -> str:
    return (
        "Turn this photo into structured data an AI can use later.\n"
        f"{_FOCUS[mode]}\n"
        "Rules:\n"
        "- Keep Arabic text in Arabic script; do not translate text you transcribe.\n"
        "- Prices as numbers with Western digits; currency as ISO code when you can tell (IQD, USD...).\n"
        "- Use null or empty lists for anything absent. Never invent prices or text.\n"
        "- items is empty unless prices are visible. store is null unless it is a shop document.\n"
        "Reply with one JSON object in exactly this shape:\n" + _SCHEMA
    )


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def to_number(value: object) -> float | None:
    """Parse model output like 1,250 / ١٬٢٥٠ / "3.500 IQD" into a float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = to_western_digits(str(value)).replace("٬", "").replace("،", "").replace(",", "").replace("٫", ".")
    m = _NUMBER.search(text.replace(" ", ""))
    return float(m.group()) if m else None


def _str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def coerce_result(obj: dict, mode: ExtractMode, model: str | None = None) -> ExtractResult:
    """Build an ExtractResult from a loosely-shaped model reply, dropping what does not fit."""
    kind = obj.get("kind")
    items = []
    for it in _list(obj.get("items")):
        if isinstance(it, dict) and _str(it.get("name")):
            items.append(
                PriceItem(
                    name=_str(it["name"]),
                    price=to_number(it.get("price")),
                    currency=_str(it.get("currency")),
                    quantity=to_number(it.get("quantity")),
                    unit=_str(it.get("unit")),
                )
            )
    store = obj.get("store")
    store_info = None
    if isinstance(store, dict) and any(store.get(k) for k in ("name", "location", "date", "total")):
        store_info = StoreInfo(
            name=_str(store.get("name")),
            location=_str(store.get("location")),
            date=_str(store.get("date")),
            total=to_number(store.get("total")),
            currency=_str(store.get("currency")),
        )
    prompt = obj.get("prompt")
    visual = None
    if isinstance(prompt, dict) and _str(prompt.get("prompt")):
        visual = VisualPrompt(
            **{k: _str(prompt.get(k)) or "" for k in ("subject", "style", "mood", "lighting",
                                                       "composition", "camera", "prompt", "negative_prompt")},
            colors=[str(c) for c in _list(prompt.get("colors")) if c],
        )
    fields = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
    return ExtractResult(
        mode=mode,
        kind=kind if kind in Kind._value2member_map_ else Kind.OTHER,
        title=_str(obj.get("title")) or "",
        summary=_str(obj.get("summary")) or "",
        language=_str(obj.get("language")),
        text=_str(obj.get("text")),
        tags=[str(t).strip().lower() for t in _list(obj.get("tags")) if str(t).strip()][:12],
        fields={str(k): str(v) for k, v in fields.items() if v not in (None, "")},
        items=items,
        store=store_info,
        prompt=visual,
        model=model,
    )


class Completer(Protocol):
    async def complete(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1500) -> str: ...


class PhotoExtractor:
    def __init__(self, client: Completer, model: str | None = None):
        self.client = client
        self.model = model

    async def extract(self, image: bytes, mime_type: str, mode: ExtractMode) -> ExtractResult:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": build_extract_prompt(mode)},
                ],
            }
        ]
        reply = await self.client.complete(messages, temperature=0.2 if mode is ExtractMode.PROMPT else 0.0,
                                           max_tokens=2500)
        return coerce_result(parse_json_object(reply), mode, self.model)
