"""Eval metrics (plan I): field accuracy, CER, null-correctness, hallucination rate,
calibration, human routing, and slices."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from khatti.arabic import cer, normalize
from khatti.calibration import brier, expected_calibration_error, reliability_bins
from khatti.models import Outcome, SessionResult
from khatti.registry import FieldGroup, Registry


@dataclass
class FieldObs:
    slot: str
    field: str
    group: str
    required: bool
    handwritten: bool
    bucket: str
    digits: str
    gt: str | None  # None: unreadable in the photo, or not printed
    gt_unreadable: bool
    pred: str | None
    status: str
    confidence: float
    features: dict[str, float]

    @property
    def correct(self) -> bool:
        if self.gt is None:
            return self.pred is None
        return self.pred is not None and normalize(self.pred) == normalize(self.gt)

    @property
    def exact(self) -> bool:
        return self.pred == self.gt


@dataclass
class SessionObs:
    session_id: str
    variants: list[str]
    human_expected: bool
    outcome: str
    buckets: list[str]
    required_ok: bool  # every required field correct and readable in the photo

    @property
    def human_needed(self) -> bool:
        return self.human_expected or not self.required_ok

    @property
    def predicted_review(self) -> bool:
        return self.outcome == Outcome.HUMAN_REVIEW.value


@dataclass
class Collector:
    registry: Registry
    fields: list[FieldObs] = field(default_factory=list)
    sessions: list[SessionObs] = field(default_factory=list)

    def add(self, group: list[dict], result: SessionResult) -> None:
        by_slot = {d.slot: d for d in result.documents}
        required_ok = True
        for row in group:
            spec = self.registry.get(row["slot"])
            doc = by_slot[row["slot"]]
            for f in spec.fields:
                unreadable = f.name in row.get("gt_unreadable", [])
                gt = None if unreadable else row["fields"].get(f.name)
                r = doc.fields[f.name]
                obs = FieldObs(row["slot"], f.name, f.group.value, f.required, f.name in row.get("handwritten", []),
                               row.get("bucket", "good"), row.get("digits", "western"), gt, unreadable,
                               r.value, r.status.value, r.confidence, r.features)
                self.fields.append(obs)
                if f.required and (unreadable or not obs.correct):
                    required_ok = False
        self.sessions.append(SessionObs(group[0]["session_id"], group[0]["variants"], group[0]["human_expected"],
                                        result.decision.outcome.value, [r.get("bucket", "good") for r in group], required_ok))

    # ------------------------------------------------------------ summaries

    def field_summary(self, obs: list[FieldObs]) -> dict:
        if not obs:
            return {"n": 0}
        readable = [o for o in obs if o.gt is not None]
        unreadable = [o for o in obs if o.gt_unreadable]
        predicted = [o for o in obs if o.pred is not None]
        text = [o for o in readable if o.pred is not None and o.group in (FieldGroup.NAMES.value, FieldGroup.TEXT.value)]
        digits = [o for o in readable if o.pred is not None and o.group == FieldGroup.DIGITS.value]
        nulls = [o for o in obs if o.pred is None]
        return {
            "n": len(obs),
            "exact_match": _mean([o.exact for o in readable]),
            "normalized_match": _mean([o.correct for o in readable]),
            "coverage": _mean([o.pred is not None for o in readable]),
            "cer_arabic_text": _mean([cer(o.gt, o.pred) for o in text]),
            "cer_digits": _mean([cer(o.gt, o.pred) for o in digits]),
            "null_precision": _mean([o.gt_unreadable for o in nulls]),
            "null_recall": _mean([o.pred is None for o in unreadable]),
            "hallucination_rate": _mean([o.pred is not None for o in unreadable]),
            "n_unreadable": len(unreadable),
            "accuracy_of_ok_fields": _mean([o.correct for o in predicted if o.status == "ok"]),
        }

    def calibration(self, obs: list[FieldObs]) -> dict:
        pts = [(o.confidence, float(o.correct)) for o in obs if o.pred is not None]
        if not pts:
            return {"n": 0}
        p, y = np.array([a for a, _ in pts]), np.array([b for _, b in pts])
        return {"n": len(pts), "ece": expected_calibration_error(p, y), "brier": brier(p, y),
                "reliability": reliability_bins(p, y), "selective_risk": selective_risk(p, y)}

    def routing(self) -> dict:
        s = self.sessions
        if not s:
            return {"n": 0}
        tp = sum(x.predicted_review and x.human_needed for x in s)
        fp = sum(x.predicted_review and not x.human_needed for x in s)
        fn = sum(not x.predicted_review and x.human_needed for x in s)
        auto = [x for x in s if not x.predicted_review]
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        return {
            "n": len(s),
            "human_review_rate": _mean([x.predicted_review for x in s]),
            "precision": prec,
            "recall": rec,
            "f1": 2 * prec * rec / (prec + rec) if prec == prec and rec == rec and prec + rec else float("nan"),
            "auto_pass_rate": len(auto) / len(s),
            "auto_pass_error_rate": _mean([x.human_needed for x in auto]),
            "variant_recall": {v: _mean([x.predicted_review for x in s if v in x.variants])
                               for v in sorted({v for x in s for v in x.variants})},
        }

    def report(self) -> dict:
        slices = defaultdict(dict)
        for b in ("good", "medium", "worst"):
            slices["bucket"][b] = self.field_summary([o for o in self.fields if o.bucket == b])
        slices["writing"]["handwritten"] = self.field_summary([o for o in self.fields if o.handwritten])
        slices["writing"]["printed"] = self.field_summary([o for o in self.fields if not o.handwritten])
        for d in ("western", "arabic_indic"):
            slices["digits"][d] = self.field_summary([o for o in self.fields if o.digits == d and o.group == "digits"])
        for g in FieldGroup:
            slices["group"][g.value] = self.field_summary([o for o in self.fields if o.group == g.value])
        cal_groups = {g.value: self.calibration([o for o in self.fields if o.group == g.value]) for g in FieldGroup}
        return {
            "fields": self.field_summary(self.fields),
            "calibration": self.calibration(self.fields),
            "calibration_by_group": cal_groups,
            "calibration_worst_bucket": self.calibration([o for o in self.fields if o.bucket == "worst"]),
            "routing": self.routing(),
            "slices": slices,
        }


def selective_risk(p: np.ndarray, y: np.ndarray, points: int = 20) -> list[tuple[float, float, float]]:
    """(threshold, coverage, error among accepted) as the confidence threshold sweeps."""
    out = []
    for tau in np.linspace(0, 1, points + 1):
        acc = p >= tau
        if acc.any():
            out.append((round(float(tau), 3), float(acc.mean()), float(1 - y[acc].mean())))
    return out


def _mean(xs) -> float:
    xs = list(xs)
    return float(np.mean(xs)) if xs else float("nan")
