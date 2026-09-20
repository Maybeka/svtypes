"""Restricted, source-only compiler for embedded coverage declarations."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from itertools import product
from typing import Any

from ..bit import Bit
from ..collection import Array, AssocArray, DynArray, Queue
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic, LogicValue
from ..parameter import Parameter
from ..base import TypeBase
from .ir import (
    CoverageBinIR,
    CrossMemberViewIR,
    CoverageInitCallIR,
    CoverageInitIR,
    CoverageCrossIR,
    CoverageIR,
    CoveragePointIR,
    CrossQueueFunctionIR,
    SampleParameterIR,
)
from .limits import MAX_CROSS_MEMBERS, MAX_TRANSITION_SEQUENCE, validate_cross_normal_bin_count


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
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"Bit", "Logic"}:
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, int) or isinstance(node.args[0].value, bool) or node.args[0].value <= 0:
            raise _error(f"{node.func.id} coverage literal requires an integer width")
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
        if set(keywords).difference({"value", "signed"}) or "value" not in keywords:
            raise _error(f"{node.func.id} coverage literal requires value= and optionally signed=")
        if not isinstance(keywords["value"], ast.Constant) or not isinstance(keywords["value"].value, (int, str)):
            raise _error(f"{node.func.id} coverage literal value must be an integer or string")
        signed = False
        if "signed" in keywords:
            if not isinstance(keywords["signed"], ast.Constant) or not isinstance(keywords["signed"].value, bool):
                raise _error(f"{node.func.id} coverage literal signed must be a boolean")
            signed = keywords["signed"].value
        return {"kind": "typed_literal", "type": node.func.id, "width": node.args[0].value,
                "signed": signed, "value": keywords["value"].value}
    return _expr(node)


_SV_LITERAL = re.compile(r"^(?:(?P<width>[0-9]+)'(?P<signed>[sS]?)(?P<base>[bBoOdDhH]))(?P<digits>[0-9a-fA-F_xXzZ?+-]+)$")


def _domain_descriptor(descriptor: Any) -> tuple[int, bool, bool] | None:
    if isinstance(descriptor, (Array, DynArray, Queue)):
        return _domain_descriptor(descriptor._elem_template)
    if isinstance(descriptor, AssocArray):
        return _domain_descriptor(descriptor._val_template)
    if isinstance(descriptor, Enum):
        return descriptor.width, bool(descriptor.signed), False
    if isinstance(descriptor, Bit):
        return descriptor.width, bool(descriptor.signed), False
    if isinstance(descriptor, Logic):
        return descriptor.width, bool(descriptor.signed), True
    return None


def _scalar_descriptor(descriptor: Any) -> Any | None:
    """Return the final scalar descriptor represented by a coverage expression."""
    if isinstance(descriptor, (Array, DynArray, Queue)):
        return _scalar_descriptor(descriptor._elem_template)
    if isinstance(descriptor, AssocArray):
        return _scalar_descriptor(descriptor._val_template)
    return descriptor


def _comparison_domain_record(
    domain: tuple[int, bool, bool], descriptor: Any | None
) -> dict[str, Any]:
    """Describe the covpoint expression's final comparison type in the IR."""
    width, signed, four_state = domain
    scalar = _scalar_descriptor(descriptor)
    result: dict[str, Any] = {
        "width": width,
        "signed": signed,
        "four_state": four_state,
        "kind": "logic" if four_state else "bit",
    }
    if isinstance(scalar, Enum):
        result["kind"] = "enum"
        result["type_name"] = type(scalar).__name__
    return result


def _expression_domain(
    owner: type[Any], node: ast.AST, named_domains: dict[str, tuple[int, bool, bool]] | None = None
) -> tuple[int, bool, bool] | None:
    """Infer the supported integral result domain without using Python arithmetic."""
    descriptor = _field_descriptor(owner, node)
    domain = _domain_descriptor(descriptor)
    if domain is not None:
        return domain
    if isinstance(node, ast.Name):
        field_domain = _domain_descriptor(next((base.__dict__[node.id] for base in owner.mro() if node.id in base.__dict__), None))
        return field_domain if field_domain is not None else (named_domains or {}).get(node.id)
    if isinstance(node, ast.UnaryOp):
        return _expression_domain(owner, node.operand, named_domains)
    if isinstance(node, ast.BinOp):
        left, right = _expression_domain(owner, node.left, named_domains), _expression_domain(owner, node.right, named_domains)
        # Python numeric literals do not carry SV width/signedness.  A mixed
        # expression therefore needs an explicitly typed peer before it can
        # define a coverage comparison domain.
        if left is None or right is None:
            return None
        return left if left == right else None
    if isinstance(node, (ast.Compare, ast.BoolOp)):
        return (1, False, False)
    return None


def _contains_literal(selector: Any) -> bool:
    if isinstance(selector, dict):
        return selector.get("kind") in {"constant", "typed_literal", "enum_literal"} or any(_contains_literal(value) for value in selector.values())
    if isinstance(selector, (list, tuple)):
        return any(_contains_literal(item) for item in selector)
    return False


def _annotation_domain(annotation: ast.AST | None, static_ns: dict[str, Any]) -> tuple[int, bool, bool] | None:
    if isinstance(annotation, ast.Name):
        value = static_ns.get(annotation.id)
        if value is Bit:
            return (1, False, False)
        if value is Logic:
            return (1, False, True)
        if isinstance(value, type) and issubclass(value, Enum):
            return (value._width, bool(value._signed), False)
    return None


def _integer_in_domain(value: int, width: int, signed: bool, point_name: str) -> int:
    lower = -(1 << (width - 1)) if signed else 0
    upper = (1 << (width - 1)) - 1 if signed else (1 << width) - 1
    if value < lower or value > upper:
        raise _error(f"coverage point {point_name!r} literal {value!r} cannot be losslessly cast to {'signed' if signed else 'unsigned'} {width}-bit source domain")
    return value


def _parse_sv_literal(text: str, point_name: str) -> tuple[int, bool, bool, int | LogicValue]:
    match = _SV_LITERAL.fullmatch(text.replace(" ", ""))
    if match is None:
        # Compatibility spelling for an inferred-width binary four-state value.
        if text and all(character in "01xXzZ?_" for character in text):
            value = LogicValue.from_string(text)
            return value.width, False, True, value
        raise _error(f"coverage point {point_name!r} has invalid SV numeric literal {text!r}")
    width = int(match.group("width"))
    signed = bool(match.group("signed"))
    base, digits = match.group("base").lower(), match.group("digits").replace("_", "")
    if width <= 0:
        raise _error(f"coverage point {point_name!r} SV literal width must be positive")
    if any(character in "xXzZ?" for character in digits):
        if base != "b" or digits.startswith(("+", "-")):
            raise _error(f"coverage point {point_name!r} only binary SV literals may contain X/Z")
        value = LogicValue.from_string(digits)
        if value.width != width:
            raise _error(f"coverage point {point_name!r} SV literal digit width disagrees with its declared width")
        return width, signed, True, value
    try:
        value = int(digits, {"b": 2, "o": 8, "d": 10, "h": 16}[base])
    except ValueError as error:
        raise _error(f"coverage point {point_name!r} has invalid SV numeric literal {text!r}") from error
    if signed and value >= 0 and value & (1 << (width - 1)):
        value -= 1 << width
    return width, signed, False, _integer_in_domain(value, width, signed, point_name)


def _normalize_literal(
    selector: Any,
    domain: tuple[int, bool, bool],
    point_name: str,
    *,
    enum_type_name: str | None = None,
) -> Any:
    if not isinstance(selector, dict):
        return selector
    kind = selector.get("kind")
    if kind == "values":
        return {**selector, "items": [_normalize_literal(item, domain, point_name, enum_type_name=enum_type_name) for item in selector["items"]]}
    if kind == "range":
        lower = _normalize_literal(selector["lower"], domain, point_name, enum_type_name=enum_type_name) if selector["lower"] is not None else None
        upper = _normalize_literal(selector["upper"], domain, point_name, enum_type_name=enum_type_name) if selector["upper"] is not None else None
        if any(
            isinstance(endpoint, dict)
            and isinstance(endpoint.get("value"), LogicValue)
            and (endpoint["value"].x_mask or endpoint["value"].z_mask)
            for endpoint in (lower, upper)
        ):
            raise _error(f"coverage point {point_name!r} range endpoints cannot contain X/Z")
        return {**selector, "lower": lower, "upper": upper}
    if kind == "repeat":
        return {**selector, "term": _normalize_literal(selector["term"], domain, point_name, enum_type_name=enum_type_name)}
    if kind == "array_split":
        return {**selector, "selector": _normalize_literal(selector["selector"], domain, point_name, enum_type_name=enum_type_name)}
    if kind not in {"constant", "typed_literal", "enum_literal"}:
        return selector
    width, signed, four_state = domain
    raw = selector["value"]
    if kind == "typed_literal":
        literal_width, literal_signed, literal_four_state = selector["width"], bool(selector["signed"]), selector["type"] == "Logic"
        if literal_four_state:
            try:
                literal_value = Logic(literal_width, value=raw, signed=literal_signed).value
            except (TypeError, ValueError) as error:
                raise _error(f"coverage point {point_name!r} has invalid Logic literal: {error}") from error
        else:
            if isinstance(raw, str):
                try:
                    raw = int(raw, 0)
                except ValueError as error:
                    raise _error(f"coverage point {point_name!r} has invalid Bit literal {raw!r}") from error
            if not isinstance(raw, int):
                raise _error(f"coverage point {point_name!r} Bit literal must be integral")
            literal_value = _integer_in_domain(raw, literal_width, literal_signed, point_name)
    elif kind == "enum_literal":
        literal_width, literal_signed, literal_four_state, literal_value = width, signed, False, raw
    elif isinstance(raw, str):
        literal_width, literal_signed, literal_four_state, literal_value = _parse_sv_literal(raw, point_name)
    else:
        literal_width, literal_signed, literal_four_state, literal_value = width, signed, False, raw
    if not isinstance(literal_value, (int, LogicValue)):
        return selector
    if isinstance(literal_value, int):
        literal_value = _integer_in_domain(literal_value, literal_width, literal_signed, point_name)
    if literal_four_state and not four_state:
        raise _error(f"coverage point {point_name!r} cannot cast a four-state literal to a two-state source domain")
    if isinstance(literal_value, LogicValue):
        if literal_value.width > width:
            raise _error(f"coverage point {point_name!r} four-state literal width cannot be losslessly cast to {width} bits")
        extension = width - literal_value.width
        if extension and literal_signed:
            sign_bit = 1 << (literal_value.width - 1)
            sign_value = bool(literal_value.value_mask & sign_bit)
            sign_x = bool(literal_value.x_mask & sign_bit)
            sign_z = bool(literal_value.z_mask & sign_bit)
            mask = ((1 << extension) - 1) << literal_value.width
            literal_value = LogicValue(
                width,
                literal_value.value_mask | (mask if sign_value else 0),
                literal_value.x_mask | (mask if sign_x else 0),
                literal_value.z_mask | (mask if sign_z else 0),
            )
        else:
            literal_value = LogicValue(width, literal_value.value_mask, literal_value.x_mask, literal_value.z_mask)
        return {"kind": "constant", "value": literal_value}
    normalized = _integer_in_domain(literal_value, width, signed, point_name)
    if kind == "enum_literal" and selector["type_name"] == enum_type_name:
        return {**selector, "value": normalized}
    return {"kind": "constant", "value": normalized}


def _contains_repeat(selector: Any) -> bool:
    if isinstance(selector, dict):
        return selector.get("kind") == "repeat" or any(_contains_repeat(value) for value in selector.values())
    if isinstance(selector, list):
        return any(_contains_repeat(value) for value in selector)
    return False


def _transition_sequence_lengths(selector: Any) -> tuple[int, int]:
    """Return the shortest and longest sequence represented by a transition."""
    if not isinstance(selector, dict) or selector.get("kind") != "values":
        raise _error("transition bin selector must be a finite value sequence")
    minimum = maximum = 0
    for item in selector["items"]:
        if isinstance(item, dict) and item.get("kind") == "repeat":
            minimum += item["minimum"]
            maximum += item["maximum"]
        else:
            minimum += 1
            maximum += 1
    return minimum, maximum


def _validate_point_bin_shapes(point_name: str, bins: list[CoverageBinIR]) -> None:
    if sum(item.kind == "default" for item in bins) > 1:
        raise _error(f"coverage point {point_name!r} may declare at most one default bin")
    for bin_ in bins:
        if bin_.kind != "transition":
            continue
        minimum, maximum = _transition_sequence_lengths(bin_.selector)
        if minimum < 2 or maximum > MAX_TRANSITION_SEQUENCE:
            raise _error(
                f"coverage transition bin {point_name!r}.{bin_.name} must expand to sequences "
                f"of length 2..{MAX_TRANSITION_SEQUENCE}"
            )


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


def _split_selector(values: list[int]) -> Any:
    """Represent each split segment by its narrowest lossless selector."""
    if len(values) == 1:
        return {"kind": "constant", "value": values[0]}
    if values and all(value == values[0] + index for index, value in enumerate(values)):
        return {
            "kind": "range",
            "lower": {"kind": "constant", "value": values[0]},
            "upper": {"kind": "constant", "value": values[-1]},
        }
    return {"kind": "values", "items": [{"kind": "constant", "value": value} for value in values]}


def _fixed_split_bins(name: str, values: list[int], count: int) -> list[CoverageBinIR]:
    width, remainder = divmod(len(values), count)
    result: list[CoverageBinIR] = []
    offset = 0
    for index in range(count):
        take = width + (remainder if index == count - 1 else 0)
        result.append(CoverageBinIR(f"{name}[{index}]", "normal", _split_selector(values[offset:offset + take])))
        offset += take
    return result


def _array_bin_declaration(
    name: str, call: ast.Call, *, allowed_names: set[str], point_name: str
) -> list[CoverageBinIR]:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "split" or not isinstance(call.func.value, ast.Subscript):
        raise _error(f"coverage bin {name!r} has unsupported array-bin declaration")
    if len(call.args) > 1 or any(keyword.arg != "max_bins" for keyword in call.keywords):
        raise _error("bins.split() accepts one positional count or max_bins= only")
    if call.args and call.keywords:
        raise _error("bins.split() cannot combine count and max_bins")
    if call.args:
        count_node = call.args[0]
        if not isinstance(count_node, ast.Constant) or not isinstance(count_node.value, int) or isinstance(count_node.value, bool) or count_node.value <= 0:
            raise _error("bins.split(count) requires a positive declaration-time integer")
        selector = _slice_selector(call.func.value.slice)
        _validate_expression_names(selector, allowed_names, f"point {point_name!r} array bin {name!r}")
        try:
            values = _split_values(call.func.value.slice)
        except CoverageDeclarationError:
            if not isinstance(selector, dict) or selector.get("kind") != "range":
                raise _error("dynamic bins.split(count) requires one bounded integer range")
            return [CoverageBinIR(
                name,
                "normal",
                {"kind": "array_split", "selector": selector, "count": count_node.value},
            )]
        if len(set(values)) != len(values):
            raise _error(f"coverage array bin {name!r} contains duplicate values")
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
    if maximum is None:
        selector = _slice_selector(call.func.value.slice)
        _validate_expression_names(selector, allowed_names, f"point {point_name!r} array bin {name!r}")
        try:
            values = _split_values(call.func.value.slice)
        except CoverageDeclarationError:
            if not isinstance(selector, dict) or selector.get("kind") != "range":
                raise _error("dynamic bins.split(max_bins=None) requires one bounded integer range")
            return [CoverageBinIR(name, "normal", {"kind": "array_split", "selector": selector})]
        if len(set(values)) != len(values):
            raise _error(f"coverage array bin {name!r} contains duplicate values")
        return [CoverageBinIR(f"{name}[{value}]", "normal", {"kind": "constant", "value": value}) for value in values]
    values = _split_values(call.func.value.slice)
    if len(set(values)) != len(values):
        raise _error(f"coverage array bin {name!r} contains duplicate values")
    if len(values) > maximum:
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
        enum_type = type(descriptor).__name__
        return tuple(
            CoverageBinIR(
                f"auto[{member.name}]",
                "normal",
                {
                    "kind": "enum_literal",
                    "member": member.name,
                    "type_name": enum_type,
                    "value": int(member.value),
                },
            )
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


def _resolve_static_literals(selector: Any, static_ns: dict[str, Any], point_name: str, descriptor: Any) -> Any:
    """Fold declaration-time enum members into the point comparison domain."""
    if not isinstance(selector, dict):
        return selector
    kind = selector.get("kind")
    if kind == "values":
        return {**selector, "items": [_resolve_static_literals(item, static_ns, point_name, descriptor) for item in selector["items"]]}
    if kind == "range":
        return {
            **selector,
            "lower": _resolve_static_literals(selector["lower"], static_ns, point_name, descriptor) if selector["lower"] is not None else None,
            "upper": _resolve_static_literals(selector["upper"], static_ns, point_name, descriptor) if selector["upper"] is not None else None,
        }
    if kind == "repeat":
        return {**selector, "term": _resolve_static_literals(selector["term"], static_ns, point_name, descriptor)}
    if kind == "array_split":
        return {
            **selector,
            "selector": _resolve_static_literals(
                selector["selector"], static_ns, point_name, descriptor
            ),
        }
    if kind == "name":
        parameter = static_ns.get(selector["name"])
        if isinstance(parameter, Parameter):
            if parameter.is_type_parameter:
                raise _error(f"coverage point {point_name!r} cannot use type Parameter {selector['name']!r} as a bin value")
            return {"kind": "parameter_ref", "name": selector["name"], "type": parameter.dtype}
    if kind != "attribute":
        return selector
    base = selector.get("base")
    if not isinstance(base, dict) or base.get("kind") != "name":
        return selector
    owner = static_ns.get(base["name"])
    if owner is not None and base["name"] == "self":
        parameter = next(
            (base_type.__dict__[selector["name"]]
             for base_type in owner.mro()
             if selector["name"] in base_type.__dict__),
            None,
        )
        if isinstance(parameter, Parameter):
            if parameter.is_type_parameter:
                raise _error(
                    f"coverage point {point_name!r} cannot use type Parameter {selector['name']!r} as a bin value"
                )
            return {
                "kind": "parameter_ref",
                "name": selector["name"],
                "type": parameter.dtype,
            }
    if not (isinstance(owner, type) and issubclass(owner, Enum)):
        return selector
    member = getattr(owner, selector["name"], None)
    if not hasattr(member, "value"):
        raise _error(f"coverage point {point_name!r} references unknown enum member {base['name']}.{selector['name']}")
    final_descriptor = _scalar_descriptor(descriptor)
    if isinstance(final_descriptor, Enum) and type(final_descriptor) is not owner:
        raise _error(f"coverage point {point_name!r} cannot use a different enum type in its comparison domain")
    return {
        "kind": "enum_literal",
        "member": selector["name"],
        "type_name": type(final_descriptor).__name__ if isinstance(final_descriptor, Enum) else owner.__name__,
        "value": int(member.value),
    }


def _point_class(
    owner: type[Any], node: ast.ClassDef, allowed_names: set[str], static_ns: dict[str, Any],
    named_domains: dict[str, tuple[int, bool, bool]],
) -> tuple[CoveragePointIR, ...]:
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
    descriptor = _field_descriptor(owner, keywords["source"])
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
            bins.extend(
                CoverageBinIR(
                    bin_.name,
                    bin_.kind,
                    _resolve_static_literals(
                        bin_.selector, static_ns, node.name, descriptor
                    ),
                )
                for bin_ in _array_bin_declaration(
                    bin_name,
                    statement.value,
                    allowed_names=allowed_names,
                    point_name=node.name,
                )
            )
            continue
        if not isinstance(statement.value, ast.Subscript) or not isinstance(statement.value.value, ast.Name):
            raise _error(f"coverage point {node.name!r}.{bin_name} must use a bins declaration")
        kind = _BIN_BASES.get(statement.value.value.id)
        if kind is None:
            raise _error(f"coverage point {node.name!r}.{bin_name} has unsupported bin declaration")
        selector = _resolve_static_literals(_slice_selector(statement.value.slice), static_ns, node.name, descriptor)
        _validate_expression_names(selector, allowed_names, f"point {node.name!r} bin {bin_name!r}")
        if kind != "transition" and _contains_repeat(selector):
            raise _error(f"coverage point {node.name!r}.{bin_name} may use repeat() only in transition_bins")
        bins.append(CoverageBinIR(bin_name, kind, selector))
    _validate_point_bin_shapes(node.name, bins)
    base_name = _name(node.bases[0])
    domain = _expression_domain(owner, keywords["source"], named_domains)
    if domain is not None:
        bins = [
            CoverageBinIR(
                bin_.name,
                bin_.kind,
                _normalize_literal(
                    bin_.selector,
                    domain,
                    node.name,
                    enum_type_name=(
                        type(_scalar_descriptor(descriptor)).__name__
                        if isinstance(_scalar_descriptor(descriptor), Enum)
                        else None
                    ),
                ),
            )
            if bin_.selector is not None else bin_
            for bin_ in bins
        ]
        options.append(("comparison_domain", _comparison_domain_record(domain, descriptor)))
    elif any(_contains_literal(bin_.selector) for bin_ in bins if bin_.selector is not None):
        raise _error(
            f"coverage point {node.name!r} source has no statically inferable comparison domain; "
            "use a typed source expression"
        )
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
        field = keywords["source"]
        if not (
            isinstance(field, ast.Attribute)
            and isinstance(field.value, ast.Name)
            and field.value.id == "self"
        ):
            raise _error(f"container value-domain point {node.name!r} source must be a direct host field")
        source_expression = {
            "kind": "assoc_values" if isinstance(descriptor, AssocArray) else "container_values",
            "path": f"item.{field.attr}",
        }
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
    """Resolve ``point.bin`` or a statically indexed array bin in a cross."""
    if isinstance(node, ast.Attribute):
        point_node, bin_name = node.value, node.attr
    elif (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
        and not isinstance(node.slice.value, bool)
        and node.slice.value >= 0
    ):
        point_node = node.value.value
        bin_name = f"{node.value.attr}[{node.slice.value}]"
    else:
        raise _error("coverage cross bin selector must use member_point.member_bin or member_point.array_bin[index]")
    point = _cross_member_reference(point_node, point_groups)
    if point not in members:
        raise _error(f"coverage cross bin selector {point!r} is not in members order")
    return {"point": point, "bin": bin_name}


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
    owner: type[Any],
    allowed_names: set[str],
    static_ns: dict[str, Any],
    named_domains: dict[str, tuple[int, bool, bool]],
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
    member_view_nodes: list[ast.ClassDef] = []
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.ClassDef) and statement.name == "option":
            options.extend(_option_values(statement, f"cross {node.name!r}", "CrossOption", _CROSS_OPTIONS))
            continue
        if isinstance(statement, ast.ClassDef):
            member_view_nodes.append(statement)
            continue
        if isinstance(statement, ast.FunctionDef):
            function_nodes.append(statement)
            continue
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise _error(f"coverage cross {node.name!r} has unsupported body statement")
        assignment_nodes.append(statement)
    member_views: list[CrossMemberViewIR] = []
    for view_node in member_view_nodes:
        if view_node.name not in members:
            if "[" in " ".join(members):
                raise _error(
                    f"coverage cross {node.name!r} cannot locally redefine fixed array member "
                    f"{view_node.name!r}; array members have no class-name spelling"
                )
            raise _error(
                f"coverage cross {node.name!r} local covpoint {view_node.name!r} is not a declared member"
            )
        if len(view_node.bases) != 1 or _name(view_node.bases[0]) != "CovPoint":
            raise _error(
                f"coverage cross {node.name!r} local member {view_node.name!r} must inherit CovPoint"
            )
        local_points = _point_class(owner, view_node, allowed_names, static_ns, named_domains)
        if len(local_points) != 1:
            raise _error(
                f"coverage cross {node.name!r} local member {view_node.name!r} cannot be an array point"
            )
        local_point = local_points[0]
        forbidden = {name for name, _ in local_point.options}.intersection({"weight", "goal", "at_least"})
        if forbidden:
            raise _error(
                f"coverage cross {node.name!r} local member {view_node.name!r} cannot set aggregation option {min(forbidden)!r}"
            )
        member_views.append(CrossMemberViewIR(view_node.name, local_point))
    if len({view.name for view in member_views}) != len(member_views):
        raise _error(f"coverage cross {node.name!r} has duplicate local member definitions")
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

    effective_points = {
        **points_by_name,
        **{view.name: view.point for view in member_views},
    }
    member_points = tuple(effective_points[member] for member in members)
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
            point = effective_points[reference["point"]]
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
    return CoverageCrossIR(
        node.name,
        members,
        tuple(effective),
        iff_expression,
        tuple(options),
        queue_functions,
        tuple(member_views),
    ), total


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


def compile_coverage_initializers(owner: type[Any]) -> tuple[CoverageInitIR, ...]:
    """Compile source-only ``@coverage_init`` methods physically owned by *owner*."""
    from .declaration import CoverGroupDeclaration

    initializers: list[CoverageInitIR] = []
    initialized_groups: set[str] = set()
    declarations = {
        name: value for base in reversed(owner.mro())
        for name, value in base.__dict__.items() if isinstance(value, CoverGroupDeclaration)
    }
    marked_initializers = [
        name for name, function in owner.__dict__.items()
        if getattr(function, "_svtypes_coverage_init", False)
    ]
    if len(marked_initializers) > 1:
        raise _error(
            f"coverage class {owner.__name__} may declare only one @coverage_init method"
        )
    for name, function in owner.__dict__.items():
        if not getattr(function, "_svtypes_coverage_init", False):
            continue
        node = _function_node(function)
        if not node.args.args or node.args.args[0].arg != "self":
            raise _error(f"coverage initializer {owner.__name__}.{name} requires self as its first formal")
        if node.args.defaults or node.args.kw_defaults or node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
            raise _error(f"coverage initializer {owner.__name__}.{name} has unsupported signature")
        parameters = tuple(SampleParameterIR(argument.arg, _annotation(argument)) for argument in node.args.args[1:])
        allowed = {"self", *(item.name for item in parameters)}
        calls: list[CoverageInitCallIR] = []
        for statement in node.body:
            if isinstance(statement, ast.Pass):
                continue
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                target = call.func
                if not (
                    isinstance(target, ast.Attribute) and target.attr == "instantiate"
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name) and target.value.value.id == "self"
                ):
                    raise _error(f"coverage initializer {owner.__name__}.{name} may only call self.<covergroup>.instantiate(...)")
                group_name = target.value.attr
                declaration = declarations.get(group_name)
                if declaration is None:
                    raise _error(f"coverage initializer {owner.__name__}.{name} references unknown covergroup {group_name!r}")
                if group_name in initialized_groups:
                    raise _error(f"coverage initializer {owner.__name__} instantiates covergroup {group_name!r} more than once")
                ir = declaration.freeze()
                if len(call.args) > len(ir.constructor_parameters) or any(keyword.arg is None for keyword in call.keywords):
                    raise _error(f"coverage initializer {owner.__name__}.{name} has invalid actuals for {group_name!r}")
                named = tuple((keyword.arg, _expr(keyword.value)) for keyword in call.keywords)
                actuals = tuple(_expr(value) for value in call.args)
                for expression in (*actuals, *(value for _, value in named)):
                    _validate_expression_names(expression, allowed, f"coverage initializer {owner.__name__}.{name}")
                formal_names = {formal.name for formal in ir.constructor_parameters}
                positional_names = {formal.name for formal in ir.constructor_parameters[:len(actuals)]}
                if any(actual_name not in formal_names or actual_name in positional_names for actual_name, _ in named):
                    raise _error(f"coverage initializer {owner.__name__}.{name} has invalid actuals for {group_name!r}")
                if len(actuals) + len(named) != len(ir.constructor_parameters):
                    raise _error(f"coverage initializer {owner.__name__}.{name} must bind every CoverInput of {group_name!r}")
                calls.append(CoverageInitCallIR(group_name, actuals, named))
                initialized_groups.add(group_name)
                continue
            raise _error(f"coverage initializer {owner.__name__}.{name} has unsupported body statement")
        initializers.append(CoverageInitIR(name, parameters, tuple(calls)))
    return tuple(initializers)


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
    static_ns = dict(getattr(declaration.function, "__globals__", {}))
    if declaration.owner is not None:
        static_ns["self"] = declaration.owner
        static_ns.update(vars(declaration.owner))
    named_domains = {
        argument.arg: domain
        for argument in (sample.args.args if sample is not None else ())
        if (domain := _annotation_domain(argument.annotation, static_ns)) is not None
    }
    point_nodes = [node for node in declaration_nodes if _name(node.bases[0]) in _POINT_BASES] if all(node.bases for node in declaration_nodes) else []
    if len(point_nodes) + sum(_name(node.bases[0]) == _CROSS_BASE for node in declaration_nodes if node.bases) != len(declaration_nodes):
        invalid = next(node for node in declaration_nodes if len(node.bases) != 1 or _name(node.bases[0]) not in _POINT_BASES | {_CROSS_BASE})
        raise _error(f"coverage declaration class {invalid.name!r} must inherit CovPoint, CovPointArray, or Cross")
    grouped_points = {
        node.name: _point_class(declaration.owner, node, allowed_names, static_ns, named_domains)
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
            owner=declaration.owner,
            allowed_names=allowed_names,
            static_ns=static_ns,
            named_domains=named_domains,
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
