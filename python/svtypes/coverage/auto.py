"""Compilation of ``FieldOptions.cov`` into the default coverage declaration."""

from __future__ import annotations

from typing import Any

from ..base import TypeBase
from ..bit import Bit
from ..collection import Array, AssocArray, DynArray, Queue
from ..enum import Enum
from ..errors import CoverageDeclarationError
from ..logic import Logic
from ..object import ObjectDescriptor, SvObject, SvStruct
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
        return tuple(CoverageBinIR(f"auto[{member.value}]", "normal", {"kind": "constant", "value": member.value}) for member in descriptor._enum_items)
    if isinstance(descriptor, (Bit, Logic)):
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
            points.append(
                _point(
                    name,
                    {"kind": "is_null", "path": f"item.{name}"},
                    None,
                )
            )
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
        if isinstance(descriptor, Array):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is fixed-size; its array length defines coverage slots",
                )
            points.extend(
                _point(
                    f"{name}[{index}]",
                    _slot_nullness_expression(name, index) if _object_element(descriptor._elem_template) else _slot_expression(name, index),
                    None if _object_element(descriptor._elem_template) else descriptor._elem_template,
                )
                for index in range(len(descriptor))
            )
        elif isinstance(descriptor, (DynArray, Queue)):
            if slots is None:
                points.append(
                    _point(
                        name,
                        _value_domain_expression(name, "container_nullness" if _object_element(descriptor._elem_template) else "container_values"),
                        None if _object_element(descriptor._elem_template) else descriptor._elem_template,
                        value_domain=True,
                    )
                )
            else:
                points.extend(
                    _point(
                        f"{name}[{index}]",
                        _slot_nullness_expression(name, index) if _object_element(descriptor._elem_template) else _slot_expression(name, index),
                        None if _object_element(descriptor._elem_template) else descriptor._elem_template,
                    )
                    for index in range(slots)
                )
        elif isinstance(descriptor, AssocArray):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is associative and has no stable numeric slots",
                )
            object_values = _object_element(descriptor._val_template)
            points.append(
                _point(
                    name,
                    _value_domain_expression(name, "assoc_nullness" if object_values else "assoc_values"),
                    None if object_values else descriptor._val_template,
                    value_domain=True,
                )
            )
        elif isinstance(descriptor, (Bit, Logic, Enum)):
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
