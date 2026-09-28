import cv2
import numpy as np

from khatti.calibration import (
    DEFAULT_CALIBRATOR,
    Calibrator,
    brier,
    choose_threshold,
    expected_calibration_error,
    fit_calibrator,
    fit_isotonic,
)
from khatti.classify import Shape, shape_of
from khatti.preprocess import prepare
from khatti.quality import assess, decode
from khatti.registry import FieldGroup

from .conftest import card_image


def _edit(png: bytes, fn) -> bytes:
    img = decode(png)
    return cv2.imencode(".png", fn(img))[1].tobytes()


def issues(png: bytes) -> list[str]:
    return assess(png)[0].issues


def test_clean_card_has_no_issues_and_card_aspect():
    report, _, quad = assess(card_image(1))
    assert report.issues == []
    assert shape_of(quad.aspect) is Shape.ID1_CARD


def test_glare_detected():
    def glare(img):
        cv2.circle(img, (600, 350), 90, (255, 255, 255), -1)
        return img

    report = assess(_edit(card_image(1), glare))[0]
    assert "glare" in report.issues
    assert "انعكاس" in report.guidance_ar[report.issues.index("glare")]


def test_corner_cut_detected():
    assert "corner_cut" in issues(_edit(card_image(1), lambda img: img[150:, 150:]))


def test_dark_and_small_detected():
    assert "too_dark" in issues(_edit(card_image(1), lambda img: (img * 0.15).astype(np.uint8)))
    assert "low_resolution" in issues(_edit(card_image(1), lambda img: cv2.resize(img, (400, 300))))


def test_undecodable_image():
    assert issues(b"not an image") == ["unreadable_image"]


def test_prepare_sends_original_and_enhanced():
    png = card_image(1)
    report, img, quad = assess(png)
    p = prepare(png, "image/png", img, quad)
    assert len(p.images) == 2 and len(p.hashes) == 2
    assert abs(p.warped_aspect - 85.6 / 53.98) < 0.05


# ---------------------------------------------------------------- calibration

def test_default_calibrator_ranks_agreement_above_disagreement():
    agree = dict(agreement=1, pair_similarity=1, self_consistency=1, readers_ok=1, format_valid=1)
    half = dict(agreement=0.5, pair_similarity=0, self_consistency=1, readers_ok=0.5, format_valid=1)
    hi, lo = (DEFAULT_CALIBRATOR.predict(f, FieldGroup.DIGITS) for f in (agree, half))
    assert hi > 0.99 and lo < 0.9


def test_isotonic_is_monotone():
    x = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    y = np.array([0, 1, 0, 1, 1])
    kx, ky = fit_isotonic(x, y)
    assert all(a <= b for a, b in zip(ky, ky[1:]))


def test_fit_calibrator_learns_and_roundtrips():
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(400):
        agreement = float(rng.random())
        correct = int(rng.random() < agreement)  # true P(correct) = agreement
        rows.append((FieldGroup.NAMES, {"agreement": agreement, "pair_similarity": agreement}, correct))
    cal = fit_calibrator(rows, "test-1")
    p_hi = cal.predict({"agreement": 0.95, "pair_similarity": 0.95}, FieldGroup.NAMES)
    p_lo = cal.predict({"agreement": 0.1, "pair_similarity": 0.1}, FieldGroup.NAMES)
    assert p_hi > 0.75 and p_lo < 0.3
    again = Calibrator.from_json(cal.to_json())
    assert again.predict({"agreement": 0.95, "pair_similarity": 0.95}, FieldGroup.NAMES) == p_hi
    # groups without data keep the default model
    assert again.groups["dates"].weights == DEFAULT_CALIBRATOR.groups["dates"].weights


def test_threshold_meets_precision_target():
    p = np.linspace(0, 1, 101)
    y = (p > 0.3).astype(float)
    tau, coverage = choose_threshold(p, y, 0.99)
    assert y[p >= tau].mean() >= 0.99 and coverage > 0.6


def test_calibration_metrics():
    p = np.array([0.9, 0.9, 0.1, 0.1])
    y = np.array([1, 1, 0, 0])
    assert expected_calibration_error(p, y, bins=2) < 0.11
    assert brier(p, y) < 0.02
