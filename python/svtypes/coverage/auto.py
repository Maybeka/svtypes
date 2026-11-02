"""Compilation of ``FieldOptions.cov`` into the default coverage declaration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..base import TypeBase
from ..bit import Bit
from ..collection import Array, AssocArray, DynArray, Queue
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic
from ..object import ObjectDescriptor, SvObject, SvStruct, SvPackedStruct
from .ir import CoverageBinIR, CoverageIR, CoveragePointIR

AUTO_COVERGROUP_NAME = "svtypes_auto_cov"


class AutoCoverageDeclaration:
    """Duck-typed declaration used by the standard bound-covergroup runtime."""

    def __init__(self, owner: type["SvObject"], ir: CoverageIR) -> None:
        self.owner = owner
        self.name = AUTO_COVERGROUP_NAME
        self._ir = ir

    @property
    def qualified_name(self) -> str:
        return f"{self.owner.__name__}.{self.name}"

    @property
    def ir(self) -> CoverageIR:
        return self._ir

    def freeze(self) -> CoverageIR:
        return self._ir


def _field_expression(name: str) -> dict[str, str]:
    return {"kind": "field", "path": f"item.{name}"}


def _slot_expression(name: str, index: int) -> dict[str, Any]:
    return {"index": index, "kind": "slot", "path": f"item.{name}"}


def _slot_nullness_expression(name: str, index: int) -> dict[str, Any]:
    return {"index": index, "kind": "slot_is_null", "path": f"item.{name}"}


def _value_domain_expression(name: str, kind: str) -> dict[str, str]:
    return {"kind": kind, "path": f"item.{name}"}


def _object_element(descriptor: Any) -> bool:
    return isinstance(descriptor, ObjectDescriptor) or (
        isinstance(descriptor, SvObject) and not isinstance(descriptor, SvStruct)
    )


def _automatic_bins(descriptor: Any) -> tuple[CoverageBinIR, ...]:
    """Compile the deterministic automatic bins used by the explicit DSL."""
    if isinstance(descriptor, Enum):
        return tuple(
            CoverageBinIR(
                f"auto[{member.name}]",
                "normal",
                {
                    "kind": "enum_literal",
                    "member": member.name,
                    "type_name": type(descriptor).__name__,
                    "value": member.value,
                },
            )
            for member in descriptor._enum_items
        )
    if isinstance(descriptor, (Bit, Logic, SvPackedStruct)):
        lower = -(1 << (descriptor.width - 1)) if descriptor.signed else 0
        upper = (1 << (descriptor.width - 1)) - 1 if descriptor.signed else (1 << descriptor.width) - 1
        count = min(upper - lower + 1, 64)
        width, remainder = divmod(upper - lower + 1, count)
        result: list[CoverageBinIR] = []
        current = lower
        for index in range(count):
            end = current + width + (remainder if index == count - 1 else 0) - 1
            selector: Any = {"kind": "constant", "value": current} if current == end else {"kind": "range", "lower": {"kind": "constant", "value": current}, "upper": {"kind": "constant", "value": end}}
            result.append(CoverageBinIR(f"auto[{current}]" if current == end else f"auto[{current}:{end}]", "normal", selector))
            current = end + 1
        return tuple(result)
    if descriptor is None:
        return (
            CoverageBinIR("auto[0]", "normal", {"kind": "constant", "value": 0}),
            CoverageBinIR("auto[1]", "normal", {"kind": "constant", "value": 1}),
        )
    return (CoverageBinIR("auto", "default"),)


def _point(name: str, expression: dict[str, Any], descriptor: Any, *, value_domain: bool = False) -> CoveragePointIR:
    options = (("container_value_domain", True),) if value_domain else ()
    return CoveragePointIR(name, expression, _automatic_bins(descriptor), options=options)


def _container_point(name: str, descriptor: Any, *, indices: tuple[int, ...] = ()) -> CoveragePointIR:
    kinds = []
    leaf = descriptor
    while isinstance(leaf, (Array, DynArray, Queue, AssocArray)):
        associative = isinstance(leaf, AssocArray)
        kinds.append("mapping" if associative else "sequence")
        leaf = leaf._val_template if associative else leaf._elem_template
    nullness = _object_element(leaf)
    kind = "assoc" if isinstance(descriptor, AssocArray) else "container"
    expression: dict[str, Any] = _value_domain_expression(name, kind + ("_nullness" if nullness else "_values"))
    if len(kinds) > 1:
        expression["container_kinds"] = kinds
    if indices:
        expression["indices"] = list(indices)
    point_name = name + "".join(f"[{index}]" for index in indices)
    return _point(point_name, expression, None if nullness else leaf, value_domain=True)


def _indexed_points(name: str, descriptor: Any, indices: tuple[int, ...]) -> list[CoveragePointIR]:
    if isinstance(descriptor, Array):
        return [
            point for index in range(len(descriptor))
            for point in _indexed_points(name, descriptor._elem_template, (*indices, index))
        ]
    if isinstance(descriptor, (DynArray, Queue, AssocArray)):
        return [_container_point(name, descriptor, indices=indices)]
    nullness = _object_element(descriptor)
    expression = (_slot_nullness_expression if nullness else _slot_expression)(name, indices[0])
    if len(indices) > 1:
        expression["indices"] = list(indices)
    point_name = name + "".join(f"[{index}]" for index in indices)
    return [_point(point_name, expression, None if nullness else descriptor)]


def _contains_unpacked_struct(descriptor: Any) -> bool:
    if isinstance(descriptor, SvStruct):
        return not isinstance(descriptor, SvPackedStruct)
    if isinstance(descriptor, (Array, DynArray, Queue, AssocArray)):
        return _contains_unpacked_struct(descriptor._val_template if isinstance(descriptor, AssocArray)
                                         else descriptor._elem_template)
    return False


def _struct_collection_points(name: str, descriptor: Any) -> list[CoveragePointIR]:
    """Project each eligible struct member through its containing collections.

    Fixed dimensions retain individual slots. Dynamic dimensions contribute
    values to the same member point, without inventing a bound on their size.
    """
    points = []

    def visit(desc, display, steps):
        if isinstance(desc, SvStruct) and not isinstance(desc, SvPackedStruct):
            for member, child in desc._SvObject__svtypes_members:
                if getattr(child, "cov", False):
                    visit(child, f"{display}.{member}", [*steps, {"kind": "member", "name": member}])
        elif isinstance(desc, Array):
            for index in range(len(desc)):
                visit(desc._elem_template, f"{display}[{index}]", [*steps, {"kind": "index", "index": index}])
        elif isinstance(desc, (DynArray, Queue, AssocArray)):
            associative = isinstance(desc, AssocArray)
            visit(desc._val_template if associative else desc._elem_template, display,
                  [*steps, {"kind": "mapping" if associative else "sequence"}])
        elif isinstance(desc, (Bit, Logic, Enum, SvPackedStruct)) or _object_element(desc):
            nullness = _object_element(desc)
            expression = {"kind": "container_nullness" if nullness else "container_values",
                          "path": f"item.{name}", "projection": steps}
            points.append(_point(display, expression, None if nullness else desc, value_domain=True))

    visit(descriptor, name, [])
    return points


def auto_coverage_ir(cls: type["SvObject"]) -> CoverageIR | None:
    """Compile the default group implied by effective field ``cov`` policies.

    This preserves ``cov`` as a convenience API while moving its declaration
    into the common coverage IR.  Dynamic collection decoder limits are not
    coverage slot limits: a dynamic array or queue becomes a value-domain
    point unless the field explicitly supplies ``cov_slots``.
    """
    points: list[CoveragePointIR] = []
    for name, descriptor in cls._SvObject__svtypes_members:
        if isinstance(descriptor, ObjectDescriptor):
            continue
        if not isinstance(descriptor, TypeBase):
            continue
        if not descriptor.cov:
            if descriptor.field_options.cov_slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} specifies cov_slots while cov is disabled",
                )
            continue
        if isinstance(descriptor, SvObject) and not isinstance(descriptor, SvStruct):
            points.append(
                _point(
                    name,
                    {"kind": "is_null", "path": f"item.{name}"},
                    None,
                )
            )
            continue
        slots = descriptor.field_options.cov_slots
        if isinstance(descriptor, SvStruct) and not isinstance(descriptor, SvPackedStruct):
            if slots is not None:
                raise CoverageDeclarationError("SVT-COV-SLOTS", f"{cls.__name__}.{name} is not a dynamic indexed collection")
            nested = auto_coverage_ir(type(descriptor))
            if nested is not None:
                for point in nested.points:
                    expression = dict(point.expression)
                    expression["path"] = f"item.{name}." + expression["path"].removeprefix("item.")
                    points.append(replace(point, name=f"{name}.{point.name}", expression=expression))
        elif isinstance(descriptor, (Array, DynArray, Queue, AssocArray)) and _contains_unpacked_struct(descriptor):
            if slots is None:
                points.extend(_struct_collection_points(name, descriptor))
            else:
                for index in range(slots):
                    for point in _struct_collection_points(name, descriptor._elem_template):
                        expression = dict(point.expression)
                        expression["projection"] = [{"kind": "index", "index": index}, *expression["projection"]]
                        points.append(replace(point, name=point.name.replace(name, f"{name}[{index}]", 1), expression=expression))
        elif isinstance(descriptor, Array):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is fixed-size; its array length defines coverage slots",
                )
            points.extend(_indexed_points(name, descriptor, ()))
        elif isinstance(descriptor, (DynArray, Queue)):
            if slots is None:
                points.append(_container_point(name, descriptor))
            else:
                points.extend(
                    point
                    for index in range(slots)
                    for point in _indexed_points(name, descriptor._elem_template, (index,))
                )
        elif isinstance(descriptor, AssocArray):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is associative and has no stable numeric slots",
                )
            points.append(_container_point(name, descriptor))
        elif isinstance(descriptor, (Bit, Logic, Enum, SvPackedStruct)):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is not a dynamic indexed collection",
                )
            points.append(_point(name, _field_expression(name), descriptor))
        elif slots is not None:
            raise CoverageDeclarationError(
                "SVT-COV-SLOTS",
                f"{cls.__name__}.{name} is not a dynamic indexed collection",
            )
    if not points:
        return None
    sample_type = getattr(cls, "_svtypes_unified_type_name", cls.__name__)
    return CoverageIR(
        sample_type=sample_type,
        declaration_name=AUTO_COVERGROUP_NAME,
        points=tuple(points),
    )
