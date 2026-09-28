"""DocumentType registry: each type maps to a schema, validators and a cross-document ruleset.

KYC documents are registry entries; receipts and notes plug in the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable

from .models import CheckResult, DocumentResult


class FieldGroup(str, Enum):
    NAMES = "names"
    DIGITS = "digits"
    DATES = "dates"
    ENUMS = "enums"
    TEXT = "text"


class Shape(str, Enum):
    ID1_CARD = "id1_card"  # 85.60 x 53.98 mm, aspect ~1.586
    A4 = "a4"  # aspect ~1.414
    ANY = "any"


ASPECT = {Shape.ID1_CARD: 85.60 / 53.98, Shape.A4: 297 / 210}

Validator = Callable[[DocumentResult, date], list[CheckResult]]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    description: str
    group: FieldGroup
    required: bool = True
    handwritten: bool = False
    label_ar: str = ""
    label_en: str = ""


@dataclass(frozen=True)
class DocTypeSpec:
    key: str
    domain: str
    label: str  # shown to models
    shape: Shape
    fields: tuple[FieldSpec, ...]
    validators: tuple[Validator, ...] = ()
    label_ar: str = ""

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{self.key} has no field '{name}'")


@dataclass
class Registry:
    types: dict[str, DocTypeSpec] = field(default_factory=dict)
    # domain -> ruleset (see crossdoc.RuleSet); typed loosely to avoid an import cycle
    rulesets: dict[str, object] = field(default_factory=dict)

    def register(self, spec: DocTypeSpec) -> None:
        if spec.key in self.types:
            raise ValueError(f"document type '{spec.key}' already registered")
        self.types[spec.key] = spec

    def get(self, key: str) -> DocTypeSpec:
        try:
            return self.types[key]
        except KeyError:
            raise KeyError(f"unknown document type '{key}'") from None

    def of_domain(self, domain: str) -> list[DocTypeSpec]:
        return [s for s in self.types.values() if s.domain == domain]


def default_registry() -> Registry:
    from . import kyc

    registry = Registry()
    kyc.register(registry)
    return registry
