"""DocumentType registry: each type maps to a schema, validators and cross-document rules.

KYC documents are registry entries; receipts, price books and notes plug in the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .models import CheckResult, DocumentReading

Validator = Callable[[DocumentReading, date], list[CheckResult]]
CrossRule = Callable[[list[DocumentReading]], list[CheckResult]]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class DocTypeSpec:
    key: str
    domain: str
    label: str  # shown to image readers
    fields: tuple[FieldSpec, ...]
    validators: tuple[Validator, ...] = ()

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass
class Registry:
    types: dict[str, DocTypeSpec] = field(default_factory=dict)
    cross_rules: dict[str, list[CrossRule]] = field(default_factory=dict)

    def register(self, spec: DocTypeSpec) -> None:
        if spec.key in self.types:
            raise ValueError(f"document type '{spec.key}' already registered")
        self.types[spec.key] = spec

    def add_cross_rule(self, domain: str, rule: CrossRule) -> None:
        self.cross_rules.setdefault(domain, []).append(rule)

    def get(self, key: str) -> DocTypeSpec:
        try:
            return self.types[key]
        except KeyError:
            raise KeyError(f"unknown document type '{key}'") from None


def default_registry() -> Registry:
    from . import kyc

    registry = Registry()
    kyc.register(registry)
    return registry
