"""Preprocessing: perspective warp to the detected document, CLAHE contrast, glare mask.

Readers get the original photo plus the enhanced copy. Every derived image is hashed
into the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .llm import sha256
from .quality import Quad


@dataclass
class Prepared:
    images: list[tuple[bytes, str]]  # (data, mime) sent to readers: original, enhanced
    hashes: list[str]
    warped_aspect: float | None


def warp(img: np.ndarray, quad: Quad) -> np.ndarray:
    tl, tr, br, bl = quad.points
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(quad.points, dst), (w, h))


def enhance(img: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def prepare(original: bytes, mime: str, img: np.ndarray | None, quad: Quad | None) -> Prepared:
    images = [(original, mime)]
    hashes = [sha256(original)]
    aspect = None
    if img is not None:
        doc = warp(img, quad) if quad is not None else img
        aspect = quad.aspect if quad is not None else None
        ok, buf = cv2.imencode(".jpg", enhance(doc), [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            data = buf.tobytes()
            images.append((data, "image/jpeg"))
            hashes.append(sha256(data))
    return Prepared(images=images, hashes=hashes, warped_aspect=aspect)
