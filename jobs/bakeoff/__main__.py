"""Week-1 reader bake-off (plan D, decision gate due Oct 3).

    python -m jobs.bakeoff --data data/synth --sample 60 --nvidia nvidia-omni

Runs every configured reader on its own over a sample of captures (20 per document type by
default, mixed quality), then scores:
- CER on Arabic name lines,
- exact match on digit fields, with Arabic-Indic numerals measured separately,
- exact match on dates.

Decision rule: if the NVIDIA reader's Arabic-name CER is within 5 points of the best
non-NVIDIA reader, it becomes primary; otherwise it is primary for numerals, dates and
layout and the best other reader takes Arabic names. Writes a decision memo and the
KHATTI_PRIMARY_READERS value. Report the numbers honestly in the README either way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from khatti.arabic import cer, normalize
from khatti.config import load_settings
from khatti.llm import ChatClient
from khatti.models import ReaderStatus, Transcript
from khatti.readers import VisionChatReader
from khatti.registry import FieldGroup, default_registry

from ..common import manifest_hash, read_jsonl
from ..readers import CachingReader, SimulatedReader, capture_index

MARGIN = 0.05


def best_value(transcripts: list[Transcript], label: str, gt_value: str) -> str | None:
    """The reader's reading of a field: text after '<label>:' on the best-matching line."""
    best, best_cer = None, math.inf
    for t in transcripts[:1]:  # first sample only: the bake-off scores single reads
        for line in t.lines:
            head, sep, value = line.text.partition(":")
            candidate = value.strip() if sep and normalize(head) == normalize(label) else line.text.strip()
            c = cer(gt_value, candidate)
            if c < best_cer:
                best, best_cer = candidate, c
    return best


def score(rows: list[dict], outputs: dict[str, list[Transcript]], registry) -> dict:
    names, digits, indic, dates = [], [], [], []
    by_bucket = defaultdict(list)
    failures = 0
    for r in rows:
        ts = outputs[r["capture"]]
        if not ts or ts[0].status is not ReaderStatus.OK:
            failures += 1
            continue
        spec = registry.get(r["slot"])
        for f in spec.fields:
            gt = r["fields"].get(f.name)
            if not gt or f.name in r.get("gt_unreadable", []):
                continue
            pred = best_value(ts, f.label_ar, gt) or ""
            if f.group is FieldGroup.NAMES:
                c = min(cer(gt, pred), 1.0)
                names.append(c)
                by_bucket[r.get("bucket", "good")].append(c)
            elif f.group is FieldGroup.DIGITS:
                ok = normalize(pred).replace(" ", "") == normalize(gt).replace(" ", "")
                (indic if r.get("digits") == "arabic_indic" else digits).append(ok)
            elif f.group is FieldGroup.DATES:
                dates.append(normalize(pred) == normalize(gt))
    m = lambda xs: float(np.mean(xs)) if xs else float("nan")  # noqa: E731
    return {
        "arabic_name_cer": m(names), "digit_exact": m(digits), "arabic_indic_digit_exact": m(indic),
        "date_exact": m(dates), "name_cer_by_bucket": {b: m(v) for b, v in by_bucket.items()},
        "n_images": len(rows), "failed_images": failures,
    }


def decide(scores: dict[str, dict], nvidia: str) -> tuple[dict[str, str], str]:
    others = {k: v for k, v in scores.items() if k != nvidia and not math.isnan(v["arabic_name_cer"])}
    if nvidia not in scores or not others:
        return {}, "not enough readers scored to decide"
    best_other = min(others, key=lambda k: others[k]["arabic_name_cer"])
    gap = scores[nvidia]["arabic_name_cer"] - others[best_other]["arabic_name_cer"]
    if gap <= MARGIN:
        primary = {g.value: nvidia for g in FieldGroup}
        why = f"{nvidia} Arabic-name CER is within {MARGIN:.0%} of {best_other} (gap {gap:+.3f}): {nvidia} is primary."
    else:
        primary = {"digits": nvidia, "dates": nvidia, "enums": nvidia, "names": best_other, "text": best_other}
        why = (f"{nvidia} Arabic-name CER is {gap:.3f} worse than {best_other}: {nvidia} reads numerals, dates and "
               f"layout; {best_other} reads Arabic names and text.")
    return primary, why


def sample_rows(rows: list[dict], per_type: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_slot = defaultdict(list)
    for r in rows:
        if r["split"] == "tune":  # never the test split
            by_slot[r["slot"]].append(r)
    out = []
    for slot, rs in sorted(by_slot.items()):
        rng.shuffle(rs)
        out += rs[:per_type]
    return out


async def run(args) -> Path:
    registry = default_registry()
    rows = sample_rows(list(read_jsonl(args.data / args.manifest)), args.per_type, args.seed)
    if args.readers == "simulated":
        index = capture_index(rows, args.data)
        readers = [SimulatedReader("sim-nvidia-omni", index, "digits-strong"), SimulatedReader("sim-arabic-vlm", index, "arabic-strong")]
        nvidia, simulated = "sim-nvidia-omni", True
    else:
        settings = load_settings()
        if not settings.readers:
            raise SystemExit("KHATTI_READERS is not configured")
        readers = [CachingReader(VisionChatReader(ChatClient(r, settings.timeout_s)), args.cache) for r in settings.readers]
        nvidia, simulated = args.nvidia, False

    sem = asyncio.Semaphore(args.concurrency)
    scores = {}
    for reader in readers:
        async def read(r):
            async with sem:
                data = (args.data / r["capture"]).read_bytes()
                return r["capture"], await reader.transcribe([(data, "image/jpeg")])

        outputs = dict(await asyncio.gather(*(read(r) for r in rows)))
        scores[reader.name] = score(rows, outputs, registry)

    primary, why = decide(scores, nvidia)
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"simulated": simulated, "manifest_sha256": manifest_hash(args.data / args.manifest),
              "images": len(rows), "scores": scores, "decision": why, "KHATTI_PRIMARY_READERS": primary}
    (args.out / "bakeoff.json").write_text(json.dumps(report, indent=2))
    f = lambda v: "–" if isinstance(v, float) and math.isnan(v) else f"{v:.3f}"  # noqa: E731
    md = ["# Reader bake-off — decision memo", ""]
    if simulated:
        md += ["> **SIMULATED READERS.** Harness test only; not a decision.", ""]
    md += [f"{len(rows)} tune-split captures, mixed quality.", "",
           "| reader | Arabic-name CER ↓ | digits exact ↑ | Arabic-Indic digits exact ↑ | dates exact ↑ | failed images |",
           "|---|---|---|---|---|---|"]
    for name, s in scores.items():
        md.append(f"| {name} | {f(s['arabic_name_cer'])} | {f(s['digit_exact'])} | {f(s['arabic_indic_digit_exact'])} | {f(s['date_exact'])} | {s['failed_images']} |")
    md += ["", f"**Decision:** {why}", "", f"`KHATTI_PRIMARY_READERS={json.dumps(primary)}`", ""]
    (args.out / "bakeoff.md").write_text("\n".join(md))
    print("\n".join(md))
    return args.out / "bakeoff.md"


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/synth"))
    ap.add_argument("--manifest", default="captures.jsonl")
    ap.add_argument("--per-type", type=int, default=15, help="captures per document type (4 types x 15 = 60)")
    ap.add_argument("--readers", choices=["env", "simulated"], default="env")
    ap.add_argument("--nvidia", default="nvidia-omni", help="name of the NVIDIA reader in KHATTI_READERS")
    ap.add_argument("--cache", type=Path, default=Path("eval/cache"))
    ap.add_argument("--out", type=Path, default=Path("eval/bakeoff"))
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=4)
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
