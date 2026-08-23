"""Symbol resolution, type checking, and lowering to Typed Constraint IR."""

from __future__ import annotations

from typing import Any

from ..bit import Bit
from ..collection import Array
from ..enum import Enum
from ..errors import (
    ConstraintNameError,
    ConstraintTypeError,
    ConstraintUnsupportedError,
)
from ..logic import Logic
from ..object import ObjectDescriptor, SvObject, SvStruct
from ..parameter import Parameter
from ..schema import unified_type_name
from .ast import (
    AstNode,
    BinaryExpr,
    ConstraintBlock,
    DistExpr,
    DistItem,
    ConstraintDecl,
    FieldRef,
    ForConstraint,
    IfConstraint,
    IfExpr,
    IndexRef,
    InsideExpr,
    IntLiteral,
    MemberRef,
    NameRef,
    Predicate,
    SourceLoc,
    UnaryExpr,
    UniqueExpr,
)
from .ir import (
    BOOL,
    ConstraintIR,
    DistItem as IRDistItem,
    Expr,
    IRStmt,
    IRType,
    VarDecl,
    bv,
    c_bool,
    c_field,
    c_int,
    enum_ty,
)
from .leaves import format_path, join_path


def compile_block(cls: type, decl: ConstraintDecl) -> ConstraintIR:
    return _Analyzer(cls, decl).compile(decl.block)


class _Analyzer:
    def __init__(self, cls: type, decl: ConstraintDecl) -> None:
        self.cls = cls
        self.decl = decl
        # Parameters visible to a constraint come from the whole MRO (a
        # subclass or intermediate specialization can reference inherited
        # bound parameters); the class's own table is just the declarations.
        self.params: dict[str, Any] = {}
        for base in cls.__mro__:
            if base is object or base.__name__ in ("SvObject", "SvStruct"):
                continue
            for name, attr in base.__dict__.items():
                if isinstance(attr, Parameter) and name not in self.params:
                    self.params[name] = attr
        self.members = dict(getattr(cls, "_SvObject__svtypes_members", ()))
        self.globals = getattr(decl.func, "__globals__", {})
        self.loop_vars: dict[str, int] = {}
        self.loop_symbols: dict[str, str] = {}
        self.loop_index_arrays: dict[str, str] = {}
        self.vars: dict[str, VarDecl] = {}

    def compile(self, block: ConstraintBlock) -> ConstraintIR:
        statements: list[IRStmt] = []
        for stmt in block.body:
            statements.extend(self.lower_stmt(stmt))
        parameters = [
            (name, int(parameter.value))
            for name, parameter in self.params.items()
            if isinstance(parameter.value, int) and type(parameter.value) is not bool
        ]
        return ConstraintIR(
            name=block.name,
            predicates=[_flatten_stmt(stmt) for stmt in statements],
            vars=list(self.vars.values()),
            parameters=parameters,
            statements=statements,
        )

    def lower_stmt(self, node: AstNode) -> list[IRStmt]:
        if isinstance(node, Predicate):
            expr = self.as_bool(self.expr(node.expr))
            if _contains_dist(expr) and expr.op != "dist":
                raise ConstraintTypeError(
                    f"{node.loc.format()}: dist must be a complete constraint statement; "
                    "place it inside an if-body instead of combining it with Python boolean operators"
                )
            return [IRStmt(kind="pred", expr=expr)]
        if isinstance(node, IfConstraint):
            return [IRStmt(
                kind="if",
                cond=self.as_bool(self.expr(node.cond)),
                then_body=[item for stmt in node.then_body for item in self.lower_stmt(stmt)],
                else_body=[item for stmt in node.else_body for item in self.lower_stmt(stmt)],
            )]
        if isinstance(node, ForConstraint):
            if node.var in self.loop_vars or node.var in self.loop_symbols:
                raise ConstraintNameError(f"{node.loc.format()}: loop variable {node.var!r} is already bound")
            start = self.try_const_int(node.start)
            stop = self.try_const_int(node.stop)
            if start is not None and stop is not None:
                if start < 0 or stop < 0:
                    raise ConstraintTypeError(f"{node.loc.format()}: range bounds must be non-negative")
                # Constant bounds: unroll for the Python SMT/randomize path.
                out: list[IRStmt] = []
                for index in range(start, stop):
                    self.loop_vars[node.var] = index
                    for stmt in node.body:
                        out.extend(self.lower_stmt(stmt))
                self.loop_vars.pop(node.var, None)
                return out
            # Symbolic bounds (e.g. `range(WIDTH)` on a template): keep the
            # loop and let the target language solve it.
            self.loop_symbols[node.var] = node.var
            body: list[IRStmt] = []
            for stmt in node.body:
                body.extend(self.lower_stmt(stmt))
            self.loop_symbols.pop(node.var, None)
            array = self.loop_index_arrays.get(node.var)
            if array is None:
                raise ConstraintUnsupportedError(
                    f"{node.loc.format()}: a symbolic range() loop must index an array "
                    "in its body (SV constraints have no `for` statement)"
                )
            return [IRStmt(
                kind="for",
                var=node.var,
                start=self.expr(node.start),
                stop=self.expr(node.stop),
                then_body=body,
                array=array,
            )]
        raise ConstraintUnsupportedError(f"{node.loc.format()}: unsupported constraint statement")

    def try_const_int(self, node: AstNode) -> int | None:
        try:
            return self.const_int(node)
        except ConstraintTypeError:
            return None

    def expr(self, node: AstNode) -> Expr:
        if isinstance(node, IntLiteral):
            hint = None if node.radix == "dec" else node.radix
            return c_int(node.value, node.loc, hint=hint)
        if isinstance(node, NameRef):
            return self.name(node)
        if isinstance(node, FieldRef):
            return self.field(node.path, node.loc)
        if isinstance(node, IndexRef):
            return self.index(node)
        if isinstance(node, MemberRef):
            return self.member(node)
        if isinstance(node, UnaryExpr):
            inner = self.expr(node.expr)
            if node.op == "not":
                return Expr("not", (self.as_bool(inner),), BOOL, node.loc)
            typed = self.as_bv(inner)
            return Expr(node.op, (typed,), typed.ty, node.loc)
        if isinstance(node, BinaryExpr):
            return self.binary(node)
        if isinstance(node, InsideExpr):
            value = self.expr(node.expr)
            items = [self.expr(item) for item in node.items]
            if not items:
                raise ConstraintTypeError(f"{node.loc.format()}: membership set cannot be empty")
            expr = Expr("inside", (value, *items), BOOL, node.loc)
            if node.invert:
                expr = Expr("not", (expr,), BOOL, node.loc)
            return expr
        if isinstance(node, DistExpr):
            return self.dist(node)
        if isinstance(node, UniqueExpr):
            return self.unique(node)
        if isinstance(node, IfExpr):
            cond = self.as_bool(self.expr(node.cond))
            then_expr = self.expr(node.then_expr)
            else_expr = self.expr(node.else_expr)
            then_expr, else_expr = self.unify_pair(then_expr, else_expr, node.loc)
            return Expr("ite", (cond, then_expr, else_expr), then_expr.ty, node.loc)
        raise ConstraintUnsupportedError(f"{node.loc.format()}: unsupported expression")

    def dist(self, node: DistExpr) -> Expr:
        value = self.as_bv(self.expr(node.expr))
        value_paths = _field_paths(value)
        if not value_paths or not any(self.vars[path].declared_rand for path in value_paths):
            raise ConstraintTypeError(
                f"{node.loc.format()}: a dist expression must contain at least one rand variable"
            )
        if any(bool(getattr(self.vars[path].descriptor, "randc", False)) for path in value_paths):
            raise ConstraintTypeError(
                f"{node.loc.format()}: dist cannot be applied to a randc variable"
            )
        items: list[IRDistItem] = []
        for item in node.items:
            low = self.as_bv(self.expr(item.low))
            value_cmp, low_cmp = self.unify_compare(value, low, item.loc)
            high_cmp = None
            if item.high is not None:
                high = self.as_bv(self.expr(item.high))
                value_cmp, high_cmp = self.unify_compare(value_cmp, high, item.loc)
                _low_cmp, high_cmp = self.unify_compare(low_cmp, high_cmp, item.loc)
                low_cmp = _low_cmp
            weight = self.as_bv(self.expr(item.weight))
            if weight.op == "int" and int(weight.args[0]) < 0:
                raise ConstraintTypeError(f"{item.loc.format()}: dist weight must be non-negative")
            items.append(IRDistItem(
                low=low_cmp,
                high=high_cmp,
                weight=weight,
                each=item.each,
            ))
        if not items:
            raise ConstraintTypeError(f"{node.loc.format()}: dist[] cannot be empty")
        return Expr("dist", (value, tuple(items)), BOOL, node.loc)

    def unique(self, node: UniqueExpr) -> Expr:
        items = tuple(self.as_bv(self.expr(item)) for item in node.items)
        return Expr("unique", items, BOOL, node.loc)

    def name(self, node: NameRef) -> Expr:
        if node.kind == "attr":
            return self.global_attr(node.name, node.loc)
        name = node.name
        if name in self.loop_vars:
            return c_int(self.loop_vars[name], node.loc)
        if name in self.loop_symbols:
            return Expr("loopvar", (name,), bv(0, False), node.loc)
        if name in self.params:
            return self.parameter(name, node.loc)
        if name == "self":
            raise ConstraintNameError(f"{node.loc.format()}: self cannot be used as a value")
        value = self.globals.get(name)
        if type(value) is int:
            return c_int(value, node.loc)
        if isinstance(value, type) and issubclass(value, Enum):
            raise ConstraintNameError(f"{node.loc.format()}: enum type {name!r} must be qualified with a member")
        raise ConstraintNameError(f"{node.loc.format()}: unresolved name {name!r}")

    def global_attr(self, dotted: str, loc: SourceLoc) -> Expr:
        head, _, tail = dotted.partition(".")
        value = self.globals.get(head)
        if isinstance(value, type) and issubclass(value, Enum) and tail:
            if tail not in value._enum_map:
                raise ConstraintNameError(f"{loc.format()}: {head} has no member {tail}")
            member = value._enum_map[tail]
            return Expr("int", (int(member),), enum_ty(value), loc, hint=f"enum:{tail}")
        raise ConstraintNameError(f"{loc.format()}: unresolved name {dotted!r}")

    def parameter(self, name: str, loc: SourceLoc) -> Expr:
        parameter = self.params[name]
        value = parameter.value
        if value is None:
            # Unbound value parameter: emit a symbolic reference so a template
            # constraint can be rendered with the parameter name on the
            # parameterized class definition (e.g. `addr < WIDTH`).
            if parameter.is_type_parameter or parameter.dtype in ("str", "float"):
                raise ConstraintTypeError(
                    f"{loc.format()}: parameter {name!r} of type {parameter.dtype!r} "
                    "cannot be used in constraints"
                )
            return Expr("param", (name,), bv(0, False), loc)
        if type(value) is not int:
            raise ConstraintTypeError(f"{loc.format()}: parameter {name!r} is not an integer constant")
        return c_int(value, loc)

    def field(self, parts: list[str], loc: SourceLoc, base: Any | None = None, prefix: str = "") -> Expr:
        if base is None:
            if not parts:
                raise ConstraintNameError(f"{loc.format()}: empty field path")
            desc = self.members.get(parts[0])
            if parts[0] in self.params and len(parts) == 1:
                return self.parameter(parts[0], loc)
            if desc is None:
                raise ConstraintNameError(f"{loc.format()}: unknown field {parts[0]!r}")
            return self.field(parts[1:], loc, desc, parts[0])
        if not parts:
            return self.leaf_ref(prefix, base, loc)
        head, rest = parts[0], parts[1:]
        if isinstance(base, SvStruct):
            nested = dict(base.__class__._SvObject__svtypes_members).get(head)
            if nested is None:
                raise ConstraintNameError(f"{loc.format()}: {prefix} has no member {head!r}")
            return self.field(rest, loc, nested, f"{prefix}.{head}")
        raise ConstraintNameError(f"{loc.format()}: cannot access {head!r} on {prefix}")

    def member(self, node: MemberRef) -> Expr:
        path_parts, desc = self._ref_descriptor(node.base)
        if isinstance(desc, SvStruct):
            nested = dict(desc.__class__._SvObject__svtypes_members).get(node.name)
            if nested is None:
                raise ConstraintNameError(f"{node.loc.format()}: unknown member {node.name!r}")
            path = format_path([*path_parts, node.name])
            return self.leaf_ref(path, nested, node.loc)
        raise ConstraintNameError(f"{node.loc.format()}: cannot access {node.name!r}")

    def index(self, node: IndexRef) -> Expr:
        path_parts, desc = self._ref_descriptor(node)
        return self.leaf_ref(format_path(path_parts), desc, node.loc)

    def _ref_descriptor(self, node: AstNode) -> tuple[list[str | int], Any]:
        if isinstance(node, FieldRef):
            desc: Any = self.members.get(node.path[0])
            if desc is None:
                raise ConstraintNameError(f"{node.loc.format()}: unknown field {node.path[0]!r}")
            parts: list[str | int] = [node.path[0]]
            for name in node.path[1:]:
                if isinstance(desc, SvStruct):
                    nxt = dict(desc.__class__._SvObject__svtypes_members).get(name)
                    if nxt is None:
                        raise ConstraintNameError(f"{node.loc.format()}: unknown member {name!r}")
                    desc = nxt
                    parts.append(name)
                    continue
                raise ConstraintNameError(f"{node.loc.format()}: cannot access {name!r}")
            return parts, desc
        if isinstance(node, IndexRef):
            parts, desc = self._ref_descriptor(node.base)
            index_expr = self.expr(node.index)
            if index_expr.op == "loopvar":
                # Symbolic index (loop variable) on a template: keep the name
                # so the SV renderer emits `words[i]`; record the full array
                # path (e.g. header.words) for the foreach target.
                index_part: int | tuple[str, str] = ("idx", str(index_expr.args[0]))
                self.loop_index_arrays.setdefault(
                    str(index_expr.args[0]),
                    format_path(parts) if parts else "",
                )
            else:
                index = self.const_int(index_expr, node.loc)
                if not isinstance(desc, Array):
                    raise ConstraintUnsupportedError(f"{node.loc.format()}: indexing is only allowed on fixed arrays")
                if index < 0 or index >= desc._size:
                    raise ConstraintTypeError(f"{node.loc.format()}: index {index} is out of range")
                index_part = index
            if not isinstance(desc, Array):
                raise ConstraintUnsupportedError(f"{node.loc.format()}: indexing is only allowed on fixed arrays")
            return [*parts, index_part], desc._elem_template
        if isinstance(node, MemberRef):
            parts, desc = self._ref_descriptor(node.base)
            if isinstance(desc, SvStruct):
                nested = dict(desc.__class__._SvObject__svtypes_members).get(node.name)
                if nested is None:
                    raise ConstraintNameError(f"{node.loc.format()}: unknown member {node.name!r}")
                return [*parts, node.name], nested
            raise ConstraintNameError(f"{node.loc.format()}: cannot access {node.name!r}")
        raise ConstraintNameError(f"{node.loc.format()}: index base must be a field path")

    def leaf_ref(self, path: str, desc: Any, loc: SourceLoc, rest_desc: Any | None = None) -> Expr:
        target = rest_desc if rest_desc is not None else desc
        if isinstance(target, SvStruct):
            raise ConstraintTypeError(f"{loc.format()}: struct {path} cannot be used as a scalar")
        if isinstance(target, Array):
            raise ConstraintTypeError(f"{loc.format()}: array {path} cannot be used as a scalar")
        if isinstance(target, ObjectDescriptor) or (
            isinstance(target, SvObject) and not isinstance(target, SvStruct)
        ):
            raise ConstraintNameError(f"{loc.format()}: object handle {path} cannot appear in constraints")
        ty, projected, enum_name = self.solver_type(target, loc)
        declared_rand = self._declared_rand(path)
        self.vars.setdefault(
            path,
            VarDecl(
                path=path,
                declared_rand=declared_rand,
                width=ty.width,
                signed=ty.signed,
                projected_from=projected,
                enum_name=enum_name,
                descriptor=target,
            ),
        )
        return c_field(path, ty, loc)

    def _declared_rand(self, path: str) -> bool:
        root = path.split("[", 1)[0].split(".", 1)[0]
        desc = self.members.get(root)
        return bool(getattr(desc, "rand", False))

    def solver_type(self, desc: Any, loc: SourceLoc) -> tuple[IRType, str | None, str | None]:
        if isinstance(desc, Logic):
            return bv(desc.width, desc.signed), "logic", None
        if isinstance(desc, Bit):
            return bv(desc.width, desc.signed), None, None
        if isinstance(desc, Enum):
            return enum_ty(desc.__class__), None, unified_type_name(desc.__class__)
        raise ConstraintTypeError(
            f"{loc.format()}: type {type(desc).__name__} is not allowed in the solver domain"
        )

    def binary(self, node: BinaryExpr) -> Expr:
        left = self.expr(node.left)
        right = self.expr(node.right)
        if node.op in {"land", "lor"}:
            return Expr(node.op, (self.as_bool(left), self.as_bool(right)), BOOL, node.loc)
        if node.op in {"eq", "ne", "lt", "le", "gt", "ge"}:
            return self.compare(node.op, left, right, node.loc)
        if node.op in {"shl", "shr"}:
            left_bv = self.as_bv(left)
            amount = self.as_bv(right)
            amount = (
                Expr("int", (self._as_int_if_const(amount),), bv(left_bv.ty.width, False), node.loc, amount.undef, amount.hint)
                if amount.op == "int"
                else self._resize(amount, left_bv.ty.width, False, node.loc)
            )
            if amount.op == "int" and int(amount.args[0]) < 0:
                raise ConstraintTypeError(f"{node.loc.format()}: shift amount must be non-negative")
            return Expr(node.op, (left_bv, amount), left_bv.ty, node.loc)
        if node.op == "mod":
            if right.op == "int" and int(right.args[0]) == 0:
                raise ConstraintTypeError(f"{node.loc.format()}: modulo by compile-time 0")
        left_bv, right_bv = self.unify_arith(left, right, node.loc)
        return Expr(node.op, (left_bv, right_bv), left_bv.ty, node.loc)

    def compare(self, op: str, left: Expr, right: Expr, loc: SourceLoc) -> Expr:
        left_u, right_u = self.unify_compare(left, right, loc)
        return Expr(op, (left_u, right_u), BOOL, loc)

    def as_bool(self, expr: Expr) -> Expr:
        if expr.ty.is_bool:
            return expr
        typed = self.as_bv(expr)
        zero = Expr("int", (0,), typed.ty, expr.loc)
        return Expr("ne", (typed, zero), BOOL, expr.loc)

    def as_bv(self, expr: Expr) -> Expr:
        if expr.ty.is_bool:
            raise ConstraintTypeError(f"{_fmt(expr)}: Bool cannot be used as a bit-vector")
        if expr.ty.is_enum:
            return Expr(expr.op, expr.args, expr.ty.as_bv(), expr.loc, expr.undef, expr.hint)
        if expr.op == "int":
            return expr
        return expr

    def const_int(self, node: AstNode | Expr, loc: SourceLoc | None = None) -> int:
        expr = node if isinstance(node, Expr) else self.expr(node)
        if expr.op == "int":
            value = int(expr.args[0])
            if type(value) is bool:
                raise ConstraintTypeError(f"{(loc or expr.loc).format()}: bool is not an integer")
            return value
        raise ConstraintTypeError(f"{(loc or expr.loc).format()}: expected a compile-time integer")

    def unify_pair(self, left: Expr, right: Expr, loc: SourceLoc) -> tuple[Expr, Expr]:
        if left.ty.is_bool or right.ty.is_bool:
            return self.as_bool(left), self.as_bool(right)
        return self.unify_arith(left, right, loc)

    def unify_arith(self, left: Expr, right: Expr, loc: SourceLoc) -> tuple[Expr, Expr]:
        left_bv = self.as_bv(left)
        right_bv = self.as_bv(right)
        signed = _signed_of(left_bv) or _signed_of(right_bv)
        width = max(_width_of(left_bv), _width_of(right_bv))
        return (
            self._resize(left_bv, width, signed, loc),
            self._resize(right_bv, width, signed, loc),
        )

    def unify_compare(self, left: Expr, right: Expr, loc: SourceLoc) -> tuple[Expr, Expr]:
        left_bv = self.as_bv(left)
        right_bv = self.as_bv(right)
        l_signed = _signed_of(left_bv)
        r_signed = _signed_of(right_bv)
        signed = l_signed and r_signed
        if l_signed != r_signed:
            signed = False
        width = max(_width_of(left_bv), _width_of(right_bv))
        return (
            self._resize(left_bv, width, signed, loc),
            self._resize(right_bv, width, signed, loc),
        )

    def _resize(self, expr: Expr, width: int, signed: bool, loc: SourceLoc) -> Expr:
        if expr.op == "int":
            return Expr("int", (int(expr.args[0]),), bv(width, signed), loc, expr.undef, expr.hint)
        if expr.ty.width == width and expr.ty.signed == signed:
            return expr
        return Expr(expr.op, expr.args, bv(width, signed), expr.loc, expr.undef, expr.hint)

    def _as_int_if_const(self, expr: Expr) -> int:
        if expr.op == "int":
            return int(expr.args[0])
        return 0


def _flatten_stmt(stmt: IRStmt) -> Expr:
    if stmt.kind == "pred":
        assert stmt.expr is not None
        return stmt.expr
    if stmt.kind == "for":
        # Symbolic loops are only rendered for the target language; the Python
        # template IR never solves them, so they flatten to a true placeholder.
        loc = stmt.start.loc if stmt.start is not None else None
        return c_bool(True, loc)
    loc = stmt.cond.loc if stmt.cond is not None else None
    then_expr = _and_all([_flatten_stmt(item) for item in stmt.then_body], loc)
    else_expr = (
        _and_all([_flatten_stmt(item) for item in stmt.else_body], loc)
        if stmt.else_body
        else c_bool(True, loc)
    )
    cond = stmt.cond
    assert cond is not None
    return Expr("land", (
        Expr("lor", (Expr("not", (cond,), BOOL, loc), then_expr), BOOL, loc),
        Expr("lor", (cond, else_expr), BOOL, loc),
    ), BOOL, loc)


def _contains_dist(expr: Expr) -> bool:
    if expr.op == "dist":
        return True
    for arg in expr.args:
        if isinstance(arg, Expr) and _contains_dist(arg):
            return True
    return False


def _field_paths(expr: Expr) -> set[str]:
    if expr.op == "field":
        return {str(expr.args[0])}
    paths: set[str] = set()
    for arg in expr.args:
        if isinstance(arg, Expr):
            paths.update(_field_paths(arg))
    return paths


def _signed_of(expr: Expr) -> bool:
    if expr.op == "int":
        return int(expr.args[0]) < 0
    return expr.ty.signed


def _width_of(expr: Expr) -> int:
    if expr.op == "int":
        value = int(expr.args[0])
        if value >= 0:
            return max(value.bit_length(), 1)
        return max(value.bit_length() + 1, 2)
    return expr.ty.width or 1


def _and_all(preds: list[Expr], loc: SourceLoc) -> Expr:
    if not preds:
        return c_bool(True, loc)
    expr = preds[0]
    for item in preds[1:]:
        expr = Expr("land", (expr, item), BOOL, loc)
    return expr


def _fmt(expr: Expr) -> str:
    return expr.loc.format() if expr.loc else "<constraint>"
