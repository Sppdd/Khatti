"""Merge verified values from all readers/samples into one field result with features.

Reader disagreement is the main confidence signal. A reader that is unavailable or
failed counts as a missing vote: one surviving reader is never consensus.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .arabic import normalize, similarity
from .calibration import Calibrator
from .crossdoc import RuleSet
from .models import FieldResult, FieldStatus, QualityReport, ReaderStatus, VerifiedValue
from .registry import FieldSpec
from .validation import check_format

AGREE = 0.9  # normalised similarity at which two readings count as the same value


@dataclass(frozen=True)
class Reading:
    reader: str
    sample: int
    value: VerifiedValue

    @property
    def key(self) -> str:
        return f"{self.reader}#{self.sample}"


def _same(a: str, b: str) -> bool:
    return similarity(a, b) >= AGREE


def merge_field(
    field: FieldSpec,
    doc_type: str,
    readings: list[Reading],
    reader_status: dict[str, ReaderStatus],
    ruleset: RuleSet | None,
    calibrator: Calibrator,
    quality: QualityReport | None = None,
    primary_reader: str | None = None,
) -> FieldResult:
    configured = list(reader_status)
    ok_readers = [r for r, s in reader_status.items() if s is ReaderStatus.OK]

    # Per-reader value: mode across that reader's samples, plus its self-consistency.
    per_reader: dict[str, str | None] = {}
    consistency: list[float] = []
    for r in ok_readers:
        samples = [x.value.value for x in readings if x.reader == r]
        if not samples:
            continue
        keys = Counter(normalize(v) if v else None for v in samples)
        top_key, top_n = keys.most_common(1)[0]
        per_reader[r] = next((v for v in samples if (normalize(v) if v else None) == top_key), None)
        if len(samples) > 1:
            consistency.append(top_n / len(samples))

    illegible = [x for x in readings if x.value.reason == "illegible"]
    raw_partial = illegible[0].value.raw_partial if illegible else None
    candidates = {x.key: x.value.value for x in readings}
    bbox = next((x.value.bbox for x in readings if x.value.bbox), None)
    line_confs = [x.value.line_conf for x in readings if x.value.line_conf is not None]
    sources = {x.key: x.value.source_line_ids for x in readings if x.value.source_line_ids}
    values = {r: v for r, v in per_reader.items() if v}

    def result(value, status, reason=None, feats=None, conf=0.0) -> FieldResult:
        return FieldResult(
            name=field.name, value=value, status=status, confidence=round(conf, 4), reason=reason,
            raw_partial=raw_partial, candidates=candidates, sources=sources, features=feats or {}, bbox=bbox,
        )

    if not values:
        if not ok_readers:
            return result(None, FieldStatus.UNREADABLE, "no reader available")
        if illegible:
            return result(None, FieldStatus.UNREADABLE, "illegible characters")
        reasons = {x.value.reason for x in readings if x.reader in ok_readers}
        if reasons <= {"not_present", None}:
            return result(None, FieldStatus.NOT_PRESENT, "not present on the document")
        return result(None, FieldStatus.UNREADABLE, "no verbatim reading")

    # Cluster reader values; the biggest cluster wins, ties go to the primary reader,
    # then to a cluster whose value passes validation.
    clusters: list[list[str]] = []
    for r, v in values.items():
        for c in clusters:
            if _same(values[c[0]], v):
                c.append(r)
                break
        else:
            clusters.append([r])
    valid = {r: check_format(field, doc_type, v, ruleset).valid for r, v in values.items()}

    def rank(c: list[str]):
        return (len(c), primary_reader in c, any(valid[r] is not False for r in c))

    winner = max(clusters, key=rank)
    chosen_reader = primary_reader if primary_reader in winner else winner[0]
    value = values[chosen_reader]
    others = [v for r, v in per_reader.items() if r not in winner]
    disagree = len(clusters) > 1

    fmt = check_format(field, doc_type, value, ruleset)
    features = {
        "agreement": len(winner) / max(len(configured), 1),
        "pair_similarity": min([similarity(value, v) if v else 0.0 for v in others], default=1.0)
        if len(configured) > 1 else 0.0,
        "self_consistency": sum(consistency) / len(consistency) if consistency else 0.5,
        "readers_ok": len(ok_readers) / max(len(configured), 1),
        "illegible": 1.0 if illegible else 0.0,
        "format_valid": {True: 1.0, False: 0.0, None: 0.5}[fmt.valid],
        "glare": (quality.metrics.get("glare_fraction", 0.0) if quality else 0.0),
        "blur": (quality.metrics.get("blur_score", 0.0) if quality else 0.0),
        "handwritten": 1.0 if field.handwritten else 0.0,
        "logprob": sum(line_confs) / len(line_confs) if line_confs else 0.5,
    }
    conf = calibrator.predict(features, field.group)

    # Never invent: readers disagree and no candidate passes validation -> blank and flagged.
    if disagree and all(valid[r] is False for r in values):
        return result(None, FieldStatus.LOW_CONFIDENCE, "readers disagree and no reading passes validation", features, conf)
    if illegible and len(winner) < len(ok_readers):
        return result(None, FieldStatus.UNREADABLE, "illegible for some readers; others disagree", features, conf)
    if fmt.valid is False:
        return result(value, FieldStatus.INVALID_FORMAT, fmt.reason, features, conf)
    if conf < calibrator.threshold(field.group):
        reason = "readers disagree" if disagree else "below confidence threshold"
        return result(value, FieldStatus.LOW_CONFIDENCE, reason, features, conf)
    return result(value, FieldStatus.OK, None, features, conf)
