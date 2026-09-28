"""Document classification: layout cues (aspect ratio) + label evidence in the reader lines
+ Nemotron Lightning over the reader caption. The session declares the expected slot, so a
confident disagreement becomes an explicit "wrong document in slot" exception."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .arabic import normalize
from .llm import ChatClient, parse_json_object
from .models import Transcript
from .registry import ASPECT, DocTypeSpec, Shape

OTHER = "other"


@dataclass(frozen=True)
class Classification:
    doc_type: str
    confidence: float
    evidence: str


def shape_of(aspect: float | None) -> Shape | None:
    if not aspect:
        return None
    for shape, ref in ASPECT.items():
        if abs(aspect - ref) / ref < 0.08:
            return shape
    return None


def label_scores(transcripts: list[Transcript], specs: list[DocTypeSpec]) -> dict[str, float]:
    """Share of each type's printed labels found in the lines (normalised)."""
    text = normalize(" \n".join(l.text for t in transcripts for l in t.lines))
    scores = {}
    for s in specs:
        labels = [normalize(f.label_ar) for f in s.fields if f.label_ar]
        title = normalize(s.label_ar) if s.label_ar else ""
        hits = sum(1 for l in labels if l and l in text) + (2 if title and title in text else 0)
        scores[s.key] = hits / (len(labels) + 2)
    return scores


class Classifier(Protocol):
    async def classify(self, transcripts: list[Transcript], aspect: float | None, specs: list[DocTypeSpec]) -> Classification: ...


class RuleClassifier:
    async def classify(self, transcripts: list[Transcript], aspect: float | None, specs: list[DocTypeSpec]) -> Classification:
        shape = shape_of(aspect)
        pool = [s for s in specs if shape is None or s.shape in (shape, Shape.ANY)] or specs
        scores = label_scores(transcripts, pool)
        best = max(scores, key=scores.get, default=OTHER)
        if not scores or scores[best] == 0:
            return Classification(OTHER, 0.0, "no labels found")
        ranked = sorted(scores.values(), reverse=True)
        margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0)
        return Classification(best, round(min(1.0, 0.5 + margin), 3), f"labels={scores[best]:.2f} shape={shape}")


CLASSIFY_PROMPT = """Classify an Iraqi onboarding document photo from what image readers saw.
You get reader captions, the first transcribed lines, the detected aspect ratio, and a label-evidence
score per candidate type. Answer with one of the candidate keys or "other".
Reply with JSON only: {"doc_type": "...", "confidence": 0.0-1.0}"""


class LightningClassifier:
    """Nemotron 3.5 Lightning over captions + layout cues; falls back to rules on failure."""

    def __init__(self, client: ChatClient):
        self.client = client
        self.rules = RuleClassifier()

    async def classify(self, transcripts: list[Transcript], aspect: float | None, specs: list[DocTypeSpec]) -> Classification:
        fallback = await self.rules.classify(transcripts, aspect, specs)
        payload = {
            "candidates": {s.key: s.label for s in specs},
            "captions": [t.caption for t in transcripts if t.caption],
            "first_lines": [l.text for l in (transcripts[0].lines if transcripts else [])][:12],
            "aspect_ratio": round(aspect, 3) if aspect else None,
            "label_evidence": label_scores(transcripts, specs),
        }
        try:
            reply = await self.client.complete(
                [
                    {"role": "system", "content": CLASSIFY_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                max_tokens=200,
                purpose="classify",
            )
            obj = parse_json_object(reply)
            doc_type = str(obj.get("doc_type"))
            if doc_type not in payload["candidates"] and doc_type != OTHER:
                return fallback
            conf = float(obj.get("confidence", 0.5))
            # Layout is a hard rule: a card-shaped photo cannot be an A4 certificate.
            shape = shape_of(aspect)
            spec = next((s for s in specs if s.key == doc_type), None)
            if shape and spec and spec.shape not in (shape, Shape.ANY):
                return fallback
            return Classification(doc_type, round(max(0.0, min(conf, 1.0)), 3), "lightning")
        except Exception:
            return fallback
