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
    soft_constraints: tuple[Expr, ...] = ()
    selection_order: tuple[str, ...] = ()
    selection_groups: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "irs", tuple(self.irs))
        object.__setattr__(self, "random_paths", tuple(self.random_paths))
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))
        object.__setattr__(self, "var_index", MappingProxyType(dict(self.var_index)))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        object.__setattr__(self, "soft_constraints", tuple(self.soft_constraints))
        object.__setattr__(self, "selection_order", tuple(self.selection_order))
        object.__setattr__(self, "selection_groups", tuple(tuple(group) for group in self.selection_groups))


@dataclass(frozen=True, slots=True)
class SolveResult:
    """A backend outcome expressed as a normalized bit assignment."""

    assignments: Mapping[str, int] | None
    reason: str | None = None
    backend_reason: str | None = None

    def __post_init__(self) -> None:
        if self.reason is None:
            object.__setattr__(self, "reason", "sat" if self.assignments is not None else "unsat")
        if self.reason not in ("sat", "unsat", "unknown", "timeout", "resource_limit"):
            raise ValueError("invalid solve result reason")
        if (self.reason == "sat") != (self.assignments is not None):
            raise ValueError("only SAT results may carry assignments")
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

    @classmethod
    def unknown(cls, backend_reason: str | None = None) -> "SolveResult":
        return cls(None, "unknown", backend_reason)
