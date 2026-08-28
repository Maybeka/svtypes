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
from .ir import CoverageIR, CoveragePointIR

AUTO_COVERGROUP_NAME = "svtypes_auto_cov"


def _field_expression(name: str) -> dict[str, str]:
    return {"kind": "field", "path": f"item.{name}"}


def _slot_expression(name: str, index: int) -> dict[str, Any]:
    return {"index": index, "kind": "slot", "path": f"item.{name}"}


def _value_domain_expression(name: str, kind: str) -> dict[str, str]:
    return {"kind": kind, "path": f"item.{name}"}


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
                CoveragePointIR(
                    name,
                    {"kind": "is_null", "path": f"item.{name}"},
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
                CoveragePointIR(
                    name,
                    {"kind": "is_null", "path": f"item.{name}"},
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
                CoveragePointIR(f"{name}[{index}]", _slot_expression(name, index))
                for index in range(len(descriptor))
            )
        elif isinstance(descriptor, (DynArray, Queue)):
            if slots is None:
                points.append(
                    CoveragePointIR(
                        name,
                        _value_domain_expression(name, "container_values"),
                    )
                )
            else:
                points.extend(
                    CoveragePointIR(f"{name}[{index}]", _slot_expression(name, index))
                    for index in range(slots)
                )
        elif isinstance(descriptor, AssocArray):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is associative and has no stable numeric slots",
                )
            points.append(
                CoveragePointIR(name, _value_domain_expression(name, "assoc_values"))
            )
        elif isinstance(descriptor, (Bit, Logic, Enum)):
            if slots is not None:
                raise CoverageDeclarationError(
                    "SVT-COV-SLOTS",
                    f"{cls.__name__}.{name} is not a dynamic indexed collection",
                )
            points.append(CoveragePointIR(name, _field_expression(name)))
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
