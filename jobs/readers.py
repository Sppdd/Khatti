"""Reader wrappers for jobs.

- CachingReader: stores transcripts per (reader, image sha256) so structuring, calibration
  and evals can be re-run without re-reading images (the plan's token budget relies on it).
- SimulatedReader: a noisy stand-in built from ground truth. It exists ONLY to test the
  harness end to end without model endpoints. Its numbers mean nothing about real models,
  and every report produced with it is labelled SIMULATED.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from khatti.models import Line, ReaderStatus, Transcript

CONFUSABLE = ["بتثنيئ", "جحخ", "دذ", "رز", "سش", "صض", "طظ", "عغ", "فق", "هة", "اأإآ", "وؤ"]
_CONF = {ch: group for group in CONFUSABLE for ch in group}


class CachingReader:
    def __init__(self, inner, cache_dir: Path):
        self.inner = inner
        self.name = inner.name
        self.samples = getattr(inner, "samples", 1)
        self.dir = Path(cache_dir) / self.name
        self.dir.mkdir(parents=True, exist_ok=True)

    async def transcribe(self, images: list[tuple[bytes, str]]) -> list[Transcript]:
        key = hashlib.sha256(images[0][0]).hexdigest()
        path = self.dir / f"{key}.json"
        if path.exists():
            return [Transcript.model_validate(t) for t in json.loads(path.read_text())]
        out = await self.inner.transcribe(images)
        if all(t.status is not ReaderStatus.UNAVAILABLE for t in out):  # never cache an outage
            path.write_text(json.dumps([t.model_dump(mode="json") for t in out], ensure_ascii=False))
        return out


PROFILES = {
    # char error probability for Arabic letters / digits, by quality bucket
    "arabic-strong": {"ar": {"good": 0.005, "medium": 0.02, "worst": 0.06}, "digit": {"good": 0.003, "medium": 0.015, "worst": 0.05}},
    "digits-strong": {"ar": {"good": 0.04, "medium": 0.09, "worst": 0.18}, "digit": {"good": 0.001, "medium": 0.005, "worst": 0.02}},
}


class SimulatedReader:
    """SIMULATED reader: ground-truth lines with profile-driven character noise."""

    simulated = True

    def __init__(self, name: str, index: dict[str, dict], profile: str, samples: int = 1, seed: int = 0, handwriting_penalty: float = 2.5):
        self.name = name
        self.index = index  # capture sha256 -> manifest row
        self.profile = PROFILES[profile]
        self.samples = samples
        self.seed = seed
        self.hand = handwriting_penalty

    def _noisy(self, text: str, p_ar: float, p_dig: float, rng: random.Random) -> str:
        out = []
        for ch in text:
            if ch.isdigit() and rng.random() < p_dig:
                out.append(rng.choice("0123456789" if ch in "0123456789" else "٠١٢٣٤٥٦٧٨٩"))
            elif ch in _CONF and rng.random() < p_ar:
                out.append(rng.choice(_CONF[ch]))
            else:
                out.append(ch)
        return "".join(out)

    async def transcribe(self, images: list[tuple[bytes, str]]) -> list[Transcript]:
        row = self.index.get(hashlib.sha256(images[0][0]).hexdigest())
        if row is None:
            return [Transcript(reader=self.name, status=ReaderStatus.ERROR, error="unknown image")]
        bucket = row.get("bucket", "good")
        out = []
        for s in range(self.samples):
            rng = random.Random(f"{self.seed}:{self.name}:{row['capture']}:{s}")
            lines = []
            labels = {v: k for k, v in row["fields"].items() if v}
            for i, text in enumerate(row["printed_lines"], 1):
                label, sep, value = text.partition(": ")
                field = labels.get(value) if sep else None
                if field in row.get("gt_unreadable", []):
                    k = max(1, len(value) // 3)
                    value = value[:k] + "?" * max(1, len(value) // 4) + value[k + len(value) // 4:]
                elif field:
                    mult = self.hand if field in row.get("handwritten", []) else 1.0
                    value = self._noisy(value, self.profile["ar"][bucket] * mult, self.profile["digit"][bucket], rng)
                if rng.random() < {"good": 0.0, "medium": 0.01, "worst": 0.04}[bucket]:
                    continue  # dropped line
                lines.append(Line(line_id=f"L{i}", text=f"{label}{sep}{value}" if sep else label))
            out.append(Transcript(reader=self.name, sample=s, caption=row["slot"].replace("_", " "), lines=lines))
        return out


def capture_index(rows: list[dict], root: Path) -> dict[str, dict]:
    return {hashlib.sha256((root / r["capture"]).read_bytes()).hexdigest(): r for r in rows}
