"""Augment rendered documents into phone-photo-like captures (plan H, Serverless Job).

    python -m jobs.augment --src data/synth --per-render 5 --seed 11

Conditions: background placement, perspective (0-45 deg), dim/backlit lighting, shadow,
glare, motion blur, defocus, thumb occlusion, cut-off corner, crumple, JPEG. Every image
is tagged with its conditions and a quality bucket (good / medium / worst). Fields covered
by glare or a thumb, or pushed out of frame, become unreadable in the ground truth: the
system must return null for them (anything else counts as a hallucination).
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

from ..common import read_jsonl, write_jsonl

OCCLUDED = 0.25  # share of a field's box that must be covered to make it unreadable


def _background(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    base = rng.integers(20, 150, size=3)
    grad = np.linspace(0.7, 1.2, w)[None, :, None] * np.linspace(0.8, 1.1, h)[:, None, None]
    noise = rng.normal(0, 12, size=(h, w, 1))
    wood = 10 * np.sin(np.linspace(0, rng.uniform(20, 60), w))[None, :, None]
    return np.clip(base[None, None, :] * grad + noise + wood, 0, 255).astype(np.uint8)


def _glare(h: int, w: int, center: tuple[float, float], radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    d = ((xx - center[0]) ** 2 + ((yy - center[1]) * 1.6) ** 2) / (radius**2)
    return np.clip(1.4 * np.exp(-d), 0, 1)


def augment_one(doc: np.ndarray, boxes: dict[str, list[float]], rng: np.random.Generator, pyrng: random.Random):
    cond: dict = {}
    # --- placement + perspective (canvas long side capped for speed)
    if max(doc.shape[:2]) > 1300:
        f = 1300 / max(doc.shape[:2])
        doc = cv2.resize(doc, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    dh, dw = doc.shape[:2]
    landscape = dw >= dh
    W, H = (2000, 1500) if landscape else (1500, 2000)
    scale = pyrng.uniform(0.5, 0.85)
    tw = scale * W if landscape else scale * H * dw / dh
    th = tw * dh / dw
    if th > 0.9 * H:
        tw, th = tw * 0.9 * H / th, 0.9 * H
    angle = pyrng.choice([0, 0, 0, 10, 20, 30, 45])
    cond["angle"] = angle
    cx, cy = W / 2 + pyrng.uniform(-0.06, 0.06) * W, H / 2 + pyrng.uniform(-0.06, 0.06) * H
    j = np.sin(np.radians(angle)) * 0.22
    dst = np.array([
        [cx - tw / 2 + pyrng.uniform(0, j) * tw, cy - th / 2 + pyrng.uniform(0, j) * th],
        [cx + tw / 2 - pyrng.uniform(0, j) * tw, cy - th / 2 + pyrng.uniform(-j, j) * th * 0.3],
        [cx + tw / 2 - pyrng.uniform(0, j) * tw * 0.3, cy + th / 2],
        [cx - tw / 2, cy + th / 2 - pyrng.uniform(0, j) * th * 0.3],
    ], dtype=np.float32)
    if pyrng.random() < 0.06:
        shift = np.array([pyrng.uniform(0.1, 0.22) * tw, pyrng.uniform(0.1, 0.22) * th], dtype=np.float32)
        dst -= dst[0] + shift  # push the top-left corner out of frame
        cond["corner_cut"] = True
    src = np.array([[0, 0], [dw, 0], [dw, dh], [0, dh]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    canvas = _background(H, W, rng)
    warped = cv2.warpPerspective(doc, M, (W, H))
    mask = cv2.warpPerspective(np.full((dh, dw), 255, np.uint8), M, (W, H))
    img = np.where(mask[..., None] > 0, warped, canvas).astype(np.float32)

    # field boxes in canvas coordinates: (visible mask, full projected area)
    field_masks = {}
    for name, (x0, y0, x1, y1) in boxes.items():
        pts = np.array([[x0 * dw, y0 * dh], [x1 * dw, y0 * dh], [x1 * dw, y1 * dh], [x0 * dw, y1 * dh]], np.float32)
        proj = cv2.perspectiveTransform(pts[None], M)[0]
        m = np.zeros((H, W), np.uint8)
        cv2.fillConvexPoly(m, np.round(proj).astype(np.int32), 1)
        field_masks[name] = (m, float(cv2.contourArea(proj)))
    occluder = np.zeros((H, W), np.float32)

    # --- lighting
    light = pyrng.choice(["normal"] * 6 + ["dim", "dim", "backlit", "shadow"])
    cond["light"] = light
    if light == "dim":
        img *= pyrng.uniform(0.3, 0.55)
    elif light == "backlit":
        g = np.linspace(pyrng.uniform(1.3, 1.6), 0.5, W)[None, :, None]
        img = img * 0.55 * g + 90 * (g > 1.2)
    elif light == "shadow":
        poly = np.array([[pyrng.uniform(0, W), 0], [W, 0], [W, H], [pyrng.uniform(0, W), H]], np.int32)
        sh = np.ones((H, W), np.float32)
        cv2.fillConvexPoly(sh, poly, 0.55)
        img *= cv2.GaussianBlur(sh, (0, 0), 25)[..., None]

    # --- glare
    if pyrng.random() < 0.1:
        doc_pts = dst.mean(axis=0) + (rng.random(2) - 0.5) * np.array([tw, th]) * 0.6
        g = _glare(H, W, tuple(doc_pts), pyrng.uniform(0.08, 0.2) * max(tw, th))
        img = img * (1 - g[..., None]) + 255 * g[..., None]
        occluder = np.maximum(occluder, (g > 0.75).astype(np.float32))
        cond["glare"] = True

    # --- thumb
    if pyrng.random() < 0.06:
        edge = dst[pyrng.randrange(4)]
        center = (edge + dst.mean(axis=0)) / 2 * 0.3 + edge * 0.7
        axes = (int(pyrng.uniform(0.07, 0.12) * max(tw, th)), int(pyrng.uniform(0.12, 0.2) * max(tw, th)))
        t = np.zeros((H, W), np.uint8)
        cv2.ellipse(t, tuple(int(v) for v in center), axes, pyrng.uniform(0, 180), 0, 360, 1, -1)
        skin = np.array([120, 150, 205], np.float32) * pyrng.uniform(0.7, 1.05)
        img = np.where(t[..., None] > 0, skin, img)
        occluder = np.maximum(occluder, t.astype(np.float32))
        cond["thumb"] = True

    img = np.clip(img, 0, 255).astype(np.uint8)
    # --- crumple
    if pyrng.random() < 0.15:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        a = pyrng.uniform(2, 6)
        img = cv2.remap(img, xx + a * np.sin(yy / pyrng.uniform(40, 90)), yy + a * np.cos(xx / pyrng.uniform(40, 90)), cv2.INTER_LINEAR)
        cond["crumple"] = True
    # --- blur
    blur = pyrng.choice(["none", "none", "none", "none", "motion", "defocus"])
    if blur == "motion":
        n = pyrng.choice([5, 9, 13, 21])
        kern = np.zeros((n, n), np.float32)
        kern[n // 2] = 1.0 / n
        kern = cv2.warpAffine(kern, cv2.getRotationMatrix2D((n / 2, n / 2), pyrng.uniform(0, 180), 1), (n, n))
        img = cv2.filter2D(img, -1, kern / max(kern.sum(), 1e-6))
        cond["motion_blur"] = n
    elif blur == "defocus":
        s = pyrng.choice([1.5, 3.0, 5.0])
        img = cv2.GaussianBlur(img, (0, 0), s)
        cond["defocus"] = s
    # --- jpeg
    q = pyrng.choice([35, 55, 75, 90])
    cond["jpeg_quality"] = q
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])

    unreadable = []
    for name, (m, total) in field_masks.items():
        visible = m.sum()
        if total <= 0 or visible / total < 0.9:  # pushed out of frame
            unreadable.append(name)
        elif (occluder * m).sum() / max(visible, 1) >= OCCLUDED:
            unreadable.append(name)
    return buf.tobytes(), cond, bucket(cond), unreadable


def bucket(cond: dict) -> str:
    severe = sum([
        bool(cond.get("glare")), bool(cond.get("thumb")), bool(cond.get("corner_cut")),
        cond.get("light") == "backlit", cond.get("motion_blur", 0) >= 13, cond.get("defocus", 0) >= 5,
    ])
    mild = sum([
        cond.get("light") in ("dim", "shadow"), 0 < cond.get("motion_blur", 0) < 13, 0 < cond.get("defocus", 0) < 5,
        cond.get("angle", 0) >= 30, bool(cond.get("crumple")), cond.get("jpeg_quality", 100) <= 35,
    ])
    if severe >= 1:
        return "worst"
    return "medium" if mild >= 1 else "good"


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("data/synth"))
    ap.add_argument("--per-render", type=int, default=5)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--include-clean", action="store_true", help="also keep the clean render as a 'good' capture")
    args = ap.parse_args(argv)

    rng, pyrng = np.random.default_rng(args.seed), random.Random(args.seed)
    out_dir = args.src / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in read_jsonl(args.src / "renders.jsonl"):
        doc = cv2.imread(str(args.src / r["image"]), cv2.IMREAD_COLOR)
        if args.include_clean:
            rows.append({**r, "capture": r["image"], "bucket": "good", "gt_unreadable": []})
        for k in range(args.per_render):
            data, cond, b, unreadable = augment_one(doc, r["field_boxes"], rng, pyrng)
            name = f"{Path(r['image']).stem}_c{k}.jpg"
            (out_dir / name).write_bytes(data)
            rows.append({**r, "capture": f"captures/{name}", "condition": cond, "bucket": b, "gt_unreadable": unreadable})
    manifest = args.src / "captures.jsonl"
    write_jsonl(manifest, rows)
    print(f"wrote {len(rows)} captures -> {manifest}")
    return manifest


if __name__ == "__main__":
    main()
