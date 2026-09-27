"""Safe symbolic expressions for parameter-dependent declarations.

The public DSL supplies a lambda or a one-return function.  This module never
executes that callable: it extracts and validates its AST, then exposes a
small expression object that can be rendered for SV/C++ or evaluated only
against an explicit specialization environment.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import DeclarationError


_BINARY = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%",
    ast.Pow: "**", ast.LShift: "<<", ast.RShift: ">>", ast.BitAnd: "&",
    ast.BitOr: "|", ast.BitXor: "^",
}
_UNARY = {ast.UAdd: "+", ast.USub: "-", ast.Invert: "~", ast.Not: "not"}
_COMPARE = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=",
}


def _error(message: str) -> DeclarationError:
    return DeclarationError(f"parameter expression: {message}")


def _extract_callable_expression(fn: Any) -> tuple[tuple[str, ...], ast.expr]:
    if not callable(fn):
        raise TypeError("parameter expression must be a lambda or function")
    try:
        source = inspect.getsource(fn)
    except (OSError, IOError, TypeError) as exc:
        raise _error("source is unavailable; declare the expression in a source file") from exc
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise _error("source could not be parsed") from exc

    candidates: list[tuple[tuple[str, ...], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            if node.args.vararg or node.args.kwarg or node.args.defaults or node.args.kwonlyargs:
                raise _error("lambda cannot use defaults, *args, **kwargs, or keyword-only arguments")
            candidates.append((tuple(arg.arg for arg in node.args.args), node.body))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == getattr(fn, "__name__", None):
            if node.args.vararg or node.args.kwarg or node.args.defaults or node.args.kwonlyargs:
                raise _error("function cannot use defaults, *args, **kwargs, or keyword-only arguments")
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Return) or node.body[0].value is None:
                raise _error("function must have exactly one return expression")
            candidates.append((tuple(arg.arg for arg in node.args.args), node.body[0].value))
    if len(candidates) != 1:
        raise _error("source must contain exactly one matching lambda or function")
    return candidates[0]


def _validate(node: ast.AST, parameters: set[str]) -> None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            raise _error("only integer literals are allowed")
        return
    if isinstance(node, ast.Name):
        if node.id not in parameters:
            raise _error(f"name {node.id!r} is not a parameter-expression argument")
        return
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        _validate(node.operand, parameters)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        _validate(node.left, parameters)
        _validate(node.right, parameters)
        return
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate(value, parameters)
        return
    if isinstance(node, ast.Compare) and all(type(op) in _COMPARE for op in node.ops):
        _validate(node.left, parameters)
        for comparator in node.comparators:
            _validate(comparator, parameters)
        return
    if isinstance(node, ast.IfExp):
        _validate(node.test, parameters)
        _validate(node.body, parameters)
        _validate(node.orelse, parameters)
        return
    raise _error(f"unsupported syntax {node.__class__.__name__}")


def _render(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.UnaryOp):
        operator = _UNARY[type(node.op)]
        return f"(!{_render(node.operand)})" if operator == "not" else f"({operator}{_render(node.operand)})"
    if isinstance(node, ast.BinOp):
        return f"({_render(node.left)} {_BINARY[type(node.op)]} {_render(node.right)})"
    if isinstance(node, ast.BoolOp):
        operator = " && " if isinstance(node.op, ast.And) else " || "
        return "(" + operator.join(_render(item) for item in node.values) + ")"
    if isinstance(node, ast.Compare):
        parts = [_render(node.left)]
        for operator, comparator in zip(node.ops, node.comparators):
            parts.extend((_COMPARE[type(operator)], _render(comparator)))
        return "(" + " ".join(parts) + ")"
    if isinstance(node, ast.IfExp):
        return f"({_render(node.test)} ? {_render(node.body)} : {_render(node.orelse)})"
    raise AssertionError(f"unvalidated node {node!r}")


def _trunc_div(left: int, right: int) -> int:
    if right == 0:
        raise ZeroDivisionError("division by zero in parameter expression")
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def _evaluate(node: ast.AST, values: Mapping[str, int]) -> int | bool:
    if isinstance(node, ast.Constant):
        return int(node.value)
    if isinstance(node, ast.Name):
        try:
            value = values[node.id]
        except KeyError as exc:
            raise _error(f"missing parameter value {node.id!r}") from exc
        if isinstance(value, bool) or not isinstance(value, int):
            raise _error(f"parameter {node.id!r} is not an integer")
        return value
    if isinstance(node, ast.UnaryOp):
        value = _evaluate(node.operand, values)
        if isinstance(node.op, ast.UAdd): return +int(value)
        if isinstance(node.op, ast.USub): return -int(value)
        if isinstance(node.op, ast.Invert): return ~int(value)
        return not bool(value)
    if isinstance(node, ast.BinOp):
        left, right = int(_evaluate(node.left, values)), int(_evaluate(node.right, values))
        if isinstance(node.op, ast.Add): return left + right
        if isinstance(node.op, ast.Sub): return left - right
        if isinstance(node.op, ast.Mult): return left * right
        if isinstance(node.op, ast.Div): return _trunc_div(left, right)
        if isinstance(node.op, ast.Mod): return left % right
        if isinstance(node.op, ast.Pow): return left ** right
        if isinstance(node.op, ast.LShift): return left << right
        if isinstance(node.op, ast.RShift): return left >> right
        if isinstance(node.op, ast.BitAnd): return left & right
        if isinstance(node.op, ast.BitOr): return left | right
        return left ^ right
    if isinstance(node, ast.BoolOp):
        values_iter = iter(node.values)
        result = bool(_evaluate(next(values_iter), values))
        for item in values_iter:
            if isinstance(node.op, ast.And):
                result = result and bool(_evaluate(item, values))
            else:
                result = result or bool(_evaluate(item, values))
        return result
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, values)
        for operator, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, values)
            if not {
                ast.Eq: left == right, ast.NotEq: left != right, ast.Lt: left < right,
                ast.LtE: left <= right, ast.Gt: left > right, ast.GtE: left >= right,
            }[type(operator)]:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _evaluate(node.body if _evaluate(node.test, values) else node.orelse, values)
    raise AssertionError(f"unvalidated node {node!r}")


@dataclass(frozen=True, slots=True)
class ParameterExpr:
    """Validated, immutable parameter expression IR."""

    parameters: tuple[str, ...]
    _node: ast.expr

    @classmethod
    def parse(cls, fn: Any) -> "ParameterExpr":
        parameters, node = _extract_callable_expression(fn)
        if len(set(parameters)) != len(parameters):
            raise _error("argument names must be unique")
        _validate(node, set(parameters))
        result = cls(parameters, node)
        if set(result.parameters) != set(result.dependencies):
            raise _error("function arguments must exactly match referenced parameters")
        return result

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(sorted({node.id for node in ast.walk(self._node) if isinstance(node, ast.Name)}))

    def render(self) -> str:
        return _render(self._node)

    def evaluate(self, values: Mapping[str, int]) -> int:
        result = _evaluate(self._node, values)
        if isinstance(result, bool) or not isinstance(result, int):
            raise _error("result is not an integer")
        return result
