"""Export a manifest split as a NeMo Agent Toolkit eval dataset (JSONL: id, question, answer).

    python -m jobs.nat_dataset --data data/synth --split test --out eval/nat/test.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from khatti.registry import default_registry

from ..common import write_jsonl
from ..runner import load_rows, sessions_of


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/synth"))
    ap.add_argument("--manifest", default="captures.jsonl")
    ap.add_argument("--split", default="test")
    ap.add_argument("--out", type=Path, default=Path("eval/nat/test.jsonl"))
    args = ap.parse_args(argv)
    registry = default_registry()
    rows = []
    for group in sessions_of(load_rows(args)):
        truth, required, unreadable = {}, [], []
        for r in group:
            spec = registry.get(r["slot"])
            for f in spec.fields:
                key = f"{r['slot']}.{f.name}"
                gone = f.name in r.get("gt_unreadable", [])
                truth[key] = None if gone else r["fields"].get(f.name)
                if f.required:
                    required.append(key)
                if gone:
                    unreadable.append(key)
        question = {"as_of": group[0]["as_of"], "documents": [{"slot": r["slot"], "capture": r["capture"]} for r in group]}
        answer = {"fields": truth, "required": required, "unreadable": unreadable,
                  "human_expected": group[0]["human_expected"], "variants": group[0]["variants"],
                  "buckets": [r.get("bucket") for r in group]}
        stem = Path(group[0]["capture"]).stem.rsplit("_c", 1)
        rows.append({"id": f"{group[0]['session_id']}-{stem[1] if len(stem) > 1 else 'clean'}",
                     "question": json.dumps(question, ensure_ascii=False), "answer": json.dumps(answer, ensure_ascii=False)})
    write_jsonl(args.out, rows)
    print(f"wrote {len(rows)} sessions -> {args.out}")
    return args.out


if __name__ == "__main__":
    main()
