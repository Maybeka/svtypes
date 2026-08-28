"""SystemVerilog rendering and observation metadata for frozen coverage IR."""

from __future__ import annotations

import re
from itertools import product
from typing import Any, Iterable, Mapping

from ..errors import CoverageDeclarationError
from .auto import AUTO_COVERGROUP_NAME, auto_coverage_ir
from .ir import CoverageBinIR, CoverageCrossIR, CoverageIR, CoveragePointIR


def _identifier(value: str) -> str:
    """Produce a deterministic SystemVerilog identifier for an IR name."""
    result = re.sub(r"[^A-Za-z0-9_$]", "_", value)
    if not result or result[0].isdigit():
        result = "_" + result
    return result


def _observation_label(ir: CoverageIR, category: str, name: str) -> str:
    """Create a collision-free adapter label for one emitted coverage item."""
    return _identifier(f"{ir.covergroup_type_id}__{category}__{name}")


def _expr(expression: Any) -> str:
    kind = expression["kind"]
    if kind == "field":
        return expression["path"]
    if kind == "slot":
        return f"{expression['path']}[{expression['index']}]"
    if kind == "slot_is_null":
        return f"({expression['path']}[{expression['index']}] == null)"
    if kind == "is_null":
        return f"({expression['path']} == null)"
    if kind == "constant":
        value = expression["value"]
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "1'b1" if value else "1'b0"
        return str(value)
    if kind == "name":
        return "item" if expression["name"] == "self" else expression["name"]
    if kind == "attribute":
        return f"{_expr(expression['base'])}.{expression['name']}"
    if kind == "subscript":
        index = expression["index"]
        if index.get("kind") == "slice":
            lower = _expr(index["lower"]) if index["lower"] is not None else "0"
            upper = _expr(index["upper"]) if index["upper"] is not None else "$"
            return f"{_expr(expression['base'])}[{lower}:{upper}]"
        return f"{_expr(expression['base'])}[{_expr(index)}]"
    if kind == "unary":
        operator = "!" if expression["operator"] == "not" else expression["operator"]
        return f"({operator}{_expr(expression['operand'])})"
    if kind == "binary":
        operator = "/" if expression["operator"] == "/" else expression["operator"]
        return f"({_expr(expression['left'])} {operator} {_expr(expression['right'])})"
    if kind == "boolean":
        operator = "&&" if expression["operator"] == "and" else "||"
        return "(" + f" {operator} ".join(_expr(value) for value in expression["values"]) + ")"
    if kind == "compare":
        result = _expr(expression["left"])
        for operator, right in zip(expression["operators"], expression["comparators"]):
            result = f"({result} {operator} {_expr(right)})"
        return result
    if kind == "if":
        return f"({_expr(expression['condition'])} ? {_expr(expression['then'])} : {_expr(expression['else'])})"
    raise ValueError(f"unsupported coverage expression for SV rendering: {kind!r}")


def _selector(selector: Any) -> str:
    if selector is None:
        return ""
    kind = selector["kind"]
    if kind == "values":
        return ", ".join(_selector(item) for item in selector["items"])
    if kind == "range":
        return f"[{_expr(selector['lower'])}:{_expr(selector['upper'])}]"
    return _expr(selector)


def _point_lines(point: CoveragePointIR, indent: str) -> list[str]:
    point_name = _identifier(point.name)
    expression = _expr(point.expression)
    iff = "" if point.iff is None else f" iff ({_expr(point.iff)})"
    lines = [f"{indent}{point_name}: coverpoint {expression}{iff} {{"]
    for bin_ in point.bins:
        lines.extend(_point_bin_lines(bin_, indent + "    "))
    lines.append(f"{indent}}}")
    return lines


def _point_bin_lines(bin_: CoverageBinIR, indent: str) -> list[str]:
    name = _identifier(bin_.name)
    if bin_.kind == "default":
        return [f"{indent}bins {name} = default;"]
    prefix = {
        "normal": "bins",
        "ignore": "ignore_bins",
        "illegal": "illegal_bins",
    }.get(bin_.kind)
    if prefix is None:
        if bin_.kind == "transition":
            return [f"{indent}bins {name} = ({_selector(bin_.selector)});"]
        raise ValueError(f"unsupported point bin kind for SV rendering: {bin_.kind!r}")
    return [f"{indent}{prefix} {name} = {{{_selector(bin_.selector)}}};"]


def _cross_selector(selector: Any, point_names: dict[str, str]) -> str:
    kind = selector["kind"]
    if kind == "cross_bin_refs":
        return " && ".join(
            f"binsof({point_names[item['point']]}.{_identifier(item['bin'])})"
            for item in selector["items"]
        )
    if kind == "cross_queue_call":
        return f"{_identifier(selector['function'])}({', '.join(_expr(argument) for argument in selector['args'])})"
    if kind == "cross_queue_values":
        # Concrete queues occur only in Python instance layouts.  The template
        # renderer emits their frozen function call, never instance data.
        raise ValueError("cannot render materialized cross queue without its function call")
    raise ValueError(f"unsupported cross selector for SV rendering: {kind!r}")


def _cross_lines(
    cross: CoverageCrossIR, points: dict[str, CoveragePointIR], indent: str
) -> list[str]:
    point_names = {member: _identifier(member) for member in cross.members}
    iff = "" if cross.iff is None else f" iff ({_expr(cross.iff)})"
    lines = [f"{indent}{_identifier(cross.name)}: cross {', '.join(point_names[member] for member in cross.members)}{iff} {{"]
    # Some configured targets do not accept ``option.cross_retain_auto_bins`` in a cross.
    # Therefore suppress its implicit automatic tuples by explicitly ignoring
    # every remaining finite member-bin combination.
    has_retained_auto = any(bin_.kind == "normal" and bin_.name.startswith("auto[") for bin_ in cross.bins)
    has_named_normal = any(bin_.kind == "normal" and not bin_.name.startswith("auto[") for bin_ in cross.bins)
    if int(dict(cross.options).get("cross_retain_auto_bins", 0)) == 0 and has_named_normal and not has_retained_auto:
        static = [bin_ for bin_ in cross.bins if bin_.selector.get("kind") == "cross_bin_refs"]
        if len(static) != len(cross.bins):
            raise ValueError(
                f"cross {cross.name!r} uses CrossQueueType while the configured target cannot express cross_retain_auto_bins=0"
            )
        candidate = product(*[
            [bin_ for bin_ in points[member].bins if bin_.kind in {"normal", "default"}]
            for member in cross.members
        ])
        normal = {
            tuple((item["point"], item["bin"]) for item in bin_.selector["items"])
            for bin_ in static if bin_.kind == "normal"
        }
        ignored = {
            tuple((item["point"], item["bin"]) for item in bin_.selector["items"])
            for bin_ in static if bin_.kind == "ignore"
        }
        for index, candidate_bins in enumerate(candidate):
            selector = tuple((member, bin_.name) for member, bin_ in zip(cross.members, candidate_bins))
            if selector in normal or selector in ignored:
                continue
            refs = {"kind": "cross_bin_refs", "items": [{"point": point, "bin": bin_name} for point, bin_name in selector]}
            lines.append(f"{indent}    ignore_bins __svtypes_unselected_{index} = {_cross_selector(refs, point_names)};")
    for bin_ in cross.bins:
        if bin_.kind not in {"normal", "ignore"}:
            continue
        prefix = "bins" if bin_.kind == "normal" else "ignore_bins"
        lines.append(f"{indent}    {prefix} {_identifier(bin_.name)} = {_cross_selector(bin_.selector, point_names)};")
    lines.append(f"{indent}}}")
    return lines


def _formal_type(type_name: str) -> str:
    if type_name.startswith("CoverInput[") and type_name.endswith("]"):
        type_name = type_name[len("CoverInput["):-1].strip()
    result = {"int": "int", "bool": "bit", "str": "string", "float": "real"}.get(type_name)
    if result is None:
        raise ValueError(f"coverage formal type {type_name!r} has no SV renderer type")
    return result


def _queue_statement_lines(statement: Any, indent: str) -> list[str]:
    kind = statement["kind"]
    if kind == "queue_new":
        return [f"{indent}CrossQueueType {statement['target']} = {{}};"]
    if kind == "assign":
        return [f"{indent}int {statement['target']} = {_expr(statement['value'])};"]
    if kind == "push":
        return [f"{indent}{statement['target']}.push_back('{{{', '.join(_expr(item) for item in statement['items'])}}});"]
    if kind == "for":
        terms = statement["range"]
        if len(terms) == 1:
            start, stop, step = "0", _expr(terms[0]), "1"
        elif len(terms) == 2:
            start, stop, step = _expr(terms[0]), _expr(terms[1]), "1"
        else:
            start, stop, step = _expr(terms[0]), _expr(terms[1]), _expr(terms[2])
        lines = [
            f"{indent}for (int {statement['target']} = {start}; "
            f"(({step}) > 0 ? {statement['target']} < {stop} : {statement['target']} > {stop}); "
            f"{statement['target']} += {step}) begin"
        ]
        for child in statement["body"]:
            lines.extend(_queue_statement_lines(child, indent + "    "))
        lines.append(f"{indent}end")
        return lines
    if kind == "if":
        lines = [f"{indent}if ({_expr(statement['condition'])}) begin"]
        for child in statement["then"]:
            lines.extend(_queue_statement_lines(child, indent + "    "))
        if statement["else"]:
            lines.append(f"{indent}end else begin")
            for child in statement["else"]:
                lines.extend(_queue_statement_lines(child, indent + "    "))
        lines.append(f"{indent}end")
        return lines
    if kind == "return":
        return [f"{indent}return {statement['value']};"]
    raise ValueError(f"unsupported cross queue statement {kind!r}")


def _queue_function_lines(cross: CoverageCrossIR, indent: str) -> list[str]:
    lines: list[str] = []
    for function in cross.queue_functions:
        parameters = ", ".join(f"{_formal_type(parameter.type_name)} {parameter.name}" for parameter in function.parameters)
        lines.append(f"{indent}function CrossQueueType {_identifier(function.name)}({parameters});")
        for statement in function.body:
            lines.extend(_queue_statement_lines(statement, indent + "    "))
        lines.append(f"{indent}endfunction")
    return lines


def _declaration_groups(cls: type[Any]) -> tuple[CoverageIR, ...]:
    groups: list[CoverageIR] = []
    automatic = auto_coverage_ir(cls)
    if automatic is not None:
        groups.append(automatic)
    # A derived generated class inherits its base's nested coverage collector.
    # Only declarations physically owned by this class are emitted here.
    from .declaration import CoverGroupDeclaration

    for value in cls.__dict__.values():
        if isinstance(value, CoverGroupDeclaration):
            groups.append(value.freeze())
    return tuple(groups)


def _value_domain_declaration(cls: type[Any], point: CoveragePointIR) -> tuple[str, str, str]:
    """Return ``(field, value_type, foreach_index)`` for one auto container point."""
    expression = point.expression
    if expression.get("kind") not in {"container_values", "container_nullness", "assoc_values", "assoc_nullness"}:
        raise ValueError(f"coverage point {point.name!r} is not a value-domain point")
    _, field = expression["path"].split(".", 1)
    descriptor = next((base.__dict__[field] for base in cls.mro() if field in base.__dict__), None)
    template = getattr(descriptor, "_val_template", None)
    if template is None:
        template = getattr(descriptor, "_elem_template", None)
    if template is None:
        raise ValueError(f"coverage point {point.name!r} has no renderable value-domain descriptor")
    marker = "__svtypes_cov_value"
    declaration = template.sv_decl(marker).strip().rstrip(";")
    # ``rand`` / ``randc`` describe a field declaration. A covergroup sample
    # formal is a regular argument, where those class-property qualifiers are
    # illegal (and would make auto coverage of rand object containers fail to
    # compile). Keep only the element's actual SV type spelling.
    for qualifier in ("rand ", "randc "):
        if declaration.startswith(qualifier):
            declaration = declaration[len(qualifier):]
            break
    if not declaration.endswith(marker):
        raise ValueError(f"coverage point {point.name!r} has no scalar SV value declaration")
    return field, declaration[:-len(marker)].rstrip(), f"__svtypes_cov_index_{_identifier(point.name)}"


def render_type_coverage(cls: type[Any], indent: str, unit: str) -> list[str]:
    """Render nested collector classes from the exact frozen CoverageIR."""
    lines: list[str] = []
    sample_type = cls._sv_coverage_sample_type()
    for ir in _declaration_groups(cls):
        queue_cross = next((cross for cross in ir.crosses if cross.queue_functions), None)
        if queue_cross is not None:
            raise CoverageDeclarationError(
                "SVT-COV-SV-BACKEND",
                f"cross {queue_cross.name!r} requires CrossQueueType, which the configured SystemVerilog target does not support",
            )
        class_suffix = "__svtypes_coverage" if ir.declaration_name == AUTO_COVERGROUP_NAME else f"__{_identifier(ir.declaration_name)}__coverage"
        class_name = f"{cls.__name__}{class_suffix}"
        lines.extend(["", f"{indent}{unit}class {class_name};"])
        for formal in ir.constructor_parameters:
            # 1.8 CoverInput accepts scalar values with SV spelling retained in
            # the declaration IR.  The renderer intentionally refuses opaque
            # annotations rather than guessing a different SV type.
            lines.append(f"{indent}{unit * 2}{_formal_type(formal.type_name)} {formal.name};")
        for cross in ir.crosses:
            lines.extend(_queue_function_lines(cross, indent + unit * 2))
        scalar_points = tuple(point for point in ir.points if not dict(point.options).get("container_value_domain"))
        value_points = tuple(point for point in ir.points if dict(point.options).get("container_value_domain"))
        if scalar_points or ir.crosses:
            lines.append(f"{indent}{unit * 2}covergroup cg with function sample({sample_type} item);")
            for point in scalar_points:
                lines.extend(_point_lines(point, indent + unit * 3))
            point_map = {point.name: point for point in ir.points}
            for cross in ir.crosses:
                lines.extend(_cross_lines(cross, point_map, indent + unit * 3))
            lines.append(f"{indent}{unit * 2}endgroup")
        for point in value_points:
            _, value_type, _ = _value_domain_declaration(cls, point)
            group_name = f"cg_{_identifier(point.name)}"
            lines.append(f"{indent}{unit * 2}covergroup {group_name} with function sample({value_type} value);")
            value_expression = {"kind": "name", "name": "value"}
            if point.expression.get("kind") in {"container_nullness", "assoc_nullness"}:
                value_expression = {
                    "kind": "compare",
                    "left": value_expression,
                    "operators": ["=="],
                    "comparators": [{"kind": "constant", "value": None}],
                }
            value_point = CoveragePointIR(point.name, value_expression, point.bins, point.iff, point.options)
            lines.extend(_point_lines(value_point, indent + unit * 3))
            lines.append(f"{indent}{unit * 2}endgroup")
        lines.append("")
        signature = ", ".join(f"{_formal_type(formal.type_name)} {formal.name}" for formal in ir.constructor_parameters)
        lines.append(f"{indent}{unit * 2}function new({signature});")
        for formal in ir.constructor_parameters:
            lines.append(f"{indent}{unit * 3}this.{formal.name} = {formal.name};")
        if scalar_points or ir.crosses:
            lines.append(f"{indent}{unit * 3}cg = new();")
        for point in value_points:
            lines.append(f"{indent}{unit * 3}cg_{_identifier(point.name)} = new();")
        lines.append(f"{indent}{unit * 2}endfunction")
        lines.append("")
        lines.append(f"{indent}{unit * 2}function void sample({sample_type} item);")
        if scalar_points or ir.crosses:
            lines.append(f"{indent}{unit * 3}cg.sample(item);")
        for point in value_points:
            field, _, index = _value_domain_declaration(cls, point)
            lines.append(f"{indent}{unit * 3}foreach (item.{field}[{index}]) begin")
            lines.append(f"{indent}{unit * 4}cg_{_identifier(point.name)}.sample(item.{field}[{index}]);")
            lines.append(f"{indent}{unit * 3}end")
        lines.append(f"{indent}{unit * 2}endfunction")
        lines.append(f"{indent}{unit}endclass")
    return lines


def observation_manifest(
    types: list[type[Any]],
    *,
    instances: Iterable[Any] = (),
    target_labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return deterministic type and optional runtime-instance observations.

    ``target_labels`` is test-harness metadata, not a coverage option or an
    encoding field. A label is mandatory for each supplied per-instance record
    so a target observation can be matched without deriving a key from a
    handle, object number, or report name.
    """
    groups: list[dict[str, Any]] = []
    for cls in sorted(types, key=lambda item: (item.__module__, item.__qualname__)):
        for ir in _declaration_groups(cls):
            points = [
                {
                    "python_name": point.name,
                    "semantic_id": f"{ir.covergroup_type_id}::point::{point.name}",
                    "sv_name": _identifier(point.name),
                    "observation_label": _observation_label(ir, "point", point.name),
                    "bins": [
                        {
                            "semantic_id": f"{ir.covergroup_type_id}::point::{point.name}::bin::{bin_.name}",
                            "sv_name": _identifier(bin_.name),
                            "kind": bin_.kind,
                        }
                        for bin_ in point.bins
                    ],
                }
                for point in ir.points
            ]
            crosses = [
                {
                    "python_name": cross.name,
                    "semantic_id": f"{ir.covergroup_type_id}::cross::{cross.name}",
                    "sv_name": _identifier(cross.name),
                    "observation_label": _observation_label(ir, "cross", cross.name),
                    "bins": [
                        {
                            "semantic_id": f"{ir.covergroup_type_id}::cross::{cross.name}::bin::{bin_.name}",
                            "sv_name": _identifier(bin_.name),
                            "kind": bin_.kind,
                        }
                        for bin_ in cross.bins
                    ],
                }
                for cross in ir.crosses
            ]
            groups.append({
                "covergroup_type_id": ir.covergroup_type_id,
                "declaration_semantic_digest": ir.declaration_semantic_digest,
                "sample_type": ir.sample_type,
                "points": points,
                "crosses": crosses,
            })
    result: dict[str, Any] = {"version": 1, "covergroups": groups}
    supplied = tuple(instances)
    if not supplied:
        return result
    labels = {} if target_labels is None else dict(target_labels)
    type_ids = {group["covergroup_type_id"] for group in groups}
    records: list[dict[str, Any]] = []
    keys: set[str] = set()
    used_labels: set[str] = set()
    for instance in supplied:
        declaration = getattr(instance, "declaration", None)
        ir = getattr(declaration, "ir", None)
        key = getattr(instance, "logical_instance_key", None)
        if ir is None or not isinstance(key, str) or not key:
            raise ValueError("instance observation requires a bound logical_instance_key")
        if ir.covergroup_type_id not in type_ids:
            raise ValueError(f"instance observation type {ir.covergroup_type_id!r} is absent from manifest types")
        label = labels.get(key)
        if not isinstance(label, str) or not label:
            raise ValueError(f"instance observation {key!r} requires a non-empty target label")
        if key in keys or label in used_labels:
            raise ValueError("instance observation keys and target labels must each be unique")
        keys.add(key)
        used_labels.add(label)
        records.append({
            "covergroup_type_id": ir.covergroup_type_id,
            "instance_layout_digest": instance.instance_layout_digest,
            "logical_instance_key": key,
            "instance_name": instance.option.name if instance.option is not None else None,
            "target_label": label,
        })
    unused = set(labels).difference(keys)
    if unused:
        raise ValueError(f"target labels have no supplied Python instance: {sorted(unused)}")
    result["instances"] = sorted(records, key=lambda item: item["logical_instance_key"])
    return result
