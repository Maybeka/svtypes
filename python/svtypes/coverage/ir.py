"""Frozen, backend-neutral functional-coverage declaration IR.

The IR contains template semantics only.  Source locations, runtime bindings,
hits and report names travel in separate objects and therefore cannot affect a
declaration digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .canonical import canonical_value, semantic_digest


COVERAGE_IR_VERSION = 1


@dataclass(frozen=True, slots=True)
class SampleParameterIR:
    """A static covergroup constructor or sample formal."""

    name: str
    type_name: str

    def stable_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type_name}


@dataclass(frozen=True, slots=True)
class CoverageBinIR:
    """One named bin declaration in a point or cross."""

    name: str
    kind: str
    selector: Any = None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "selector": canonical_value(self.selector),
        }


@dataclass(frozen=True, slots=True)
class CoveragePointIR:
    """One point template and its declared bins."""

    name: str
    expression: Any
    bins: tuple[CoverageBinIR, ...] = ()
    iff: Any = None
    options: tuple[tuple[str, Any], ...] = ()

    def stable_dict(self) -> dict[str, Any]:
        return {
            "bins": [item.stable_dict() for item in self.bins],
            "expression": canonical_value(self.expression),
            "iff": canonical_value(self.iff),
            "name": self.name,
            "options": {key: canonical_value(value) for key, value in sorted(self.options)},
        }


@dataclass(frozen=True, slots=True)
class CoverageCrossIR:
    """One cross template; queue results remain instance-layout data."""

    name: str
    members: tuple[str, ...]
    bins: tuple[CoverageBinIR, ...] = ()
    iff: Any = None
    options: tuple[tuple[str, Any], ...] = ()

    def stable_dict(self) -> dict[str, Any]:
        return {
            "bins": [item.stable_dict() for item in self.bins],
            "iff": canonical_value(self.iff),
            "members": list(self.members),
            "name": self.name,
            "options": {key: canonical_value(value) for key, value in sorted(self.options)},
        }


@dataclass(frozen=True, slots=True)
class CoverageIR:
    """Complete semantic template for one stable covergroup declaration slot."""

    sample_type: str
    declaration_name: str
    constructor_parameters: tuple[SampleParameterIR, ...] = ()
    sample_parameters: tuple[SampleParameterIR, ...] = ()
    points: tuple[CoveragePointIR, ...] = ()
    crosses: tuple[CoverageCrossIR, ...] = ()
    options: tuple[tuple[str, Any], ...] = ()
    type_options: tuple[tuple[str, Any], ...] = ()

    @property
    def covergroup_type_id(self) -> str:
        """Stable declaration-slot identity, intentionally independent of bins."""
        return f"{self.sample_type}::{self.declaration_name}"

    def semantic_dict(self) -> dict[str, Any]:
        """Return the complete provenance-free template definition."""
        return {
            "constructor_parameters": [
                item.stable_dict() for item in self.constructor_parameters
            ],
            "covergroup_type_id": self.covergroup_type_id,
            "crosses": [item.stable_dict() for item in self.crosses],
            "declaration_name": self.declaration_name,
            "options": {key: canonical_value(value) for key, value in sorted(self.options)},
            "points": [item.stable_dict() for item in self.points],
            "sample_parameters": [item.stable_dict() for item in self.sample_parameters],
            "sample_type": self.sample_type,
            "type_options": {
                key: canonical_value(value) for key, value in sorted(self.type_options)
            },
            "version": COVERAGE_IR_VERSION,
        }

    @property
    def declaration_semantic_digest(self) -> str:
        return semantic_digest(self.semantic_dict())

    def definition_snapshot(self) -> dict[str, Any]:
        """Return the immutable semantic data retained by reports and merges."""
        return self.semantic_dict()


@dataclass(frozen=True, slots=True)
class CoverageProvenance:
    """Non-semantic origin data kept outside :class:`CoverageIR`."""

    filename: str | None = None
    line: int | None = None
    column: int | None = None
    declaration_qualname: str | None = None
