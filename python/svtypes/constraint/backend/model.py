"""Backend-neutral request and result types for constrained random solving."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..ir import ConstraintIR, Expr, VarDecl


@dataclass(frozen=True, slots=True)
class SolveRequest:
    """The normalized, per-call input presented to a constraint backend.

    This is intentionally runtime-only: field modes, sampled candidates and
    instance state do not alter a class's Constraint IR or its schema digest.
    """

    irs: tuple[ConstraintIR, ...]
    random_paths: tuple[str, ...]
    state: Mapping[str, int]
    var_index: Mapping[str, VarDecl]
    assumptions: tuple[Expr, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "irs", tuple(self.irs))
        object.__setattr__(self, "random_paths", tuple(self.random_paths))
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))
        object.__setattr__(self, "var_index", MappingProxyType(dict(self.var_index)))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))


@dataclass(frozen=True, slots=True)
class SolveResult:
    """A backend outcome expressed as a normalized bit assignment."""

    assignments: Mapping[str, int] | None

    def __post_init__(self) -> None:
        if self.assignments is not None:
            object.__setattr__(self, "assignments", MappingProxyType(dict(self.assignments)))

    @property
    def is_sat(self) -> bool:
        return self.assignments is not None

    @classmethod
    def sat(cls, assignments: Mapping[str, int]) -> "SolveResult":
        return cls(dict(assignments))

    @classmethod
    def unsat(cls) -> "SolveResult":
        return cls(None)
