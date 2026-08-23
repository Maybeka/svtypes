"""Leaf flattening, path helpers, and 2-state projection."""

from __future__ import annotations

from typing import Any, Iterator

from ..bit import Bit
from ..collection import Array, DynArray, Queue
from ..enum import Enum
from ..errors import ConstraintError
from ..logic import Logic, LogicValue
from ..object import ObjectDescriptor, SvObject, SvStruct
from ..randomizable import is_randomizable


def split_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    buf = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if buf:
                tokens.append(buf)
                buf = ""
            index += 1
            continue
        if char == "[":
            if buf:
                tokens.append(buf)
                buf = ""
            end = path.index("]", index)
            tokens.append(int(path[index + 1:end]))
            index = end + 1
            continue
        buf += char
        index += 1
    if buf:
        tokens.append(buf)
    return tokens


def join_path(base: str, part: str | int | tuple[str, str]) -> str:
    if isinstance(part, tuple) and part and part[0] == "idx":
        return f"{base}[{part[1]}]"
    if isinstance(part, int):
        return f"{base}[{part}]"
    return f"{base}.{part}" if base else part


def format_path(parts: list[str | int]) -> str:
    text = ""
    for part in parts:
        text = join_path(text, part)
    return text


def _is_handle(desc: Any) -> bool:
    return isinstance(desc, ObjectDescriptor) or (
        isinstance(desc, SvObject) and not isinstance(desc, SvStruct)
    )


def flatten_descriptor(desc: Any, path: str) -> Iterator[tuple[str, Any]]:
    if _is_handle(desc):
        return
    if isinstance(desc, Array):
        for index in range(desc._size):
            yield from flatten_descriptor(desc._elem_template, f"{path}[{index}]")
        return
    if isinstance(desc, SvStruct):
        for name, member in desc.__class__._SvObject__svtypes_members:
            yield from flatten_descriptor(member, f"{path}.{name}")
        return
    if isinstance(desc, (Bit, Logic, Enum)):
        yield path, desc


def iter_class_leaves(cls: type) -> Iterator[tuple[str, Any, bool]]:
    members = getattr(cls, "_SvObject__svtypes_members", ())
    for name, desc in members:
        declared_rand = bool(getattr(desc, "rand", False)) and is_randomizable(desc)
        for path, leaf in flatten_descriptor(desc, name):
            yield path, leaf, declared_rand


def iter_object_leaves(obj: Any, cls: type | None = None) -> Iterator[tuple[str, Any, bool]]:
    """Flatten scalar leaves, expanding dynamic arrays and queues at their current size."""

    if cls is None:
        cls = obj.__class__
    for name, desc in getattr(cls, "_SvObject__svtypes_members", ()):
        declared_rand = bool(getattr(desc, "rand", False)) and is_randomizable(desc)
        yield from _flatten_object_descriptor(getattr(obj, name), desc, name, declared_rand)


def _flatten_object_descriptor(
    value: Any,
    desc: Any,
    path: str,
    declared_rand: bool,
) -> Iterator[tuple[str, Any, bool]]:
    if _is_handle(desc):
        return
    if isinstance(desc, Array):
        for index, element in enumerate(value._elements):
            yield from _flatten_object_descriptor(element, desc._elem_template, f"{path}[{index}]", declared_rand)
        return
    if isinstance(desc, (DynArray, Queue)):
        for index, element in enumerate(value._elements):
            yield from _flatten_object_descriptor(element, desc._elem_template, f"{path}[{index}]", declared_rand)
        return
    if isinstance(desc, SvStruct):
        for name, member in desc.__class__._SvObject__svtypes_members:
            yield from _flatten_object_descriptor(
                getattr(value, name), member, f"{path}.{name}", declared_rand
            )
        return
    if isinstance(desc, (Bit, Logic, Enum)):
        yield path, desc, declared_rand


def resolve_attr(obj: Any, path: str) -> Any:
    current = obj
    for token in split_path(path):
        current = current[token] if isinstance(token, int) else getattr(current, token)
    return current


def leaf_unsigned(desc: Any, value: Any) -> int:
    width = desc.width
    mask = (1 << width) - 1
    if isinstance(desc, Logic):
        if not isinstance(value, LogicValue):
            value = desc._normalize(value)
        return value.value_mask & mask
    if isinstance(desc, Enum):
        return int(value) & mask
    return int(value) & mask


def leaf_has_xz(desc: Any, value: Any) -> bool:
    if not isinstance(desc, Logic):
        return False
    if not isinstance(value, LogicValue):
        value = desc._normalize(value)
    return (value.x_mask | value.z_mask) != 0


def write_leaf_bits(obj: Any, path: str, bits: int) -> None:
    from ..logic import LogicValue

    target = resolve_attr(obj, path)
    desc = target
    if isinstance(desc, Logic):
        desc.value = LogicValue(desc.width, bits & ((1 << desc.width) - 1), 0, 0)
        return
    desc.value = bits


def read_leaf_bits(obj: Any, path: str) -> tuple[Any, int]:
    target = resolve_attr(obj, path)
    return target, leaf_unsigned(target, target.value)


def snapshot_leaves(obj: Any, paths: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for path in paths:
        target = resolve_attr(obj, path)
        value = target.value
        if isinstance(value, LogicValue):
            values[path] = LogicValue(value.width, value.value_mask, value.x_mask, value.z_mask)
        else:
            values[path] = value
    return values


def restore_leaves(obj: Any, values: dict[str, Any]) -> None:
    for path, value in values.items():
        resolve_attr(obj, path).value = value
