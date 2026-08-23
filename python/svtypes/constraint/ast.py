"""Untyped constraint AST produced by the Python frontend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceLoc:
    filename: str
    lineno: int
    col_offset: int
    constraint_name: str = ""
    path: str | None = None

    def format(self) -> str:
        where = f"{self.filename}:{self.lineno}:{self.col_offset}"
        if self.constraint_name:
            where = f"{where} in constraint {self.constraint_name}"
        if self.path:
            where = f"{where} path {self.path}"
        return where


@dataclass
class AstNode:
    loc: SourceLoc


@dataclass
class ConstraintBlock(AstNode):
    name: str
    body: list[AstNode]


@dataclass
class Predicate(AstNode):
    expr: AstNode


@dataclass
class IfConstraint(AstNode):
    cond: AstNode
    then_body: list[AstNode]
    else_body: list[AstNode]


@dataclass
class ForConstraint(AstNode):
    var: str
    start: AstNode
    stop: AstNode
    body: list[AstNode]


@dataclass
class FieldRef(AstNode):
    path: list[str]


@dataclass
class IndexRef(AstNode):
    base: AstNode
    index: AstNode


@dataclass
class MemberRef(AstNode):
    base: AstNode
    name: str


@dataclass
class NameRef(AstNode):
    kind: str
    name: str


@dataclass
class IntLiteral(AstNode):
    value: int
    radix: str = "dec"


@dataclass
class BinaryExpr(AstNode):
    op: str
    left: AstNode
    right: AstNode


@dataclass
class UnaryExpr(AstNode):
    op: str
    expr: AstNode


@dataclass
class InsideExpr(AstNode):
    expr: AstNode
    items: list[AstNode]
    invert: bool


@dataclass
class IfExpr(AstNode):
    cond: AstNode
    then_expr: AstNode
    else_expr: AstNode


@dataclass
class DistItem(AstNode):
    """One `dist[...]` item, using := (`each`) or :/ (`total`) weight."""

    low: AstNode
    high: AstNode | None
    weight: AstNode
    each: bool


@dataclass
class DistExpr(AstNode):
    """Distribution constraint written as ``expression @ dist[...]``."""

    expr: AstNode
    items: list[DistItem]


@dataclass
class UniqueExpr(AstNode):
    """Scalar `unique(...)` constraint declaration."""

    items: list[AstNode]


@dataclass
class SoftExpr(AstNode):
    """One source-level soft constraint expression."""

    expr: AstNode


@dataclass
class ConstraintDecl:
    """Class-level inoperable constraint declaration."""

    name: str
    func: Any
    block: ConstraintBlock
    loc: SourceLoc
    filename: str
    source: str | None = None

    def __get__(self, instance, owner):
        if instance is None:
            return self
        from .modes import ConstraintHandle

        return ConstraintHandle(instance, self.name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from ..errors import ConstraintError

        raise ConstraintError(
            f"constraint {self.name!r} cannot be called as a function; use randomize()"
        )

    def constraint_mode(self, on: int | None = None) -> int:
        from ..errors import ConstraintError

        raise ConstraintError(
            f"class-level constraint {self.name!r} is inoperable; use an instance handle"
        )


@dataclass(frozen=True, slots=True)
class RandLayerInfo:
    """Resolved membership of one named priority group, including builtin."""

    alias: str
    priority: int
    variables: tuple[str, ...]
    constraints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RandLayerBatch:
    """One randomize() invocation: all aliases that share a priority."""

    priority: int
    aliases: tuple[str, ...]
    variables: tuple[str, ...]
    constraints: tuple[str, ...]


@dataclass
class RandLayerDecl:
    """Class-level inoperable @rand_layer declaration."""

    alias: str
    priority: int
    members: tuple[str, ...]
    uses_super: bool
    loc: SourceLoc
    filename: str
    func: Any = None

    def __get__(self, instance, owner):
        if instance is None:
            return self
        from ..errors import ConstraintError

        raise ConstraintError(
            f"rand_layer {self.alias!r} is a declaration and cannot be accessed on an instance"
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from ..errors import ConstraintError

        raise ConstraintError(
            f"rand_layer {self.alias!r} cannot be called; use layered_randomize()"
        )
