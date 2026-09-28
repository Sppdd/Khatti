"""Shared helpers for jobs: manifests, splits, hashing."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Iterable, Iterator

SPLITS = (("tune", 0.6), ("calibration", 0.2), ("test", 0.2))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assign_splits(identity_ids: list[str], seed: int) -> dict[str, str]:
    """Split by synthetic identity (never by photo), 60/20/20."""
    ids = sorted(set(identity_ids))
    random.Random(seed).shuffle(ids)
    out, start = {}, 0
    for i, (name, frac) in enumerate(SPLITS):
        end = len(ids) if i == len(SPLITS) - 1 else start + round(frac * len(ids))
        for x in ids[start:end]:
            out[x] = name
        start = end
    return out


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def manifest_hash(path: Path) -> str:
    """Hash of a manifest's content (order-independent): logged on day one to freeze the test set."""
    rows = sorted(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in read_jsonl(path))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def maybe_mlflow():
    """mlflow module when MLFLOW_TRACKING_URI is set and mlflow is installed, else None."""
    import os

    if not os.getenv("MLFLOW_TRACKING_URI"):
        return None
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow
