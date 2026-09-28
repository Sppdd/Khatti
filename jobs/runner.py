"""Run the real pipeline over a manifest split (shared by eval and calibrate)."""

from __future__ import annotations

import argparse
import asyncio
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import AsyncIterator

from khatti.calibration import Calibrator
from khatti.config import load_settings
from khatti.models import DocumentInput, SessionResult
from khatti.pipeline import Pipeline
from khatti.registry import default_registry
from khatti.services import build_pipeline
from khatti.structuring import LabelStructurer

from .common import read_jsonl
from .readers import CachingReader, SimulatedReader, capture_index


def add_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--data", type=Path, default=Path("data/synth"))
    ap.add_argument("--manifest", default="captures.jsonl")
    ap.add_argument("--split", default="test", help="tune | calibration | test | all")
    ap.add_argument("--readers", choices=["env", "simulated"], default="env",
                    help="env: KHATTI_READERS endpoints; simulated: harness testing only")
    ap.add_argument("--structurer", choices=["env", "label"], default="env")
    ap.add_argument("--calibrator", type=Path, default=None)
    ap.add_argument("--cache", type=Path, default=Path("eval/cache"))
    ap.add_argument("--limit", type=int, default=0, help="max sessions (0 = all)")
    ap.add_argument("--concurrency", type=int, default=4)


def load_rows(args) -> list[dict]:
    rows = list(read_jsonl(args.data / args.manifest))
    if args.split != "all":
        rows = [r for r in rows if r["split"] == args.split]
    return rows


def sessions_of(rows: list[dict]) -> list[list[dict]]:
    """Group captures into sessions: capture k of every slot of a session forms one session attempt."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        m = re.search(r"_c(\d+)$", Path(r["capture"]).stem)
        k = m.group(1) if m else "clean"
        groups[(r["session_id"], k)].append(r)
    return [sorted(g, key=lambda r: r["slot"]) for _, g in sorted(groups.items())]


def build(args, rows: list[dict]) -> tuple[Pipeline, bool]:
    registry = default_registry()
    calibrator = Calibrator.load(args.calibrator) if args.calibrator else None
    if args.readers == "simulated":
        index = capture_index(rows, args.data)
        readers = [
            SimulatedReader("sim-nvidia-omni", index, "digits-strong"),
            SimulatedReader("sim-arabic-vlm", index, "arabic-strong", samples=3),
        ]
        pipeline = Pipeline(readers, LabelStructurer(), registry, calibrator)
        return pipeline, True
    settings = load_settings()
    pipeline = build_pipeline(settings, registry)
    if pipeline is None:
        raise SystemExit("KHATTI_READERS is not configured (use --readers simulated to test the harness)")
    pipeline.readers = [CachingReader(r, args.cache) for r in pipeline.readers]
    if args.structurer == "label":
        pipeline.structurer = LabelStructurer()
    if calibrator:
        pipeline.calibrator = calibrator
    return pipeline, False


async def run_sessions(pipeline: Pipeline, args, rows: list[dict]) -> AsyncIterator[tuple[list[dict], SessionResult]]:
    groups = sessions_of(rows)
    if args.limit:
        groups = groups[: args.limit]
    sem = asyncio.Semaphore(args.concurrency)

    async def one(group):
        async with sem:
            docs = [DocumentInput(slot=r["slot"], image=(args.data / r["capture"]).read_bytes(),
                                  mime_type="image/jpeg" if r["capture"].endswith(".jpg") else "image/png") for r in group]
            return group, await pipeline.run(docs, today=date.fromisoformat(group[0]["as_of"]))

    for fut in asyncio.as_completed([one(g) for g in groups]):
        yield await fut
