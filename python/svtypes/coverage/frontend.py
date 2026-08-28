"""Restricted, source-only compiler for embedded coverage declarations."""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from ..bit import Bit
from ..collection import Array, AssocArray, DynArray, Queue
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic
from ..base import TypeBase
from .ir import CoverageBinIR, CoverageIR, CoveragePointIR, SampleParameterIR


_BIN_BASES = {
    "bins": "normal",
    "ignore_bins": "ignore",
    "illegal_bins": "illegal",
    "transition_bins": "transition",
}
_POINT_BASES = {"CovPoint", "CovPointArray"}
_GROUP_OPTIONS = {"name", "comment", "per_instance", "get_inst_coverage", "weight", "goal", "at_least", "auto_bin_max", "detect_overlap", "cross_num_print_missing"}
_TYPE_OPTIONS = {"comment", "weight", "goal", "merge_instances"}
_POINT_OPTIONS = {"comment", "weight", "goal", "at_least", "auto_bin_max", "detect_overlap"}


def _error(message: str) -> CoverageDeclarationError:
    return CoverageDeclarationError("SVT-COV-SYNTAX", message)


def _name(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _annotation(node: ast.arg) -> str:
    if node.annotation is None:
        raise _error(f"coverage formal {node.arg!r} requires a type annotation")
    return ast.unparse(node.annotation)


def _annotation_base(node: ast.arg) -> str | None:
    annotation = node.annotation
    return annotation.value.id if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name) else None


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
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "repeat":
        if len(node.args) != 3 or node.keywords:
            raise _error("repeat() requires term, minimum, maximum")
        minimum, maximum = node.args[1:]
        if not all(isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool) for value in (minimum, maximum)):
            raise _error("repeat() bounds must be declaration-time integers")
        if minimum.value < 0 or maximum.value < minimum.value:
            raise _error("repeat() requires 0 <= minimum <= maximum")
        return {"kind": "repeat", "term": _slice_selector(node.args[0]), "minimum": minimum.value, "maximum": maximum.value}
    return _expr(node)


def _contains_repeat(selector: Any) -> bool:
    if isinstance(selector, dict):
        return selector.get("kind") == "repeat" or any(_contains_repeat(value) for value in selector.values())
    if isinstance(selector, list):
        return any(_contains_repeat(value) for value in selector)
    return False


def _validate_expression_names(expression: Any, allowed_names: set[str], owner: str) -> None:
    if isinstance(expression, dict):
        if expression.get("kind") == "name" and expression["name"] not in allowed_names:
            raise _error(f"coverage {owner} references unknown name {expression['name']!r}")
        for value in expression.values():
            _validate_expression_names(value, allowed_names, owner)
    elif isinstance(expression, (list, tuple)):
        for value in expression:
            _validate_expression_names(value, allowed_names, owner)


def _split_values(node: ast.AST) -> list[int]:
    """Enumerate the finite 2-state selector accepted by array bins."""
    if isinstance(node, ast.Tuple):
        return [value for item in node.elts for value in _split_values(item)]
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return [node.value]
    if isinstance(node, ast.Slice):
        if not all(isinstance(item, ast.Constant) and isinstance(item.value, int) and not isinstance(item.value, bool) for item in (node.lower, node.upper)):
            raise _error("bins.split() ranges require integer endpoints")
        if node.lower.value > node.upper.value:
            raise _error("bins.split() range lower endpoint exceeds upper endpoint")
        return list(range(node.lower.value, node.upper.value + 1))
    raise _error("bins.split() requires finite integer values or ranges")


def _value_selector(values: list[int]) -> Any:
    return {"kind": "values", "items": [{"kind": "constant", "value": value} for value in values]}


def _fixed_split_bins(name: str, values: list[int], count: int) -> list[CoverageBinIR]:
    width, remainder = divmod(len(values), count)
    result: list[CoverageBinIR] = []
    offset = 0
    for index in range(count):
        take = width + (remainder if index == count - 1 else 0)
        result.append(CoverageBinIR(f"{name}[{index}]", "normal", _value_selector(values[offset:offset + take])))
        offset += take
    return result


def _array_bin_declaration(name: str, call: ast.Call) -> list[CoverageBinIR]:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "split" or not isinstance(call.func.value, ast.Subscript):
        raise _error(f"coverage bin {name!r} has unsupported array-bin declaration")
    values = _split_values(call.func.value.slice)
    if len(set(values)) != len(values):
        raise _error(f"coverage array bin {name!r} contains duplicate values")
    if len(call.args) > 1 or any(keyword.arg != "max_bins" for keyword in call.keywords):
        raise _error("bins.split() accepts one positional count or max_bins= only")
    if call.args and call.keywords:
        raise _error("bins.split() cannot combine count and max_bins")
    if call.args:
        count_node = call.args[0]
        if not isinstance(count_node, ast.Constant) or not isinstance(count_node.value, int) or isinstance(count_node.value, bool) or count_node.value <= 0:
            raise _error("bins.split(count) requires a positive declaration-time integer")
        return _fixed_split_bins(name, values, count_node.value)
    maximum: int | None = 64
    if call.keywords:
        value = call.keywords[0].value
        if isinstance(value, ast.Constant) and value.value is None:
            maximum = None
        elif isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool) and value.value > 0:
            maximum = value.value
        else:
            raise _error("bins.split(max_bins=...) requires a positive integer or None")
    if maximum is not None and len(values) > maximum:
        return _fixed_split_bins(name, values, maximum)
    return [CoverageBinIR(f"{name}[{value}]", "normal", {"kind": "constant", "value": value}) for value in values]


def _option_values(node: ast.ClassDef, owner: str, base: str, allowed: set[str]) -> tuple[tuple[str, Any], ...]:
    if len(node.bases) != 1 or _name(node.bases[0]) != base:
        raise _error(f"coverage {owner} option must inherit {base}")
    values: list[tuple[str, Any]] = []
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise _error(f"coverage {owner} option has unsupported body statement")
        if not isinstance(statement.value, ast.Constant) or not (
            isinstance(statement.value.value, (bool, int, str))
            or (statement.targets[0].id == "name" and statement.value.value is None)
        ):
            raise _error(f"coverage {owner} option {statement.targets[0].id!r} must be a declaration-time constant")
        name, value = statement.targets[0].id, statement.value.value
        if name not in allowed:
            raise _error(f"coverage {owner} has unsupported option {name!r}")
        if name in {"weight", "goal", "at_least", "auto_bin_max", "cross_num_print_missing"} and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            raise _error(f"coverage {owner} option {name!r} must be a positive integer")
        if name in {"per_instance", "get_inst_coverage", "detect_overlap", "merge_instances"} and value not in {0, 1, False, True}:
            raise _error(f"coverage {owner} option {name!r} must be 0 or 1")
        values.append((name, value))
    return tuple(values)


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


def _automatic_bins_for_descriptor(
    descriptor: Any, point_name: str, auto_bin_max: int
) -> tuple[CoverageBinIR, ...]:
    if isinstance(descriptor, (Array, DynArray, Queue, AssocArray)):
        descriptor = descriptor._val_template if isinstance(descriptor, AssocArray) else descriptor._elem_template
    if isinstance(descriptor, Enum):
        return tuple(
            CoverageBinIR(f"auto[{member.value}]", "normal", {"kind": "constant", "value": member.value})
            for member in descriptor._enum_items
        )
    if not isinstance(descriptor, (Bit, Logic)):
        raise _error(f"coverage point {point_name!r} needs explicit bins because its automatic value domain is unknown")
    lower = -(1 << (descriptor.width - 1)) if descriptor.signed else 0
    upper = (1 << (descriptor.width - 1)) - 1 if descriptor.signed else (1 << descriptor.width) - 1
    if not isinstance(auto_bin_max, int) or isinstance(auto_bin_max, bool) or auto_bin_max <= 0:
        raise _error(f"coverage point {point_name!r} option auto_bin_max must be a positive integer")
    count = min(upper - lower + 1, auto_bin_max)
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


def _automatic_bins(
    owner: type[Any], point_name: str, source: ast.AST, auto_bin_max: int
) -> tuple[CoverageBinIR, ...]:
    return _automatic_bins_for_descriptor(_field_descriptor(owner, source), point_name, auto_bin_max)


def _point_class(owner: type[Any], node: ast.ClassDef, allowed_names: set[str]) -> tuple[CoveragePointIR, ...]:
    if len(node.bases) != 1 or _name(node.bases[0]) not in _POINT_BASES:
        raise _error(f"coverage declaration class {node.name!r} must inherit CovPoint or CovPointArray")
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
    if set(keywords).difference({"source", "iff", "length"}):
        raise _error(f"coverage point {node.name!r} has unsupported class keyword")
    if "source" not in keywords:
        raise _error(f"coverage point {node.name!r} requires source=")
    source_expression = _expr(keywords["source"])
    _validate_expression_names(source_expression, allowed_names, f"point {node.name!r}")
    iff_expression = _expr(keywords["iff"]) if "iff" in keywords else None
    if iff_expression is not None:
        _validate_expression_names(iff_expression, allowed_names, f"point {node.name!r} iff")
    bins: list[CoverageBinIR] = []
    options: list[tuple[str, Any]] = []
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "option":
            options.extend(_option_values(statement, f"point {node.name!r}", "CovPointOption", _POINT_OPTIONS))
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise _error(f"coverage point {node.name!r} has unsupported body statement")
        bin_name = statement.targets[0].id
        if isinstance(statement.value, ast.Name) and statement.value.id == "default_bins":
            bins.append(CoverageBinIR(bin_name, "default"))
            continue
        if isinstance(statement.value, ast.Call):
            bins.extend(_array_bin_declaration(bin_name, statement.value))
            continue
        if not isinstance(statement.value, ast.Subscript) or not isinstance(statement.value.value, ast.Name):
            raise _error(f"coverage point {node.name!r}.{bin_name} must use a bins declaration")
        kind = _BIN_BASES.get(statement.value.value.id)
        if kind is None:
            raise _error(f"coverage point {node.name!r}.{bin_name} has unsupported bin declaration")
        selector = _slice_selector(statement.value.slice)
        _validate_expression_names(selector, allowed_names, f"point {node.name!r} bin {bin_name!r}")
        if kind != "transition" and _contains_repeat(selector):
            raise _error(f"coverage point {node.name!r}.{bin_name} may use repeat() only in transition_bins")
        bins.append(CoverageBinIR(bin_name, kind, selector))
    base_name = _name(node.bases[0])
    descriptor = _field_descriptor(owner, keywords["source"])
    if not any(bin_.kind in {"normal", "default", "transition"} for bin_ in bins) and base_name == "CovPoint":
        auto_bin_max = dict(options).get("auto_bin_max", 64)
        bins.extend(_automatic_bins(owner, node.name, keywords["source"], auto_bin_max))
    if base_name == "CovPointArray":
        if "length" not in keywords:
            raise _error(f"coverage point array {node.name!r} requires length=")
        if not isinstance(keywords["length"], ast.Constant) or not isinstance(keywords["length"].value, int) or isinstance(keywords["length"].value, bool) or keywords["length"].value <= 0:
            raise _error(f"coverage point array {node.name!r} length must be a positive declaration-time integer")
        if not isinstance(descriptor, (Array, DynArray, Queue)):
            raise _error(f"coverage point array {node.name!r} source must be a direct array, dynamic array, or queue field")
        if any(bin_.kind == "transition" for bin_ in bins):
            raise _error(f"coverage point array {node.name!r} cannot declare transition bins")
        if not any(bin_.kind in {"normal", "default", "transition"} for bin_ in bins):
            bins.extend(_automatic_bins_for_descriptor(descriptor, node.name, dict(options).get("auto_bin_max", 64)))
        length = keywords["length"].value
        points: list[CoveragePointIR] = []
        for index in range(length):
            expression = {
                "kind": "subscript", "base": source_expression,
                "index": {"kind": "constant", "value": index},
            }
            points.append(CoveragePointIR(f"{node.name}[{index}]", expression, tuple(bins), iff_expression, tuple(options)))
        return tuple(points)
    elif "length" in keywords:
        raise _error(f"coverage point {node.name!r} cannot specify length=")
    if isinstance(descriptor, (DynArray, Queue, AssocArray)):
        if any(bin_.kind == "transition" for bin_ in bins):
            raise _error(f"container value-domain point {node.name!r} cannot declare transition bins")
        options.append(("container_value_domain", True))
    return (CoveragePointIR(
        node.name,
        source_expression,
        tuple(bins),
        iff_expression,
        tuple(options),
    ),)


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
    constructor: list[SampleParameterIR] = []
    references: list[SampleParameterIR] = []
    for argument in arguments[1:]:
        marker = _annotation_base(argument)
        if marker == "CoverInput":
            constructor.append(SampleParameterIR(argument.arg, _annotation(argument)))
        elif marker == "CoverRef":
            descriptor = next((value for base in declaration.owner.mro() if (value := base.__dict__.get(argument.arg)) is not None), None)
            if not isinstance(descriptor, TypeBase) or isinstance(descriptor, (Array, AssocArray, DynArray, Queue)):
                raise _error(f"coverage ref {argument.arg!r} must bind a static singular TypeBase host field")
            references.append(SampleParameterIR(argument.arg, _annotation(argument)))
        else:
            raise _error(f"coverage formal {argument.arg!r} must be CoverInput[...] or CoverRef[...]")
    constructor_parameters = tuple(constructor)
    sample: ast.FunctionDef | None = None
    group_options: tuple[tuple[str, Any], ...] = ()
    type_options: tuple[tuple[str, Any], ...] = ()
    point_nodes: list[ast.ClassDef] = []
    for statement in function.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.FunctionDef) and statement.name == "sample":
            if sample is not None:
                raise _error(f"coverage declaration {declaration.qualified_name} defines sample twice")
            sample = statement
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "option":
            group_options = _option_values(statement, f"covergroup {declaration.qualified_name}", "CoverGroupOption", _GROUP_OPTIONS)
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "type_option":
            type_options = _option_values(statement, f"covergroup type {declaration.qualified_name}", "CoverGroupTypeOption", _TYPE_OPTIONS)
            continue
        if isinstance(statement, ast.ClassDef):
            point_nodes.append(statement)
            continue
        raise _error(f"coverage declaration {declaration.qualified_name} has unsupported body statement")
    sample_parameters: tuple[SampleParameterIR, ...] = ()
    if sample is not None:
        if point_nodes:
            raise _error(f"coverage declaration {declaration.qualified_name} mixes outer and sample point declarations")
        if sample.decorator_list or sample.args.defaults or sample.args.kw_defaults or sample.args.vararg or sample.args.kwarg or sample.args.kwonlyargs:
            raise _error(f"coverage sample {declaration.qualified_name}.sample has unsupported signature")
        if sample.returns is not None and not (
            isinstance(sample.returns, ast.Constant) and sample.returns.value is None
        ):
            raise _error(f"coverage sample {declaration.qualified_name}.sample must return None")
        if sample.body and any(not isinstance(item, ast.ClassDef) for item in sample.body):
            raise _error(f"coverage sample {declaration.qualified_name}.sample has unsupported body statement")
        point_nodes = [item for item in sample.body if isinstance(item, ast.ClassDef)]
        if any(argument.arg == "case_id" for argument in sample.args.args):
            raise _error(f"coverage sample {declaration.qualified_name}.sample reserves case_id")
        sample_parameters = tuple(SampleParameterIR(argument.arg, _annotation(argument)) for argument in sample.args.args)
    allowed_names = {"self"}
    allowed_names.update(item.name for item in constructor_parameters)
    allowed_names.update(item.name for item in references)
    allowed_names.update(item.name for item in sample_parameters)
    points = tuple(point for node in point_nodes for point in _point_class(declaration.owner, node, allowed_names))
    sample_type = getattr(declaration.owner, "_svtypes_unified_type_name", declaration.owner.__name__)
    return CoverageIR(
        sample_type=sample_type,
        declaration_name=declaration.name,
        constructor_parameters=constructor_parameters,
        reference_parameters=tuple(references),
        sample_parameters=sample_parameters,
        points=points,
        options=group_options,
        type_options=type_options,
    )
