"""Field confidence: features -> calibrated probability that the field value is correct.

Per field group (names, digits, dates, enums, text): logistic regression (Platt-style)
on the features, then isotonic regression on its output. Fitted by the `calibrate` job
on the calibration split and saved as a small JSON artifact the API loads.

Until a fitted artifact is configured, DEFAULT_CALIBRATOR uses hand-set weights. Its
outputs are NOT calibrated probabilities; they only rank fields sensibly.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .registry import FieldGroup

# Order matters: this is the feature vector layout stored in artifacts.
FEATURES = (
    "agreement",  # share of configured readers whose value agrees with the chosen one
    "pair_similarity",  # min normalised similarity between chosen value and other readers' values
    "self_consistency",  # agreement across a reader's samples (0.5 when not sampled)
    "readers_ok",  # share of configured readers that returned a transcript
    "illegible",  # some reader marked a character illegible
    "format_valid",  # 1 valid, 0 invalid, 0.5 no validator
    "glare",  # share of the image that is glare
    "blur",  # 1 = very blurred, 0 = sharp
    "handwritten",  # template prior
    "logprob",  # mean token logprob mapped to 0..1 (0.5 imputed when unavailable)
)


# Features that may be unavailable are imputed with an uninformative 0.5; the rest default to 0.
IMPUTED = {"self_consistency": 0.5, "format_valid": 0.5, "logprob": 0.5}


def vector(features: dict[str, float]) -> np.ndarray:
    return np.array([float(features.get(k, IMPUTED.get(k, 0.0))) for k in FEATURES], dtype=float)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


@dataclass
class GroupModel:
    weights: list[float]
    bias: float
    iso_x: list[float] = field(default_factory=list)  # isotonic knots (logistic output -> probability)
    iso_y: list[float] = field(default_factory=list)
    threshold: float = 0.9  # tau: auto-accept at or above

    def predict(self, x: np.ndarray) -> float:
        p = float(_sigmoid(np.dot(self.weights, x) + self.bias))
        if self.iso_x:
            p = float(np.interp(p, self.iso_x, self.iso_y))
        return min(max(p, 0.0), 1.0)


@dataclass
class Calibrator:
    version: str
    groups: dict[str, GroupModel]
    fitted: bool = False

    def predict(self, features: dict[str, float], group: FieldGroup) -> float:
        return self.groups[group.value].predict(vector(features))

    def threshold(self, group: FieldGroup) -> float:
        return self.groups[group.value].threshold

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "fitted": self.fitted,
                "features": list(FEATURES),
                "groups": {g: m.__dict__ for g, m in self.groups.items()},
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Calibrator":
        raw = json.loads(text)
        if tuple(raw["features"]) != FEATURES:
            raise ValueError("calibrator artifact was fitted on a different feature layout")
        return cls(raw["version"], {g: GroupModel(**m) for g, m in raw["groups"].items()}, raw.get("fitted", True))

    @classmethod
    def load(cls, path: str | Path) -> "Calibrator":
        return cls.from_json(Path(path).read_text())


def _default_group() -> GroupModel:
    w = dict(
        agreement=7.0, pair_similarity=2.0, self_consistency=1.5, readers_ok=1.0, illegible=-4.0,
        format_valid=2.0, glare=-2.0, blur=-2.0, handwritten=-0.5, logprob=0.0,
    )
    return GroupModel(weights=[w[k] for k in FEATURES], bias=-6.5, threshold=0.9)


DEFAULT_CALIBRATOR = Calibrator("heuristic-0", {g.value: _default_group() for g in FieldGroup}, fitted=False)


# ---------------------------------------------------------------- fitting (used by jobs/calibrate)

def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> tuple[np.ndarray, float]:
    """L2-regularised logistic regression by Newton's method (IRLS)."""
    n, d = X.shape
    A = np.hstack([X, np.ones((n, 1))])
    w = np.zeros(d + 1)
    reg = np.eye(d + 1) * l2
    reg[-1, -1] = 0.0  # do not regularise the bias
    for _ in range(iters):
        p = _sigmoid(A @ w)
        grad = A.T @ (p - y) + reg @ w
        H = (A * (p * (1 - p))[:, None]).T @ A + reg
        step = np.linalg.solve(H + 1e-9 * np.eye(d + 1), grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w[:-1], float(w[-1])


def fit_isotonic(x: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    """Pool-adjacent-violators; returns increasing knots for np.interp."""
    order = np.argsort(x)
    xs, ys = x[order], y[order].astype(float)
    blocks: list[list[float]] = []  # [sum_y, count, x_min, x_max]
    for xi, yi in zip(xs, ys):
        blocks.append([yi, 1.0, xi, xi])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c, lo, hi = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
            blocks[-1][3] = hi
    knots_x, knots_y = [], []
    for s, c, lo, hi in blocks:
        knots_x += [float(lo), float(hi)]
        knots_y += [s / c, s / c]
    return knots_x, knots_y


def choose_threshold(p: np.ndarray, y: np.ndarray, target_precision: float = 0.99) -> tuple[float, float]:
    """Smallest tau whose auto-accepted set has precision >= target. Returns (tau, coverage)."""
    for tau in np.unique(np.round(p, 4)):
        accepted = p >= tau
        if accepted.sum() and y[accepted].mean() >= target_precision:
            return float(tau), float(accepted.mean())
    return 1.01, 0.0  # nothing reaches the target: accept nothing automatically


def fit_calibrator(
    rows: list[tuple[FieldGroup, dict[str, float], int]], version: str, target_precision: float = 0.99, min_rows: int = 30
) -> Calibrator:
    """rows: (group, features, correct 0/1). Groups with too few rows keep the default model."""
    groups: dict[str, GroupModel] = {}
    for g in FieldGroup:
        data = [(vector(f), y) for gg, f, y in rows if gg is g]
        if len(data) < min_rows or len({y for _, y in data}) < 2:
            groups[g.value] = _default_group()
            continue
        X = np.stack([x for x, _ in data])
        y = np.array([y for _, y in data], dtype=float)
        w, b = fit_logistic(X, y)
        raw = _sigmoid(X @ w + b)
        ix, iy = fit_isotonic(raw, y)
        model = GroupModel(weights=w.tolist(), bias=b, iso_x=ix, iso_y=iy)
        p = np.array([model.predict(x) for x in X])
        model.threshold, _ = choose_threshold(p, y, target_precision)
        groups[g.value] = model
    return Calibrator(version, groups, fitted=True)


# ---------------------------------------------------------------- metrics (used by jobs/eval)

def expected_calibration_error(p: np.ndarray, y: np.ndarray, bins: int = 15) -> float:
    """ECE with equal-mass bins."""
    if len(p) == 0:
        return math.nan
    order = np.argsort(p)
    ece = 0.0
    for chunk in np.array_split(order, min(bins, len(p))):
        if len(chunk):
            ece += len(chunk) / len(p) * abs(p[chunk].mean() - y[chunk].mean())
    return float(ece)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(p) else math.nan


def reliability_bins(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[tuple[float, float, int]]:
    """(mean predicted, observed accuracy, count) per equal-mass bin."""
    order = np.argsort(p)
    return [
        (float(p[c].mean()), float(y[c].mean()), int(len(c)))
        for c in np.array_split(order, min(bins, max(len(p), 1)))
        if len(c)
    ]
