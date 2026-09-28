"""Capture quality metrics (deterministic OpenCV; LLMs are poor at judging blur).

The same metrics run on-device (mobile frame processor) and on the server after upload.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .models import QualityReport

MIN_SHORT_SIDE = 600  # card short side, px
BLUR_VAR_OK = 150.0  # variance of Laplacian at 1000 px width
GLARE_FRACTION = 0.015
CLIP_FRACTION = 0.25
SKIN_EDGE_FRACTION = 0.04

MESSAGES = {
    "unreadable_image": ("تعذر فتح الصورة، أعد التصوير", "The image could not be opened, retake"),
    "low_resolution": ("الصورة صغيرة جداً، قرّب الكاميرا", "The image is too small, move closer"),
    "blur": ("الصورة غير واضحة، ثبّت الهاتف وأعد التصوير", "The photo is blurry, hold steady and retake"),
    "too_dark": ("الإضاءة ضعيفة، صوّر في مكان أكثر إضاءة", "Too dark, find more light"),
    "overexposed": ("الصورة ساطعة جداً، ابتعد عن مصدر الضوء", "Overexposed, move away from the light"),
    "glare": ("انعكاس ضوء فوق المستند، غيّر الزاوية", "Glare on the document, tilt it slightly"),
    "corners_not_found": ("لم تظهر حواف المستند، ضع المستند كاملاً داخل الإطار", "Document edges not found, fit the whole document in the frame"),
    "corner_cut": ("الزاوية مقطوعة، أعد التصوير", "The corner is cut off, retake"),
    "possible_occlusion": ("يبدو أن إصبعك يغطي جزءاً من المستند", "A finger may be covering part of the document"),
}


@dataclass(frozen=True)
class Quad:
    points: np.ndarray  # 4x2 float32, ordered tl, tr, br, bl
    area_fraction: float

    @property
    def aspect(self) -> float:
        tl, tr, br, bl = self.points
        w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
        h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
        return float(max(w, h) / max(min(w, h), 1e-6))

    @property
    def short_side(self) -> float:
        tl, tr, br, bl = self.points
        return float(min(np.linalg.norm(tr - tl), np.linalg.norm(bl - tl)))


def decode(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR) if arr.size else None


def order_points(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def find_document(img: np.ndarray) -> Quad | None:
    """Largest convex 4-corner contour covering at least 15% of the image."""
    h, w = img.shape[:2]
    scale = 800 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
    s = scale if scale < 1 else 1.0
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    # A black frame gives a document that runs off the photo a closing edge (-> corner_cut).
    pad = 4
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    framed = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=int(np.median(border)))
    edges = cv2.dilate(cv2.Canny(framed, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [np.clip(c - pad, 0, [small.shape[1] - 1, small.shape[0] - 1]) for c in contours]
    total = small.shape[0] * small.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        # The hull ignores text touching the edge and rounded card corners.
        hull = cv2.convexHull(c)
        area = cv2.contourArea(hull)
        if area < 0.15 * total:
            break
        if area > 0.97 * total:
            continue  # the photo frame itself, not a document
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            return Quad(order_points(approx) / s, area / total)
        rect = cv2.minAreaRect(hull)
        rect_area = rect[1][0] * rect[1][1]
        if rect_area and area / rect_area >= 0.9:
            return Quad(order_points(cv2.boxPoints(rect)) / s, area / total)
    return None


def assess(data: bytes) -> tuple[QualityReport, np.ndarray | None, Quad | None]:
    img = decode(data)
    if img is None:
        report = QualityReport(width=0, height=0, metrics={}, issues=["unreadable_image"])
        _messages(report)
        return report, None, None
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    norm = cv2.resize(gray, (1000, max(1, int(1000 * h / w))))
    lap_var = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    dark = float((gray <= 8).mean())
    bright = float((gray >= 250).mean())
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    glare_mask = ((hsv[..., 2] >= 245) & (hsv[..., 1] <= 30)).astype(np.uint8)
    # Only blobs count as glare (a white paper background is not glare if it is the page itself).
    n, _, stats, _ = cv2.connectedComponentsWithStats(glare_mask)
    blob_area = sum(int(a) for a in stats[1:, cv2.CC_STAT_AREA] if a >= 0.002 * h * w)
    glare_fraction = blob_area / (h * w)

    quad = find_document(img)
    metrics = {
        "laplacian_var": round(lap_var, 2),
        "blur_score": round(float(np.clip((BLUR_VAR_OK - lap_var) / BLUR_VAR_OK, 0, 1)), 3),
        "mean_brightness": round(float(gray.mean()), 1),
        "dark_clip": round(dark, 4),
        "bright_clip": round(bright, 4),
        "glare_fraction": round(glare_fraction, 4),
        "document_area": round(quad.area_fraction, 3) if quad else 0.0,
        "document_aspect": round(quad.aspect, 3) if quad else 0.0,
    }
    issues = []
    short = quad.short_side if quad else min(h, w)
    if short < MIN_SHORT_SIDE:
        issues.append("low_resolution")
    if lap_var < BLUR_VAR_OK / 3:
        issues.append("blur")
    if gray.mean() < 50 or dark > CLIP_FRACTION:
        issues.append("too_dark")
    elif bright > CLIP_FRACTION and glare_fraction < GLARE_FRACTION:
        issues.append("overexposed")
    if glare_fraction >= GLARE_FRACTION and quad is not None:
        issues.append("glare")
    if quad is None:
        issues.append("corners_not_found")
    else:
        margin = 0.01 * max(h, w)
        pts = quad.points
        if (pts[:, 0] <= margin).any() or (pts[:, 1] <= margin).any() or (pts[:, 0] >= w - margin).any() or (pts[:, 1] >= h - margin).any():
            issues.append("corner_cut")
        if _skin_on_edge(img, quad) > SKIN_EDGE_FRACTION:
            issues.append("possible_occlusion")
    report = QualityReport(width=w, height=h, metrics=metrics, issues=issues)
    _messages(report)
    return report, img, quad


def _skin_on_edge(img: np.ndarray, quad: Quad) -> float:
    """Share of the document's border band that looks like skin (experimental heuristic)."""
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycrcb, (0, 138, 77), (255, 173, 127))
    doc = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(doc, quad.points.astype(np.int32), 1)
    k = max(3, int(0.06 * quad.short_side))
    band = doc - cv2.erode(doc, np.ones((k, k), np.uint8))
    return float((skin[band > 0] > 0).mean()) if band.any() else 0.0


def _messages(report: QualityReport) -> None:
    report.guidance_ar = [MESSAGES[i][0] for i in report.issues]
    report.guidance_en = [MESSAGES[i][1] for i in report.issues]


FIELD_MESSAGES = {
    "glare": ("انعكاس ضوء فوق {ar}، غيّر زاوية التصوير وأعد المحاولة", "Glare over the {en}; tilt the document and retake"),
    "thumb": ("إصبعك يغطي {ar}، أعد التصوير", "Your finger covers the {en}; retake"),
    "cut_off": ("{ar} خارج الإطار، أدخل المستند كاملاً", "The {en} is cut off; fit the whole document in the frame"),
    "blur": ("{ar} غير واضح، ثبّت الهاتف وأعد التصوير", "The {en} is blurry; hold steady and retake"),
    "unreadable": ("{ar} غير مقروء، أعد التصوير", "The {en} is unreadable; retake"),
}


def _skin(img: np.ndarray) -> np.ndarray:
    return cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb), (0, 138, 77), (255, 173, 127)) > 0


def field_obstruction(img: np.ndarray | None, bbox: list[float] | None, quad: Quad | None = None) -> str:
    """Why a field could not be read, from its box on the original photo:
    cut_off | glare | thumb | blur | unreadable (unknown)."""
    if img is None or not bbox:
        return "unreadable"
    h, w = img.shape[:2]
    x0, y0, x1, y1 = bbox
    if min(x0, y0) <= 0.005 or max(x1, y1) >= 0.995:
        return "cut_off"
    px = [int(max(0, min(1, v)) * s) for v, s in zip(bbox, (w, h, w, h))]
    roi = img[px[1] : max(px[3], px[1] + 1), px[0] : max(px[2], px[0] + 1)]
    if roi.size == 0:
        return "unreadable"
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    if ((hsv[..., 2] >= 240) & (hsv[..., 1] <= 40)).mean() > 0.2:
        return "glare"
    skin_here = _skin(roi).mean()
    if skin_here > 0.4:
        # A beige card is not a thumb: compare with the document as a whole.
        doc_skin = _skin(img).mean() if quad is None else _skin_in_quad(img, quad)
        if doc_skin < 0.25:
            return "thumb"
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] >= 8 and gray.shape[1] >= 8 and cv2.Laplacian(gray, cv2.CV_64F).var() < 40:
        return "blur"
    return "unreadable"


def _skin_in_quad(img: np.ndarray, quad: Quad) -> float:
    doc = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(doc, quad.points.astype(np.int32), 1)
    return float(_skin(img)[doc > 0].mean()) if doc.any() else 0.0
