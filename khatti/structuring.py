"""Copy-only structuring: map reader lines onto a document schema.

The structurer (Nemotron Super) sees only reader lines, never the image. Whatever it
returns, code enforces the "never invent" guarantee:

1. A value is kept only if it is found verbatim in the cited reader lines. The stored
   value is the text cut from the line itself, not the model's string, so a model can
   never introduce characters the reader did not output. (Only whitespace runs and
   Arabic-Indic vs Western digits are tolerated when locating it.)
2. A value containing the illegible marker is never exposed: value becomes null and the
   partial reading goes to raw_partial for the reviewer.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from .arabic import normalize, to_western_digits
from .llm import ChatClient, parse_json_object
from .models import ExtractedValue, SourceSpan, Transcript, VerifiedValue
from .registry import DocTypeSpec

ILLEGIBLE_MARKERS = ("?", "؟", "␣?")


class Structurer(Protocol):
    async def structure(self, transcript: Transcript, spec: DocTypeSpec) -> dict[str, ExtractedValue]: ...


# ---------------------------------------------------------------- verification

def _canon(text: str) -> tuple[str, list[int]]:
    """Canonicalise for locating (digits, whitespace runs) and keep a map back to original indices."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space or not out:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
            continue
        out.append(to_western_digits(ch))
        idx.append(i)
        prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def locate(value: str, text: str) -> tuple[int, int] | None:
    """Find value in text modulo whitespace runs and digit script; return original [start, end)."""
    needle, _ = _canon(value.strip())
    hay, idx = _canon(text)
    if not needle:
        return None
    pos = hay.find(needle)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(needle) - 1] + 1


def verify(ev: ExtractedValue, transcript: Transcript) -> VerifiedValue:
    if ev.value is None or not str(ev.value).strip():
        return VerifiedValue(value=None, reason=ev.reason or "not_present")

    lines = {l.line_id: l.text for l in transcript.lines}
    cited = [s.line_id for s in ev.spans if s.line_id in lines]
    # 1) explicit spans that reproduce the value
    if ev.spans and all(s.line_id in lines for s in ev.spans):
        cut = " ".join(lines[s.line_id][s.start : s.end] for s in ev.spans)
        if 0 <= min(s.start for s in ev.spans) and _canon(cut)[0] == _canon(ev.value)[0] and cut.strip():
            return _finish(cut.strip(), [s.line_id for s in ev.spans])
    # 2) locate the value inside the cited lines (individually, then joined), else anywhere
    candidates = [[c] for c in dict.fromkeys(cited)]
    if len(cited) > 1:
        candidates.append(list(dict.fromkeys(cited)))
    # Uncited lines are searched only for values long enough not to match by accident.
    if len(ev.value.strip()) >= 4 or not cited:
        candidates += [[l.line_id] for l in transcript.lines if l.line_id not in cited]
    for ids in candidates:
        text = " ".join(lines[i] for i in ids)
        hit = locate(ev.value, text)
        if hit:
            return _finish(text[hit[0] : hit[1]], ids)
    return VerifiedValue(value=None, reason="not_verbatim")


def _finish(text: str, line_ids: list[str]) -> VerifiedValue:
    if any(m in text for m in ILLEGIBLE_MARKERS):
        return VerifiedValue(value=None, source_line_ids=line_ids, reason="illegible", raw_partial=text)
    return VerifiedValue(value=text, source_line_ids=line_ids)


# ---------------------------------------------------------------- Nemotron Super

STRUCTURE_PROMPT = """You map a document reader's transcribed lines onto fields. You never see the image.

Copy-only rules:
- Every value must be copied character-for-character from ONE OR MORE of the given lines.
  Do not fix spelling, translate, transliterate, reformat dates or convert digits.
- For each field give the line_ids you copied from and, if you can, char spans
  [start, end) into each line's text.
- Copy only the value, not its printed label (e.g. for "الاسم: علي" copy "علي").
- If the field is absent use null with reason "not_present". If it is present but
  contains '?', copy it as is (with the '?'). Never fill a field from general knowledge."""


def structure_schema(spec: DocTypeSpec) -> dict:
    field_schema = {
        "type": "object",
        "properties": {
            "value": {"type": ["string", "null"]},
            "line_ids": {"type": "array", "items": {"type": "string"}},
            "spans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"line_id": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
                    "required": ["line_id", "start", "end"],
                },
            },
            "reason": {"type": ["string", "null"]},
        },
        "required": ["value"],
    }
    return {
        "type": "object",
        "properties": {"fields": {"type": "object", "properties": {n: field_schema for n in spec.field_names}}},
        "required": ["fields"],
    }


def parse_extracted(obj: dict, spec: DocTypeSpec) -> dict[str, ExtractedValue]:
    fields = obj.get("fields") if isinstance(obj.get("fields"), dict) else obj
    out: dict[str, ExtractedValue] = {}
    for name in spec.field_names:  # only schema fields: invented keys are dropped
        raw = fields.get(name)
        if not isinstance(raw, dict):
            out[name] = ExtractedValue(value=None, reason="not_present")
            continue
        spans = []
        for s in raw.get("spans") or []:
            try:
                spans.append(SourceSpan(line_id=str(s["line_id"]), start=int(s["start"]), end=int(s["end"])))
            except (KeyError, TypeError, ValueError):
                spans = []
                break
        if not spans:
            # line citations without offsets: verify() locates the value inside them
            spans = [SourceSpan(line_id=str(i), start=0, end=0) for i in raw.get("line_ids") or []]
        value = raw.get("value")
        out[name] = ExtractedValue(value=None if value is None else str(value), spans=spans, reason=raw.get("reason"))
    return out


class NemotronStructurer:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = client.endpoint.model

    async def structure(self, transcript: Transcript, spec: DocTypeSpec) -> dict[str, ExtractedValue]:
        payload = {
            "document": spec.label,
            "fields": {f.name: f.description for f in spec.fields},
            "lines": [{"line_id": l.line_id, "text": l.text} for l in transcript.lines],
        }
        reply = await self.client.complete(
            [
                {"role": "system", "content": STRUCTURE_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            json_schema=structure_schema(spec),
            purpose=f"structure:{spec.key}",
        )
        return parse_extracted(parse_json_object(reply), spec)


# ---------------------------------------------------------------- deterministic fallback

_LABEL_SEP = re.compile(r"^\s*(.+?)\s*[:：]\s*(.*)$")


class LabelStructurer:
    """Deterministic structurer for labelled layouts ("<label_ar>: <value>" per line).

    Used for tests, offline demos and as a baseline in evals. Copy-only by construction.
    """

    async def structure(self, transcript: Transcript, spec: DocTypeSpec) -> dict[str, ExtractedValue]:
        by_label = {normalize(f.label_ar): f.name for f in spec.fields if f.label_ar}
        out = {n: ExtractedValue(value=None, reason="not_present") for n in spec.field_names}
        for line in transcript.lines:
            m = _LABEL_SEP.match(line.text)
            if not m:
                continue
            name = by_label.get(normalize(m.group(1)))
            if name and m.group(2).strip():
                start = m.start(2)
                out[name] = ExtractedValue(
                    value=m.group(2).strip(), spans=[SourceSpan(line_id=line.line_id, start=start, end=len(line.text.rstrip()))]
                )
        return out
