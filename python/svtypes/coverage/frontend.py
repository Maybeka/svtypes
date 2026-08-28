"""Restricted, source-only compiler for embedded coverage declarations."""

from __future__ import annotations

import ast
import inspect
import textwrap
from itertools import product
from typing import Any

from ..bit import Bit
from ..collection import Array, AssocArray, DynArray, Queue
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic
from ..base import TypeBase
from .ir import (
    CoverageBinIR,
    CoverageCrossIR,
    CoverageIR,
    CoveragePointIR,
    CrossQueueFunctionIR,
    SampleParameterIR,
)
from .limits import MAX_CROSS_MEMBERS, validate_cross_normal_bin_count


_BIN_BASES = {
    "bins": "normal",
    "ignore_bins": "ignore",
    "illegal_bins": "illegal",
    "transition_bins": "transition",
}
_POINT_BASES = {"CovPoint", "CovPointArray"}
_CROSS_BASE = "Cross"
_GROUP_OPTIONS = {"name", "comment", "per_instance", "get_inst_coverage", "weight", "goal", "at_least", "auto_bin_max", "detect_overlap", "cross_num_print_missing"}
_TYPE_OPTIONS = {"comment", "weight", "goal", "merge_instances"}
_POINT_OPTIONS = {"comment", "weight", "goal", "at_least", "auto_bin_max", "detect_overlap"}
_CROSS_OPTIONS = {"comment", "weight", "goal", "at_least", "cross_num_print_missing", "cross_retain_auto_bins"}


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
        if name in {"per_instance", "get_inst_coverage", "detect_overlap", "merge_instances", "cross_retain_auto_bins"} and value not in {0, 1, False, True}:
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


def _cross_member_reference(node: ast.AST, point_groups: dict[str, tuple[str, ...]]) -> str:
    """Resolve one source-only ``Cross.members`` point reference."""
    if isinstance(node, ast.Name):
        names = point_groups.get(node.id)
        if names is None:
            raise _error(f"coverage cross member references unknown point {node.id!r}")
        if len(names) != 1:
            raise _error(f"coverage cross member {node.id!r} is an array base; select one slot")
        return names[0]
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
        and not isinstance(node.slice.value, bool)
    ):
        names = point_groups.get(node.value.id)
        index = node.slice.value
        if names is None:
            raise _error(f"coverage cross member references unknown point {node.value.id!r}")
        if index < 0 or index >= len(names):
            raise _error(f"coverage cross member {node.value.id!r}[{index}] is outside its declared slots")
        if len(names) == 1:
            raise _error(f"coverage cross member {node.value.id!r} is not an array point")
        return names[index]
    raise _error("coverage cross members must be point names or fixed array-point slots")


def _cross_bin_reference(node: ast.AST, members: tuple[str, ...], point_groups: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Resolve ``point.bin`` (or ``array[index].bin``) inside a cross bin."""
    if not isinstance(node, ast.Attribute):
        raise _error("coverage cross bin selector must use member_point.member_bin")
    point = _cross_member_reference(node.value, point_groups)
    if point not in members:
        raise _error(f"coverage cross bin selector {point!r} is not in members order")
    return {"point": point, "bin": node.attr}


def _queue_statement_list(
    statements: list[ast.stmt], allowed_names: set[str], locals_: set[str], queues: set[str]
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for statement in statements:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target = statement.targets[0].id
            if isinstance(statement.value, ast.Call) and isinstance(statement.value.func, ast.Name) and statement.value.func.id == "CrossQueueType" and not statement.value.args and not statement.value.keywords:
                result.append({"kind": "queue_new", "target": target})
                queues.add(target)
            else:
                queues.discard(target)
                value = _expr(statement.value)
                _validate_expression_names(value, allowed_names | locals_, "cross queue function")
                result.append({"kind": "assign", "target": target, "value": value})
            locals_.add(target)
            continue
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "push_back"
            and isinstance(statement.value.func.value, ast.Name)
            and len(statement.value.args) == 1
            and not statement.value.keywords
            and isinstance(statement.value.args[0], ast.Tuple)
        ):
            target = statement.value.func.value.id
            if target not in queues:
                raise _error(f"cross queue function pushes to unknown queue {target!r}")
            items = [_expr(value) for value in statement.value.args[0].elts]
            for item in items:
                _validate_expression_names(item, allowed_names | locals_, "cross queue function")
            result.append({"kind": "push", "target": target, "items": items})
            continue
        if isinstance(statement, ast.For) and isinstance(statement.target, ast.Name) and isinstance(statement.iter, ast.Call) and isinstance(statement.iter.func, ast.Name) and statement.iter.func.id == "range" and not statement.iter.keywords and 1 <= len(statement.iter.args) <= 3:
            terms = [_expr(value) for value in statement.iter.args]
            for term in terms:
                _validate_expression_names(term, allowed_names | locals_, "cross queue function range")
            target = statement.target.id
            nested_locals = set(locals_)
            nested_locals.add(target)
            body = _queue_statement_list(statement.body, allowed_names, nested_locals, set(queues))
            result.append({"kind": "for", "target": target, "range": terms, "body": list(body)})
            continue
        if isinstance(statement, ast.If):
            condition = _expr(statement.test)
            _validate_expression_names(condition, allowed_names | locals_, "cross queue function condition")
            then = _queue_statement_list(statement.body, allowed_names, set(locals_), set(queues))
            otherwise = _queue_statement_list(statement.orelse, allowed_names, set(locals_), set(queues))
            result.append({"kind": "if", "condition": condition, "then": list(then), "else": list(otherwise)})
            continue
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Name) and statement.value.id in queues:
            result.append({"kind": "return", "value": statement.value.id})
            continue
        raise _error("cross queue function has an unsupported statement")
    return tuple(result)


def _cross_queue_function(node: ast.FunctionDef, allowed_names: set[str]) -> CrossQueueFunctionIR:
    if node.decorator_list or node.args.defaults or node.args.kw_defaults or node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
        raise _error(f"cross queue function {node.name!r} has unsupported signature")
    if not isinstance(node.returns, ast.Name) or node.returns.id != "CrossQueueType":
        raise _error(f"cross queue function {node.name!r} must return CrossQueueType")
    if any(argument.arg == "self" for argument in node.args.args):
        raise _error(f"cross queue function {node.name!r} cannot declare self")
    parameters = tuple(SampleParameterIR(argument.arg, _annotation(argument)) for argument in node.args.args)
    names = {parameter.name for parameter in parameters}
    body = _queue_statement_list(node.body, allowed_names | names, set(names), set())
    if not body or body[-1].get("kind") != "return":
        raise _error(f"cross queue function {node.name!r} must end with return result")
    return CrossQueueFunctionIR(node.name, parameters, body)


def _cross_selector(
    node: ast.AST,
    members: tuple[str, ...],
    point_groups: dict[str, tuple[str, ...]],
    queue_functions: dict[str, CrossQueueFunctionIR],
    allowed_names: set[str],
) -> dict[str, Any]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in queue_functions:
        function = queue_functions[node.func.id]
        if node.keywords or len(node.args) != len(function.parameters):
            raise _error(f"cross queue function {function.name!r} has invalid arguments")
        args = [_expr(value) for value in node.args]
        for argument in args:
            _validate_expression_names(argument, allowed_names, f"cross queue function {function.name!r} arguments")
        return {"kind": "cross_queue_call", "function": function.name, "args": args}
    values = node.elts if isinstance(node, ast.Tuple) else (node,)
    if len(values) != len(members):
        raise _error(f"coverage cross selector has {len(values)} members; expected {len(members)}")
    refs = tuple(_cross_bin_reference(value, members, point_groups) for value in values)
    if tuple(reference["point"] for reference in refs) != members:
        raise _error("coverage cross bin selector must follow the declared members order")
    return {"kind": "cross_bin_refs", "items": list(refs)}


def _cross_candidate_bins(point: CoveragePointIR) -> tuple[CoverageBinIR, ...]:
    return tuple(item for item in point.bins if item.kind in {"normal", "default"})


def _cross_class(
    node: ast.ClassDef,
    points_by_name: dict[str, CoveragePointIR],
    point_groups: dict[str, tuple[str, ...]],
    *,
    allowed_names: set[str],
    existing_normal_bins: int,
) -> tuple[CoverageCrossIR, int]:
    if len(node.bases) != 1 or _name(node.bases[0]) != _CROSS_BASE:
        raise _error(f"coverage declaration class {node.name!r} must inherit Cross")
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
    if set(keywords).difference({"members", "iff"}) or "members" not in keywords:
        raise _error(f"coverage cross {node.name!r} requires members= and supports only iff=")
    if not isinstance(keywords["members"], (ast.Tuple, ast.List)):
        raise _error(f"coverage cross {node.name!r} members must be a declaration-time tuple")
    members = tuple(_cross_member_reference(value, point_groups) for value in keywords["members"].elts)
    if not members:
        raise _error(f"coverage cross {node.name!r} must have at least one member")
    if len(members) > MAX_CROSS_MEMBERS:
        raise CoverageDeclarationError(
            "SVT-COV-CROSS-LIMIT",
            f"cross {node.name!r} has {len(members)} members; limit is {MAX_CROSS_MEMBERS}",
        )
    if len(set(members)) != len(members):
        raise _error(f"coverage cross {node.name!r} has duplicate members")
    iff_expression = _expr(keywords["iff"]) if "iff" in keywords else None
    if iff_expression is not None:
        _validate_expression_names(iff_expression, allowed_names, f"cross {node.name!r} iff")
    options: list[tuple[str, Any]] = []
    declared: list[CoverageBinIR] = []
    function_nodes: list[ast.FunctionDef] = []
    assignment_nodes: list[ast.Assign] = []
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "option":
            options.extend(_option_values(statement, f"cross {node.name!r}", "CrossOption", _CROSS_OPTIONS))
            continue
        if isinstance(statement, ast.FunctionDef):
            function_nodes.append(statement)
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise _error(f"coverage cross {node.name!r} has unsupported body statement")
        assignment_nodes.append(statement)
    queue_functions = tuple(_cross_queue_function(function, allowed_names - {"self"}) for function in function_nodes)
    queue_function_map = {function.name: function for function in queue_functions}
    if len(queue_function_map) != len(queue_functions):
        raise _error(f"coverage cross {node.name!r} has duplicate queue function names")
    for statement in assignment_nodes:
        bin_name = statement.targets[0].id
        if not isinstance(statement.value, ast.Subscript) or not isinstance(statement.value.value, ast.Name):
            raise _error(f"coverage cross {node.name!r}.{bin_name} must use bins or ignore_bins")
        kind = _BIN_BASES.get(statement.value.value.id)
        if kind not in {"normal", "ignore"}:
            raise _error(f"coverage cross {node.name!r}.{bin_name} supports only bins or ignore_bins")
        declared.append(CoverageBinIR(bin_name, kind, _cross_selector(
            statement.value.slice, members, point_groups, queue_function_map, allowed_names
        )))

    member_points = tuple(points_by_name[member] for member in members)
    for point in member_points:
        if dict(point.options).get("container_value_domain"):
            raise _error(f"coverage cross {node.name!r} cannot use container value-domain point {point.name!r}")
        if any(bin_.kind == "transition" for bin_ in point.bins):
            raise _error(f"coverage cross {node.name!r} cannot use transition point {point.name!r}")
        if not _cross_candidate_bins(point):
            raise _error(f"coverage cross {node.name!r} member {point.name!r} has no normal or default bins")
    for bin_ in declared:
        if bin_.selector["kind"] == "cross_queue_call":
            continue
        for reference in bin_.selector["items"]:
            point = points_by_name[reference["point"]]
            matched = next((item for item in point.bins if item.name == reference["bin"]), None)
            if matched is None:
                raise _error(f"coverage cross {node.name!r} references unknown bin {reference['point']}.{reference['bin']}")
            if matched.kind not in {"normal", "default"}:
                raise _error(f"coverage cross {node.name!r} may reference only normal or default point bins")

    normal_declared = [item for item in declared if item.kind == "normal"]
    ignore_declared = [item for item in declared if item.kind == "ignore"]
    static_declared = [item for item in declared if item.selector["kind"] == "cross_bin_refs"]
    if len({tuple((ref["point"], ref["bin"]) for ref in item.selector["items"]) for item in static_declared}) != len(static_declared):
        raise _error(f"coverage cross {node.name!r} has duplicate bin selectors")
    candidate_refs = list(product(*[_cross_candidate_bins(point) for point in member_points]))
    candidate_selectors = {
        tuple((member, bin_.name) for member, bin_ in zip(members, candidate))
        for candidate in candidate_refs
    }
    declared_selectors = {
        tuple((ref["point"], ref["bin"]) for ref in item.selector["items"])
        for item in static_declared
    }
    if not declared_selectors.issubset(candidate_selectors):
        raise _error(f"coverage cross {node.name!r} selector is outside its member bin universe")
    ignored_selectors = {
        tuple((ref["point"], ref["bin"]) for ref in item.selector["items"])
        for item in ignore_declared if item.selector["kind"] == "cross_bin_refs"
    }
    normal_selectors = {
        tuple((ref["point"], ref["bin"]) for ref in item.selector["items"])
        for item in normal_declared if item.selector["kind"] == "cross_bin_refs"
    }
    retain_auto = int(dict(options).get("cross_retain_auto_bins", 0))
    effective = list(normal_declared)
    if not normal_declared or retain_auto:
        for selector in sorted(candidate_selectors - normal_selectors - ignored_selectors):
            name = "auto[" + ",".join(f"{point}.{bin_name}" for point, bin_name in selector) + "]"
            effective.append(CoverageBinIR(name, "normal", {"kind": "cross_bin_refs", "items": [{"point": point, "bin": bin_name} for point, bin_name in selector]}))
    # Ignore selectors remain in the IR so runtime classification can skip
    # normal tuples before incrementing any cross bin.
    effective.extend(ignore_declared)
    total = validate_cross_normal_bin_count(node.name, len([item for item in effective if item.kind == "normal"]), existing_covergroup_normal_bins=existing_normal_bins)
    return CoverageCrossIR(node.name, members, tuple(effective), iff_expression, tuple(options), queue_functions), total


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
    declaration_nodes: list[ast.ClassDef] = []
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
            declaration_nodes.append(statement)
            continue
        raise _error(f"coverage declaration {declaration.qualified_name} has unsupported body statement")
    sample_parameters: tuple[SampleParameterIR, ...] = ()
    if sample is not None:
        if declaration_nodes:
            raise _error(f"coverage declaration {declaration.qualified_name} mixes outer and sample point declarations")
        if sample.decorator_list or sample.args.defaults or sample.args.kw_defaults or sample.args.vararg or sample.args.kwarg or sample.args.kwonlyargs:
            raise _error(f"coverage sample {declaration.qualified_name}.sample has unsupported signature")
        if sample.returns is not None and not (
            isinstance(sample.returns, ast.Constant) and sample.returns.value is None
        ):
            raise _error(f"coverage sample {declaration.qualified_name}.sample must return None")
        if sample.body and any(not isinstance(item, ast.ClassDef) for item in sample.body):
            raise _error(f"coverage sample {declaration.qualified_name}.sample has unsupported body statement")
        declaration_nodes = [item for item in sample.body if isinstance(item, ast.ClassDef)]
        if any(argument.arg == "case_id" for argument in sample.args.args):
            raise _error(f"coverage sample {declaration.qualified_name}.sample reserves case_id")
        sample_parameters = tuple(SampleParameterIR(argument.arg, _annotation(argument)) for argument in sample.args.args)
    allowed_names = {"self"}
    allowed_names.update(item.name for item in constructor_parameters)
    allowed_names.update(item.name for item in references)
    allowed_names.update(item.name for item in sample_parameters)
    point_nodes = [node for node in declaration_nodes if _name(node.bases[0]) in _POINT_BASES] if all(node.bases for node in declaration_nodes) else []
    if len(point_nodes) + sum(_name(node.bases[0]) == _CROSS_BASE for node in declaration_nodes if node.bases) != len(declaration_nodes):
        invalid = next(node for node in declaration_nodes if len(node.bases) != 1 or _name(node.bases[0]) not in _POINT_BASES | {_CROSS_BASE})
        raise _error(f"coverage declaration class {invalid.name!r} must inherit CovPoint, CovPointArray, or Cross")
    grouped_points = {
        node.name: _point_class(declaration.owner, node, allowed_names)
        for node in point_nodes
    }
    points = tuple(point for group in grouped_points.values() for point in group)
    points_by_name = {point.name: point for point in points}
    point_groups = {name: tuple(point.name for point in group) for name, group in grouped_points.items()}
    normal_cross_bins = 0
    crosses: list[CoverageCrossIR] = []
    for node in declaration_nodes:
        if _name(node.bases[0]) != _CROSS_BASE:
            continue
        cross, normal_cross_bins = _cross_class(
            node,
            points_by_name,
            point_groups,
            allowed_names=allowed_names,
            existing_normal_bins=normal_cross_bins,
        )
        crosses.append(cross)
    sample_type = getattr(declaration.owner, "_svtypes_unified_type_name", declaration.owner.__name__)
    return CoverageIR(
        sample_type=sample_type,
        declaration_name=declaration.name,
        constructor_parameters=constructor_parameters,
        reference_parameters=tuple(references),
        sample_parameters=sample_parameters,
        points=points,
        crosses=tuple(crosses),
        options=group_options,
        type_options=type_options,
    )
