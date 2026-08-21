"""Python AST whitelist frontend for SvTypes constraints."""

from __future__ import annotations

import ast
import inspect
import os
import textwrap
from types import FunctionType

from ..errors import ConstraintSyntaxError, ConstraintUnsupportedError
from .ast import (
    AstNode,
    BinaryExpr,
    ConstraintBlock,
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
)

_BIN_OPS = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Mod: "mod",
    ast.LShift: "shl",
    ast.RShift: "shr",
    ast.BitAnd: "and",
    ast.BitOr: "or",
    ast.BitXor: "xor",
}
_UN_OPS = {
    ast.Not: "not",
    ast.Invert: "inv",
    ast.UAdd: "u+",
    ast.USub: "u-",
}
_CMP_OPS = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
}
_FORBIDDEN_ATTRS = {"value", "pack", "unpack", "to_bytes", "from_bytes"}
_module_ast_cache: dict[str, tuple[ast.AST, str]] = {}


def _loc(node: ast.AST, filename: str, constraint_name: str) -> SourceLoc:
    return SourceLoc(
        filename=filename,
        lineno=getattr(node, "lineno", 1),
        col_offset=getattr(node, "col_offset", 0),
        constraint_name=constraint_name,
    )


def _err(loc: SourceLoc, message: str) -> ConstraintSyntaxError:
    return ConstraintSyntaxError(f"{loc.format()}: {message}")


def _find_function(tree: ast.AST, name: str, lineno: int) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != name:
            continue
        if node.lineno == lineno:
            return node
        decorator_lines = [
            decorator.lineno for decorator in node.decorator_list if hasattr(decorator, "lineno")
        ]
        if lineno in decorator_lines:
            return node
    return None


def extract_function_def(
    fn: FunctionType,
    *,
    kind: str = "constraint",
) -> tuple[ast.FunctionDef, str, str]:
    if getattr(fn, "__wrapped__", None) is not None:
        decorator = "@constraint" if kind == "constraint" else "@rand_layer"
        raise ConstraintSyntaxError(
            f"{kind} {fn.__name__!r} must be decorated only by {decorator}"
        )
    filename = inspect.getsourcefile(fn) or inspect.getfile(fn) or "<unknown>"
    lineno = fn.__code__.co_firstlineno
    if filename and os.path.isfile(filename):
        try:
            cached = _module_ast_cache.get(filename)
            if cached is None:
                source = open(filename, encoding="utf-8").read()
                tree = ast.parse(source, filename=filename)
                _module_ast_cache[filename] = (tree, source)
            else:
                tree, source = cached
            found = _find_function(tree, fn.__name__, lineno)
            if found is not None:
                return found, filename, source
        except (OSError, SyntaxError):
            pass
    try:
        src = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(src, filename=filename)
        func = tree.body[0]
        if not isinstance(func, ast.FunctionDef):
            raise ConstraintSyntaxError(
                f"{filename}:{lineno}: cannot locate {kind} {fn.__name__}"
            )
        return func, filename, src
    except (OSError, TypeError, SyntaxError) as exc:
        raise ConstraintSyntaxError(
            f"{filename}:{lineno} in {kind} {fn.__name__}: source is unavailable"
        ) from exc


def parse_constraint_function(fn: FunctionType) -> ConstraintDecl:
    func, filename, source = extract_function_def(fn)
    loc = _loc(func, filename, fn.__name__)
    args = func.args
    if (
        len(args.args) != 1
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or args.kwonlyargs
        or args.posonlyargs
    ):
        raise _err(loc, "constraint method must take exactly one parameter (self)")
    if func.decorator_list:
        # Direct @constraint is stripped by the time we parse a standalone source
        # snippet, but a module AST still lists it. Extra decorators are rejected.
        extra = [
            item for item in func.decorator_list
            if not (isinstance(item, ast.Name) and item.id == "constraint")
        ]
        if extra:
            raise _err(loc, "decorator stack besides @constraint is not allowed")
    converter = _Converter(filename, fn.__name__, func.args.args[0].arg, source)
    body = converter.statements(func.body)
    if not body:
        raise _err(loc, "constraint body cannot be empty")
    block = ConstraintBlock(loc=loc, name=fn.__name__, body=body)
    return ConstraintDecl(name=fn.__name__, func=fn, block=block, loc=loc, filename=filename)


class _Converter:
    def __init__(self, filename: str, constraint_name: str, self_name: str, source: str) -> None:
        self.filename = filename
        self.constraint_name = constraint_name
        self.self_name = self_name
        self.source = source

    def loc(self, node: ast.AST) -> SourceLoc:
        return _loc(node, self.filename, self.constraint_name)

    def _int_radix(self, node: ast.AST) -> str:
        text = ast.get_source_segment(self.source, node) or ""
        text = text.strip().lower().replace("_", "")
        if text.startswith("-"):
            text = text[1:]
        if text.startswith("0x"):
            return "hex"
        if text.startswith("0b"):
            return "bin"
        if text.startswith("0o"):
            return "oct"
        return "dec"

    def statements(self, nodes: list[ast.stmt]) -> list[AstNode]:
        out: list[AstNode] = []
        for node in nodes:
            out.extend(self.statement(node))
        return out

    def statement(self, node: ast.stmt) -> list[AstNode]:
        loc = self.loc(node)
        if isinstance(node, ast.Pass):
            raise _err(loc, "pass is not allowed in a non-empty constraint")
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return []
            return [Predicate(loc=loc, expr=self.expr(node.value))]
        if isinstance(node, ast.If):
            if node.orelse and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                raise _err(loc, "elif is not supported")
            return [IfConstraint(
                loc=loc,
                cond=self.expr(node.test),
                then_body=self.statements(node.body),
                else_body=self.statements(node.orelse),
            )]
        if isinstance(node, ast.For):
            return [self._for_stmt(node)]
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            raise _err(loc, "assignment is not allowed in constraints")
        if isinstance(node, (ast.Return, ast.Yield, ast.Raise, ast.Try, ast.With, ast.While, ast.Assert)):
            raise _err(loc, f"{type(node).__name__} is not allowed in constraints")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            raise _err(loc, f"{type(node).__name__} is not allowed in constraints")
        raise _err(loc, f"unsupported statement {type(node).__name__}")

    def _for_stmt(self, node: ast.For) -> ForConstraint:
        loc = self.loc(node)
        if node.orelse:
            raise _err(loc, "for-else is not allowed")
        if not isinstance(node.target, ast.Name):
            raise _err(loc, "for-loop target must be a simple name")
        if not isinstance(node.iter, ast.Call):
            raise ConstraintUnsupportedError(f"{loc.format()}: for-loop iterator must be range(...)")
        call = node.iter
        if not isinstance(call.func, ast.Name) or call.func.id != "range":
            raise ConstraintUnsupportedError(f"{loc.format()}: for-loop iterator must be range(...)")
        if call.keywords:
            raise _err(loc, "range() does not accept keyword arguments")
        args = call.args
        if any(isinstance(arg, ast.Starred) for arg in args):
            raise _err(loc, "range() does not accept starred arguments")
        if len(args) == 1:
            start, stop = IntLiteral(loc=loc, value=0), self.expr(args[0])
        elif len(args) == 2:
            start, stop = self.expr(args[0]), self.expr(args[1])
        else:
            raise ConstraintUnsupportedError(f"{loc.format()}: range() step is not supported")
        return ForConstraint(
            loc=loc,
            var=node.target.id,
            start=start,
            stop=stop,
            body=self.statements(node.body),
        )

    def expr(self, node: ast.expr) -> AstNode:
        loc = self.loc(node)
        if isinstance(node, ast.Constant):
            if type(node.value) is int:
                return IntLiteral(loc=loc, value=node.value, radix=self._int_radix(node))
            raise _err(loc, f"literal {node.value!r} is not allowed")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            if type(node.operand.value) is int:
                return IntLiteral(loc=loc, value=-node.operand.value, radix=self._int_radix(node.operand))
        if isinstance(node, ast.Name):
            return NameRef(loc=loc, kind="name", name=node.id)
        if isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS:
                raise _err(loc, f"runtime attribute .{node.attr} is not allowed in constraints")
            if isinstance(node.value, ast.Name) and node.value.id == self.self_name:
                return FieldRef(loc=loc, path=[node.attr])
            if isinstance(node.value, ast.Name):
                return NameRef(loc=loc, kind="attr", name=f"{node.value.id}.{node.attr}")
            return MemberRef(loc=loc, base=self.expr(node.value), name=node.attr)
        if isinstance(node, ast.Subscript):
            return IndexRef(loc=loc, base=self.expr(node.value), index=self._index(node.slice))
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ConstraintUnsupportedError(f"{loc.format()}: operator {type(node.op).__name__} is not supported")
            return BinaryExpr(loc=loc, op=op, left=self.expr(node.left), right=self.expr(node.right))
        if isinstance(node, ast.UnaryOp):
            op = _UN_OPS.get(type(node.op))
            if op is None:
                raise _err(loc, f"unary operator {type(node.op).__name__} is not allowed")
            return UnaryExpr(loc=loc, op=op, expr=self.expr(node.operand))
        if isinstance(node, ast.BoolOp):
            op = "land" if isinstance(node.op, ast.And) else "lor"
            expr = self.expr(node.values[0])
            for item in node.values[1:]:
                expr = BinaryExpr(loc=loc, op=op, left=expr, right=self.expr(item))
            return expr
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.IfExp):
            return IfExpr(
                loc=loc,
                cond=self.expr(node.test),
                then_expr=self.expr(node.body),
                else_expr=self.expr(node.orelse),
            )
        if isinstance(node, ast.Call):
            raise _err(loc, "function calls are not allowed except range() in for-loops")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            raise _err(loc, "container literals are only allowed on the right-hand side of in/not in")
        if isinstance(node, ast.Lambda):
            raise _err(loc, "lambda is not allowed")
        if isinstance(node, ast.JoinedStr):
            raise _err(loc, "f-strings are not allowed")
        raise _err(loc, f"unsupported expression {type(node).__name__}")

    def _index(self, node: ast.AST) -> AstNode:
        loc = self.loc(node)
        if isinstance(node, ast.Slice):
            raise ConstraintUnsupportedError(f"{loc.format()}: part-select is not supported")
        return self.expr(node)

    def _compare(self, node: ast.Compare) -> AstNode:
        loc = self.loc(node)
        if any(isinstance(op, (ast.In, ast.NotIn, ast.Is, ast.IsNot)) for op in node.ops):
            if len(node.ops) != 1:
                raise _err(loc, "membership tests cannot be chained with other comparisons")
            op = node.ops[0]
            if isinstance(op, (ast.Is, ast.IsNot)):
                raise _err(loc, "is / is not are not allowed")
            rhs = node.comparators[0]
            if not isinstance(rhs, (ast.Tuple, ast.List, ast.Set)):
                raise _err(loc, "membership right-hand side must be a tuple/list/set literal")
            items = [self.expr(elt) for elt in rhs.elts]
            return InsideExpr(loc=loc, expr=self.expr(node.left), items=items, invert=isinstance(op, ast.NotIn))
        left = self.expr(node.left)
        comparators = [self.expr(item) for item in node.comparators]
        expr: AstNode | None = None
        current = left
        for op_node, right in zip(node.ops, comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise _err(loc, f"comparison {type(op_node).__name__} is not allowed")
            piece = BinaryExpr(loc=loc, op=op, left=current, right=right)
            expr = piece if expr is None else BinaryExpr(loc=loc, op="land", left=expr, right=piece)
            current = right
        assert expr is not None
        return expr
