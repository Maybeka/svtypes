"""Core-model randomizable predicate."""

from __future__ import annotations

from typing import Any


def is_randomizable(desc: Any) -> bool:
    """Return whether *desc* can enter the constraint solver domain."""
    from .bit import Bit
    from .collection import Array, AssocArray, DynArray, Queue
    from .enum import Enum
    from .logic import Logic
    from .object import SvStruct

    if isinstance(desc, type):
        return issubclass(desc, Enum)
    if isinstance(desc, (Bit, Logic, Enum)):
        return True
    if isinstance(desc, SvStruct):
        return all(is_randomizable(member) for _, member in desc._SvObject__svtypes_members)
    if isinstance(desc, Array):
        return is_randomizable(desc._elem_template)
    if isinstance(desc, (DynArray, Queue)):
        return is_randomizable(desc._elem_template)
    if isinstance(desc, AssocArray):
        return is_randomizable(desc._val_template)
    return False
