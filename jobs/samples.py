"""Build the committed demo samples (fictional, SPECIMEN-watermarked) for judges and the video.

    python -m jobs.samples --out samples

Three sessions, each showing one behaviour:
- clean/                      everything agrees                      -> auto_pass (with working readers)
- missing_grandfather/        license owner name omits the grandfather -> human_review, NAME_PARTIAL_MATCH
- glare_on_id/                glare over the ID number               -> ID number blank and flagged + retake request
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from khatti.registry import default_registry

from .synth.identities import make_session
from .synth.render import Renderer

AS_OF = date(2026, 9, 28)
SLOTS = ["national_id_front", "national_id_back", "commercial_registration", "tax_card"]
CASES = {
    "clean": (["clean"], None),
    "missing_grandfather": (["license_missing_grandfather"], None),
    "glare_on_id": (["clean"], ("national_id_front", "id_number")),
}


def photo(png: bytes, seed: int, glare_box: list[float] | None = None) -> bytes:
    """Mild phone-photo look: dark background, slight perspective, optional glare over a box."""
    doc = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    h, w = doc.shape[:2]
    if glare_box:
        x0, y0, x1, y1 = glare_box
        cx, cy = int((x0 + x1) / 2 * w), int((y0 + y1) / 2 * h)
        yy, xx = np.mgrid[0:h, 0:w]
        g = np.clip(1.6 * np.exp(-(((xx - cx) / (0.55 * (x1 - x0) * w)) ** 2 + ((yy - cy) / (1.6 * (y1 - y0) * h)) ** 2)), 0, 1)
        doc = (doc * (1 - g[..., None]) + 255 * g[..., None]).astype(np.uint8)
    rng = np.random.default_rng(seed)
    W, H = int(w * 1.35), int(h * 1.35)
    canvas = np.clip(rng.normal(55, 8, (H, W, 3)), 0, 255).astype(np.uint8)
    ox, oy = (W - w) / 2, (H - h) / 2
    j = 0.03 * w
    dst = np.float32([[ox + j, oy], [ox + w - j * 0.3, oy + j * 0.5], [ox + w, oy + h], [ox, oy + h - j * 0.4]])
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), dst)
    warped = cv2.warpPerspective(doc, M, (W, H))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    out = np.where(mask[..., None] > 0, warped, canvas)
    return cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("samples"))
    args = ap.parse_args(argv)
    registry = default_registry()
    renderer = Renderer(scale=1.25)
    try:
        for i, (case, (variants, glare)) in enumerate(CASES.items()):
            rng = random.Random(100 + i)
            s = make_session(rng, AS_OF, 900 + i, variants)
            folder = args.out / case
            folder.mkdir(parents=True, exist_ok=True)
            truth = {"variants": variants, "as_of": AS_OF.isoformat(), "documents": {}}
            for k, slot in enumerate(SLOTS):
                r = renderer.render(registry.get(slot), s.documents[slot], s.handwritten.get(slot, []), rng)
                box = r.field_boxes.get(glare[1]) if glare and glare[0] == slot else None
                (folder / f"{slot}.jpg").write_bytes(photo(r.png, 7 * i + k, box))
                truth["documents"][slot] = {"fields": s.documents[slot], "glare_over": glare[1] if box else None}
            (folder / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2))
    finally:
        renderer.close()
    print(f"wrote samples to {args.out}")
    return args.out


if __name__ == "__main__":
    main()
