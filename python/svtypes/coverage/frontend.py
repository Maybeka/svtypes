"""Restricted, source-only compiler for embedded coverage declarations."""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from ..bit import Bit
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic
from .ir import CoverageBinIR, CoverageIR, CoveragePointIR, SampleParameterIR


_BIN_BASES = {
    "bins": "normal",
    "ignore_bins": "ignore",
    "illegal_bins": "illegal",
    "transition_bins": "transition",
}
_POINT_BASES = {"CovPoint", "CovPointArray"}


def _error(message: str) -> CoverageDeclarationError:
    return CoverageDeclarationError("SVT-COV-SYNTAX", message)


def _name(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _annotation(node: ast.arg) -> str:
    if node.annotation is None:
        raise _error(f"coverage formal {node.arg!r} requires a type annotation")
    return ast.unparse(node.annotation)


def _operator(node: ast.AST) -> str:
    names = {
        ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%",
        ast.Pow: "**", ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
        ast.LShift: "<<", ast.RShift: ">>", ast.USub: "-", ast.UAdd: "+",
        ast.Invert: "~", ast.Not: "not", ast.And: "and", ast.Or: "or",
        ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">=",
    }
    for kind, value in names.items():
        if isinstance(node, kind):
            return value
    raise _error(f"unsupported coverage operator {node.__class__.__name__}")


def _expr(node: ast.AST) -> Any:
    """Normalize the frozen CoverageExpr subset without evaluating it."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (bool, int, str)) or node.value is None:
            return {"kind": "constant", "value": node.value}
        raise _error(f"unsupported coverage literal {node.value!r}")
    if isinstance(node, ast.Name):
        return {"kind": "name", "name": node.id}
    if isinstance(node, ast.Attribute):
        return {"kind": "attribute", "base": _expr(node.value), "name": node.attr}
    if isinstance(node, ast.UnaryOp):
        return {"kind": "unary", "operator": _operator(node.op), "operand": _expr(node.operand)}
    if isinstance(node, ast.BinOp):
        return {"kind": "binary", "operator": _operator(node.op), "left": _expr(node.left), "right": _expr(node.right)}
    if isinstance(node, ast.BoolOp):
        return {"kind": "boolean", "operator": _operator(node.op), "values": [_expr(value) for value in node.values]}
    if isinstance(node, ast.Compare):
        return {
            "kind": "compare", "left": _expr(node.left), "operators": [_operator(op) for op in node.ops],
            "comparators": [_expr(value) for value in node.comparators],
        }
    if isinstance(node, ast.IfExp):
        return {"kind": "if", "condition": _expr(node.test), "then": _expr(node.body), "else": _expr(node.orelse)}
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Slice):
            index = {
                "kind": "slice", "lower": _expr(node.slice.lower) if node.slice.lower else None,
                "upper": _expr(node.slice.upper) if node.slice.upper else None,
            }
        else:
            index = _expr(node.slice)
        return {"kind": "subscript", "base": _expr(node.value), "index": index}
    if isinstance(node, ast.Tuple):
        return {"kind": "tuple", "items": [_expr(item) for item in node.elts]}
    if isinstance(node, ast.Lambda) and not node.args.args and not node.args.kwonlyargs:
        return _expr(node.body)
    raise _error(f"unsupported coverage expression {node.__class__.__name__}")


def _slice_selector(node: ast.AST) -> Any:
    if isinstance(node, ast.Tuple):
        return {"kind": "values", "items": [_slice_selector(item) for item in node.elts]}
    if isinstance(node, ast.Slice):
        return {
            "kind": "range",
            "lower": _expr(node.lower) if node.lower else None,
            "upper": _expr(node.upper) if node.upper else None,
        }
    return _expr(node)


def _field_descriptor(owner: type[Any], source: ast.AST) -> Any | None:
    if not (
        isinstance(source, ast.Attribute)
        and isinstance(source.value, ast.Name)
        and source.value.id == "self"
    ):
        return None
    for base in owner.mro():
        if source.attr in base.__dict__:
            return base.__dict__[source.attr]
    return None


def _automatic_bins(owner: type[Any], point_name: str, source: ast.AST) -> tuple[CoverageBinIR, ...]:
    descriptor = _field_descriptor(owner, source)
    if isinstance(descriptor, Enum):
        return tuple(
            CoverageBinIR(f"auto[{member.value}]", "normal", {"kind": "constant", "value": member.value})
            for member in descriptor._enum_items
        )
    if not isinstance(descriptor, (Bit, Logic)):
        raise _error(f"coverage point {point_name!r} needs explicit bins because its automatic value domain is unknown")
    lower = -(1 << (descriptor.width - 1)) if descriptor.signed else 0
    upper = (1 << (descriptor.width - 1)) - 1 if descriptor.signed else (1 << descriptor.width) - 1
    count = min(upper - lower + 1, 64)
    base_width, remainder = divmod(upper - lower + 1, count)
    bins: list[CoverageBinIR] = []
    current = lower
    for index in range(count):
        width = base_width + (remainder if index == count - 1 else 0)
        end = current + width - 1
        selector: Any = {"kind": "constant", "value": current} if current == end else {
            "kind": "range",
            "lower": {"kind": "constant", "value": current},
            "upper": {"kind": "constant", "value": end},
        }
        name = f"auto[{current}]" if current == end else f"auto[{current}:{end}]"
        bins.append(CoverageBinIR(name, "normal", selector))
        current = end + 1
    return tuple(bins)


def _point_class(owner: type[Any], node: ast.ClassDef) -> CoveragePointIR:
    if len(node.bases) != 1 or _name(node.bases[0]) not in _POINT_BASES:
        raise _error(f"coverage declaration class {node.name!r} must inherit CovPoint or CovPointArray")
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
    if set(keywords).difference({"source", "iff", "length"}):
        raise _error(f"coverage point {node.name!r} has unsupported class keyword")
    if "source" not in keywords:
        raise _error(f"coverage point {node.name!r} requires source=")
    bins: list[CoverageBinIR] = []
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "option":
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise _error(f"coverage point {node.name!r} has unsupported body statement")
        bin_name = statement.targets[0].id
        if isinstance(statement.value, ast.Name) and statement.value.id == "default_bins":
            bins.append(CoverageBinIR(bin_name, "default"))
            continue
        if not isinstance(statement.value, ast.Subscript) or not isinstance(statement.value.value, ast.Name):
            raise _error(f"coverage point {node.name!r}.{bin_name} must use a bins declaration")
        kind = _BIN_BASES.get(statement.value.value.id)
        if kind is None:
            raise _error(f"coverage point {node.name!r}.{bin_name} has unsupported bin declaration")
        bins.append(CoverageBinIR(bin_name, kind, _slice_selector(statement.value.slice)))
    if not any(bin_.kind in {"normal", "default", "transition"} for bin_ in bins):
        bins.extend(_automatic_bins(owner, node.name, keywords["source"]))
    options: list[tuple[str, Any]] = []
    if _name(node.bases[0]) == "CovPointArray":
        if "length" not in keywords:
            raise _error(f"coverage point array {node.name!r} requires length=")
        options.append(("array_length", _expr(keywords["length"])))
    elif "length" in keywords:
        raise _error(f"coverage point {node.name!r} cannot specify length=")
    return CoveragePointIR(
        node.name,
        _expr(keywords["source"]),
        tuple(bins),
        _expr(keywords["iff"]) if "iff" in keywords else None,
        tuple(options),
    )


def _function_node(function: Any) -> ast.FunctionDef:
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, IOError) as error:
        raise _error(f"cannot read source for coverage declaration {function.__qualname__}") from error
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.FunctionDef):
            return node
    raise _error(f"cannot locate coverage declaration function {function.__qualname__}")


def compile_declaration(declaration: Any) -> CoverageIR:
    """Compile one :class:`CoverGroupDeclaration` without running user code."""
    function = _function_node(declaration.function)
    arguments = function.args.args
    if not arguments or arguments[0].arg != "self":
        raise _error(f"coverage declaration {declaration.qualified_name} requires self as its first formal")
    constructor_parameters = tuple(
        SampleParameterIR(argument.arg, _annotation(argument)) for argument in arguments[1:]
    )
    sample: ast.FunctionDef | None = None
    point_nodes: list[ast.ClassDef] = []
    for statement in function.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.FunctionDef) and statement.name == "sample":
            if sample is not None:
                raise _error(f"coverage declaration {declaration.qualified_name} defines sample twice")
            sample = statement
            continue
        if isinstance(statement, ast.ClassDef) and statement.name in {"option", "type_option"}:
            continue
        if isinstance(statement, ast.ClassDef):
            point_nodes.append(statement)
            continue
        raise _error(f"coverage declaration {declaration.qualified_name} has unsupported body statement")
    sample_parameters: tuple[SampleParameterIR, ...] = ()
    if sample is not None:
        if point_nodes:
            raise _error(f"coverage declaration {declaration.qualified_name} mixes outer and sample point declarations")
        if sample.body and any(not isinstance(item, ast.ClassDef) for item in sample.body):
            raise _error(f"coverage sample {declaration.qualified_name}.sample has unsupported body statement")
        point_nodes = [item for item in sample.body if isinstance(item, ast.ClassDef)]
        sample_parameters = tuple(SampleParameterIR(argument.arg, _annotation(argument)) for argument in sample.args.args)
    points = tuple(_point_class(declaration.owner, node) for node in point_nodes)
    sample_type = getattr(declaration.owner, "_svtypes_unified_type_name", declaration.owner.__name__)
    return CoverageIR(
        sample_type=sample_type,
        declaration_name=declaration.name,
        constructor_parameters=constructor_parameters,
        sample_parameters=sample_parameters,
        points=points,
    )
