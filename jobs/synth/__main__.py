"""Generate fictional sessions and render them.

    python -m jobs.synth --sessions 150 --out data/synth --seed 7 [--as-of 2026-09-28]

Writes data/synth/renders/*.png and data/synth/renders.jsonl (one row per document with
ground truth), split by identity 60/20/20.
"""

from __future__ import annotations

import argparse
import random
from datetime import date
from pathlib import Path

from khatti.registry import default_registry

from ..common import assign_splits, write_jsonl
from .identities import make_session, plan_variants
from .render import Renderer


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=150)
    ap.add_argument("--out", type=Path, default=Path("data/synth"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 28))
    ap.add_argument("--slots", default="national_id_front,national_id_back,commercial_registration,tax_card")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    registry = default_registry()
    slots = args.slots.split(",")
    sessions = [make_session(rng, args.as_of, i, v) for i, v in enumerate(plan_variants(rng, args.sessions))]
    splits = assign_splits([s.identity_id for s in sessions], args.seed)

    out = args.out / "renders"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    renderer = Renderer()
    try:
        for s in sessions:
            for slot in slots:
                spec = registry.get(slot)
                values = s.documents[slot]
                r = renderer.render(spec, values, s.handwritten.get(slot, []), rng)
                name = f"{s.session_id}_{slot}.png"
                (out / name).write_bytes(r.png)
                rows.append({
                    "image": f"renders/{name}",
                    "session_id": s.session_id,
                    "identity_id": s.identity_id,
                    "slot": slot,
                    "split": splits[s.identity_id],
                    "as_of": args.as_of.isoformat(),
                    "variants": s.variants,
                    "human_expected": s.human_expected,
                    "fields": {f.name: values.get(f.name) for f in spec.fields},
                    "handwritten": s.handwritten.get(slot, []),
                    "digits": s.digits,
                    "font": r.font,
                    "field_boxes": r.field_boxes,
                    "printed_lines": r.lines,
                    "condition": {"kind": "render"},
                })
    finally:
        renderer.close()
    manifest = args.out / "renders.jsonl"
    write_jsonl(manifest, rows)
    print(f"rendered {len(rows)} documents for {len(sessions)} sessions -> {manifest}")
    return manifest


if __name__ == "__main__":
    main()
