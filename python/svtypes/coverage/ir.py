"""Frozen, backend-neutral functional-coverage declaration IR.

The IR contains template semantics only.  Source locations, runtime bindings,
hits and report names travel in separate objects and therefore cannot affect a
declaration digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from .canonical import canonical_value, semantic_digest


COVERAGE_IR_VERSION = 1
_BIN_KINDS = frozenset({"normal", "ignore", "illegal", "default", "transition"})
_NamedIR = TypeVar("_NamedIR")


def _require_name(kind: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"coverage {kind} name must be a non-empty string")


def _canonical_options(
    owner: str, options: tuple[tuple[str, Any], ...]
) -> tuple[tuple[str, Any], ...]:
    seen: set[str] = set()
    normalized: list[tuple[str, Any]] = []
    for item in options:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"coverage {owner} options must contain (name, value) pairs")
        name, value = item
        _require_name("option", name)
        if name in seen:
            raise ValueError(f"coverage {owner} has duplicate option {name!r}")
        seen.add(name)
        # Validate declaration data now, instead of delaying a non-reproducible
        # value failure until a digest happens to be requested.
        canonical_value(value)
        normalized.append((name, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


def _canonical_named_items(kind: str, items: tuple[_NamedIR, ...]) -> tuple[_NamedIR, ...]:
    seen: set[str] = set()
    for item in items:
        name = getattr(item, "name", None)
        _require_name(kind, name)
        if name in seen:
            raise ValueError(f"coverage declaration has duplicate {kind} {name!r}")
        seen.add(name)
    return tuple(sorted(items, key=lambda item: item.name))


def _validate_parameter_names(kind: str, parameters: tuple["SampleParameterIR", ...]) -> None:
    seen: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, SampleParameterIR):
            raise TypeError(f"coverage {kind} parameters must be SampleParameterIR values")
        if parameter.name in seen:
            raise ValueError(f"coverage {kind} has duplicate parameter {parameter.name!r}")
        seen.add(parameter.name)


@dataclass(frozen=True, slots=True)
class SampleParameterIR:
    """A static covergroup constructor or sample formal."""

    name: str
    type_name: str

    def __post_init__(self) -> None:
        _require_name("parameter", self.name)
        if not isinstance(self.type_name, str) or not self.type_name:
            raise ValueError("coverage parameter type must be a non-empty string")

    def stable_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type_name}


@dataclass(frozen=True, slots=True)
class CoverageBinIR:
    """One named bin declaration in a point or cross."""

    name: str
    kind: str
    selector: Any = None

    def __post_init__(self) -> None:
        _require_name("bin", self.name)
        if self.kind not in _BIN_KINDS:
            choices = ", ".join(sorted(_BIN_KINDS))
            raise ValueError(f"coverage bin {self.name!r} has unsupported kind {self.kind!r}; expected {choices}")
        canonical_value(self.selector)

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

    def __post_init__(self) -> None:
        _require_name("point", self.name)
        canonical_value(self.expression)
        canonical_value(self.iff)
        object.__setattr__(self, "bins", _canonical_named_items("bin", self.bins))
        object.__setattr__(self, "options", _canonical_options(f"point {self.name!r}", self.options))

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

    def __post_init__(self) -> None:
        _require_name("cross", self.name)
        if not self.members:
            raise ValueError(f"coverage cross {self.name!r} must have at least one member")
        for member in self.members:
            _require_name("cross member", member)
        if len(set(self.members)) != len(self.members):
            raise ValueError(f"coverage cross {self.name!r} has duplicate members")
        canonical_value(self.iff)
        object.__setattr__(self, "bins", _canonical_named_items("bin", self.bins))
        object.__setattr__(self, "options", _canonical_options(f"cross {self.name!r}", self.options))

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
    reference_parameters: tuple[SampleParameterIR, ...] = ()
    sample_parameters: tuple[SampleParameterIR, ...] = ()
    points: tuple[CoveragePointIR, ...] = ()
    crosses: tuple[CoverageCrossIR, ...] = ()
    options: tuple[tuple[str, Any], ...] = ()
    type_options: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.sample_type, str) or not self.sample_type:
            raise ValueError("coverage sample type must be a non-empty string")
        _require_name("covergroup declaration", self.declaration_name)
        _validate_parameter_names("constructor", self.constructor_parameters)
        _validate_parameter_names("reference", self.reference_parameters)
        _validate_parameter_names("sample", self.sample_parameters)
        points = _canonical_named_items("point", self.points)
        crosses = _canonical_named_items("cross", self.crosses)
        point_names = {point.name for point in points}
        overlap = point_names.intersection(cross.name for cross in crosses)
        if overlap:
            name = min(overlap)
            raise ValueError(f"coverage declaration reuses {name!r} as both point and cross")
        for cross in crosses:
            unknown = [member for member in cross.members if member not in point_names]
            if unknown:
                raise ValueError(
                    f"coverage cross {cross.name!r} references unknown point {unknown[0]!r}"
                )
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "crosses", crosses)
        object.__setattr__(self, "options", _canonical_options("covergroup", self.options))
        object.__setattr__(self, "type_options", _canonical_options("covergroup type", self.type_options))

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
            "reference_parameters": [
                item.stable_dict() for item in self.reference_parameters
            ],
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
