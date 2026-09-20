"""Core-model randomizable predicate."""

from __future__ import annotations

from typing import Any


def is_randomizable(desc: Any) -> bool:
    """Return whether *desc* can enter the constraint solver domain."""
    from .bit import Bit
    from .collection import Array, AssocArray, DynArray, Queue
    from .enum import Enum
    from .logic import Logic
    from .object import ObjectDescriptor, SvStruct

    if isinstance(desc, type):
        return issubclass(desc, Enum)
    if isinstance(desc, (Bit, Logic, Enum)):
        return True
    # A random class handle has no scalar SMT representation of its own, but
    # it is still a random variable: an allocated referent joins the
    # containing solve and a dynamic array/queue of such handles may have a
    # constrained size.  Null handles remain outside the solve.
    if isinstance(desc, ObjectDescriptor):
        return bool(desc.rand)
    if isinstance(desc, SvStruct):
        return all(is_randomizable(member) for _, member in desc._SvObject__svtypes_members)
    if isinstance(desc, Array):
        return is_randomizable(desc._elem_template)
    if isinstance(desc, (DynArray, Queue)):
        return is_randomizable(desc._elem_template)
    if isinstance(desc, AssocArray):
        return is_randomizable(desc._val_template)
    return False
