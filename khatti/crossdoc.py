"""Declarative ruleset (YAML): field formats, enums, lifetimes and cross-document rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .arabic import match_names, normalize, parse_date, similarity
from .models import CrossCheck, DocumentResult


@dataclass(frozen=True)
class FormatSpec:
    pattern: re.Pattern
    verified: bool
    note: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    left: str | None = None
    right: str | None = None
    matcher: str | None = None
    threshold: float | None = None
    on_partial: str = "review"
    each: tuple[str, ...] = ()
    check: str | None = None


@dataclass
class RuleSet:
    version: str
    adult_age: int = 18
    formats: dict[str, FormatSpec] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    lifetimes: dict[str, dict] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "RuleSet":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        rules = []
        for r in raw.get("rules", []):
            r = dict(r)
            r["each"] = tuple(r.get("each", ()))
            rules.append(Rule(**r))
        return cls(
            version=str(raw["version"]),
            adult_age=int(raw.get("adult_age", 18)),
            formats={
                k: FormatSpec(re.compile(v["pattern"]), bool(v.get("verified", False)), v.get("note", ""))
                for k, v in (raw.get("formats") or {}).items()
            },
            enums={k: list(v) for k, v in (raw.get("enums") or {}).items()},
            lifetimes=raw.get("lifetimes") or {},
            rules=rules,
        )

    def format_for(self, doc_type: str, field_name: str) -> FormatSpec | None:
        return self.formats.get(f"{doc_type}.{field_name}")

    def enum_for(self, doc_type: str, field_name: str) -> list[str] | None:
        return self.enums.get(f"{doc_type}.{field_name}")


def _resolve(ref: str, docs: list[DocumentResult]) -> tuple[DocumentResult, str] | None:
    """'national_id.full_name_ar' -> the document (front or back) that carries that field."""
    doc_key, _, field_name = ref.partition(".")
    for d in docs:
        if (d.doc_type == doc_key or d.doc_type.startswith(doc_key + "_")) and field_name in d.fields:
            return d, field_name
    return None


def _match(rule: Rule, left: str, right: str) -> CrossCheck:
    if rule.matcher == "arabic_name_aligned":
        m = match_names(left, right)
        return CrossCheck(rule=rule.id, status=m.status, score=round(m.score, 3), detail=m.detail)
    if rule.matcher == "normalized_fuzzy":
        s = similarity(left, right)
        ok = s >= (rule.threshold or 0.9)
        return CrossCheck(rule=rule.id, status="match" if ok else "mismatch", score=round(s, 3))
    if rule.matcher == "exact":
        ok = normalize(left) == normalize(right)
        return CrossCheck(rule=rule.id, status="match" if ok else "mismatch", score=1.0 if ok else 0.0)
    raise ValueError(f"unknown matcher '{rule.matcher}'")


def evaluate(ruleset: RuleSet, docs: list[DocumentResult], today: date) -> list[CrossCheck]:
    out: list[CrossCheck] = []
    for rule in ruleset.rules:
        if rule.check == "after_today":
            bad = []
            for ref in rule.each:
                hit = _resolve(ref, docs)
                if hit:
                    d, f = hit
                    p = parse_date(d.fields[f].value)
                    if p and p.value <= today:
                        bad.append(f"{d.slot}.{f} {p.value.isoformat()}")
            out.append(CrossCheck(rule=rule.id, status="fail" if bad else "pass", detail="; ".join(bad)))
            continue

        l, r = _resolve(rule.left or "", docs), _resolve(rule.right or "", docs)
        if not l or not r:
            out.append(CrossCheck(rule=rule.id, status="skipped", detail="document not in session"))
            continue
        lv, rv = l[0].fields[l[1]].value, r[0].fields[r[1]].value
        if not lv or not rv:
            out.append(CrossCheck(rule=rule.id, status="skipped", detail="field unreadable"))
            continue
        out.append(_match(rule, lv, rv))
    return out


# Cross-check statuses that allow auto-pass.
PASSING = {"match", "pass", "skipped"}
