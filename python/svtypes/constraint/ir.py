"""Typed constraint IR and stable digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .ast import SourceLoc

CONSTRAINT_IR_VERSION = 1

_BINOPS = {
    "add", "sub", "mul", "mod", "shl", "shr",
    "and", "or", "xor",
    "eq", "ne", "lt", "le", "gt", "ge",
    "land", "lor",
}
_UNOPS = {"u+", "u-", "not", "inv"}


@dataclass(frozen=True, slots=True)
class IRType:
    kind: str
    width: int = 0
    signed: bool = False
    enum_type: type | None = None

    @property
    def is_bool(self) -> bool:
        return self.kind == "bool"

    @property
    def is_bv(self) -> bool:
        return self.kind == "bv"

    @property
    def is_enum(self) -> bool:
        return self.kind == "enum"

    def as_bv(self) -> "IRType":
        if self.is_bv:
            return self
        if self.is_enum:
            enum_cls = self.enum_type
            return IRType("bv", enum_cls._width, enum_cls._signed, None)  # type: ignore[union-attr]
        raise TypeError("cannot project Bool to BitVector")


BOOL = IRType("bool")


def bv(width: int, signed: bool = False) -> IRType:
    return IRType("bv", width, signed)


def enum_ty(enum_cls: type) -> IRType:
    return IRType("enum", enum_cls._width, enum_cls._signed, enum_cls)  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class Expr:
    op: str
    args: tuple[Any, ...]
    ty: IRType
    loc: SourceLoc | None = None
    undef: bool = False
    hint: str | None = None

    def to_stable(self) -> list[Any]:
        if self.op == "bool":
            return ["bool", bool(self.args[0])]
        if self.op == "int":
            return ["int", int(self.args[0])]
        if self.op == "field":
            return ["field", str(self.args[0])]
        if self.op == "size":
            return ["size", str(self.args[0])]
        if self.op == "param":
            return ["param", str(self.args[0])]
        if self.op == "loopvar":
            return ["loopvar", str(self.args[0])]
        if self.op in _UNOPS:
            return [self.op, self.args[0].to_stable()]
        if self.op == "ite":
            return ["ite", *(arg.to_stable() for arg in self.args)]
        if self.op == "inside":
            return ["inside", *(arg.to_stable() for arg in self.args)]
        if self.op == "dist":
            value, items = self.args
            return ["dist", value.to_stable(), *(item.to_stable() for item in items)]
        if self.op == "unique":
            return ["unique", *(arg.to_stable() for arg in self.args)]
        if self.op in _BINOPS:
            return [self.op, self.args[0].to_stable(), self.args[1].to_stable()]
        raise ValueError(f"unknown IR operator {self.op!r}")


def c_bool(value: bool, loc: SourceLoc | None = None) -> Expr:
    return Expr("bool", (bool(value),), BOOL, loc)


def c_int(value: int, loc: SourceLoc | None = None, hint: str | None = None) -> Expr:
    return Expr("int", (int(value),), bv(0, False), loc, hint=hint)


def c_field(path: str, ty: IRType, loc: SourceLoc | None = None) -> Expr:
    return Expr("field", (path,), ty, loc)


@dataclass(frozen=True, slots=True)
class DistItem:
    """Typed distribution item; ``each`` distinguishes := from :/."""

    low: Expr
    high: Expr | None
    weight: Expr
    each: bool

    def to_stable(self) -> list[Any]:
        item = ["range" if self.high is not None else "value", self.low.to_stable()]
        if self.high is not None:
            item.append(self.high.to_stable())
        item.extend([":=" if self.each else ":/", self.weight.to_stable()])
        return item


@dataclass(frozen=True, slots=True)
class VarDecl:
    path: str
    declared_rand: bool
    width: int
    signed: bool
    projected_from: str | None
    enum_name: str | None
    descriptor: Any = None
    kind: str = "field"

    def to_stable(self) -> dict[str, Any]:
        return {
            "declared_rand": self.declared_rand,
            "enum": self.enum_name,
            "path": self.path,
            "projected_from": self.projected_from,
            "signed": self.signed,
            "width": self.width,
            "kind": self.kind,
        }


@dataclass
class IRStmt:
    """Structured constraint statement used by the SV renderer."""

    kind: str
    expr: Expr | None = None
    cond: Expr | None = None
    then_body: list["IRStmt"] = field(default_factory=list)
    else_body: list["IRStmt"] = field(default_factory=list)
    var: str | None = None
    start: Expr | None = None
    stop: Expr | None = None
    array: str | None = None
    before: tuple[str, ...] | None = None
    after: tuple[str, ...] | None = None


@dataclass
class ConstraintIR:
    name: str
    predicates: list[Expr]
    vars: list[VarDecl]
    soft_predicates: list[Expr] = field(default_factory=list)
    solve_before: list[tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)
    parameters: list[tuple[str, int]] = field(default_factory=list)
    statements: list[IRStmt] = field(default_factory=list)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "predicates": [pred.to_stable() for pred in self.predicates],
            "soft_predicates": [pred.to_stable() for pred in self.soft_predicates],
            "solve_before": [[*before, "before", *after] for before, after in self.solve_before],
            "vars": [var.to_stable() for var in sorted(self.vars, key=lambda item: item.path)],
            "version": CONSTRAINT_IR_VERSION,
        }

    def digest(self) -> str:
        from ..schema import _fingerprint

        return _fingerprint(self.stable_dict()).hex()


def ir_digest(ir: ConstraintIR) -> str:
    return ir.digest()


def constraints_schema_entries(blocks: Mapping[str, ConstraintIR]) -> list[dict[str, str]]:
    entries = [
        {"ir_digest": ir.digest(), "name": name}
        for name, ir in blocks.items()
    ]
    return sorted(entries, key=lambda item: item["name"])
