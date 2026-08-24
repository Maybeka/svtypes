"""Instance handles for constraint_mode and rand_mode."""

from __future__ import annotations

import json
from typing import Any

from ..errors import ConstraintError
from ..object import SvStruct
from ..randomizable import is_randomizable


def _require_mode_arg(on: Any) -> int:
    if type(on) is not int or on not in (0, 1):
        raise TypeError("mode argument must be the integer 0 or 1")
    return on


class ConstraintHandle:
    def __init__(self, obj: Any, name: str) -> None:
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_name", name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise ConstraintError(
            f"constraint {self._name!r} cannot be called as a function; use randomize()"
        )

    def constraint_mode(self, on: int | None = None) -> int:
        obj = self._obj
        name = self._name
        modes = obj._SvObject__svtypes_constraint_modes
        if on is None:
            return modes.get(name, 1)
        modes[name] = _require_mode_arg(on)
        return modes[name]


def bind_runtime_field(value: Any, owner: Any, name: str, desc: Any) -> Any:
    from ..collection import Array, DynArray, Queue
    from ..object import ObjectDescriptor, SvObject

    if isinstance(desc, ObjectDescriptor) or (
        isinstance(desc, SvObject) and not isinstance(desc, SvStruct)
    ):
        return value
    root = getattr(owner, "_svtypes_mode_root", None)
    prefix = getattr(owner, "_svtypes_mode_path", "")
    declared = getattr(owner, "_svtypes_declared_rand", None)
    if root is None:
        if isinstance(owner, SvStruct):
            return value
        root = owner
        path = name
        declared = bool(getattr(desc, "rand", False)) and is_randomizable(desc)
    else:
        path = f"{prefix}.{name}" if prefix else name
        if declared is None:
            declared = bool(getattr(desc, "rand", False)) and is_randomizable(desc)
    object.__setattr__(value, "_svtypes_mode_root", root)
    object.__setattr__(value, "_svtypes_mode_path", path)
    object.__setattr__(value, "_svtypes_declared_rand", declared)
    if declared:
        def rand_mode(on: int | None = None, _root=root, _path=path) -> int:
            return _root._svtypes_rand_mode(_path, on)

        object.__setattr__(value, "rand_mode", rand_mode)
    if isinstance(value, SvStruct):
        bind_nested(value, root, path, declared)
        return value
    if isinstance(value, Array):
        for index, element in enumerate(value._elements):
            child_path = f"{path}[{index}]"
            object.__setattr__(element, "_svtypes_mode_root", root)
            object.__setattr__(element, "_svtypes_mode_path", child_path)
            object.__setattr__(element, "_svtypes_declared_rand", declared)
            bind_nested(element, root, child_path, declared)
            if declared:
                def element_rand_mode(on: int | None = None, _root=root, _path=child_path) -> int:
                    return _root._svtypes_rand_mode(_path, on)

                object.__setattr__(element, "rand_mode", element_rand_mode)
    elif isinstance(value, (DynArray, Queue)):
        bind_runtime_collection_elements(value)
    return value


def bind_runtime_collection_elements(value: Any) -> None:
    """Bind per-element mode handles for an already-bound dynamic collection."""
    from ..collection import DynArray, Queue
    from ..object import ObjectDescriptor, SvObject

    if not isinstance(value, (DynArray, Queue)):
        return
    root = getattr(value, "_svtypes_mode_root", None)
    path = getattr(value, "_svtypes_mode_path", "")
    declared = bool(getattr(value, "_svtypes_declared_rand", False))
    if root is None:
        return
    for index, element in enumerate(value._elements):
        if isinstance(value._elem_template, ObjectDescriptor) or (
            isinstance(value._elem_template, SvObject) and not isinstance(value._elem_template, SvStruct)
        ):
            continue
        child_path = f"{path}[{index}]"
        bind_nested(element, root, child_path, declared)


def bind_nested(value: Any, root: Any, path: str, declared: bool) -> None:
    from ..collection import Array, DynArray, Queue

    object.__setattr__(value, "_svtypes_mode_root", root)
    object.__setattr__(value, "_svtypes_mode_path", path)
    object.__setattr__(value, "_svtypes_declared_rand", declared)
    if declared:
        def rand_mode(on: int | None = None, _root=root, _path=path) -> int:
            return _root._svtypes_rand_mode(_path, on)

        object.__setattr__(value, "rand_mode", rand_mode)
    if isinstance(value, Array):
        for index, element in enumerate(value._elements):
            bind_nested(element, root, f"{path}[{index}]", declared)
    elif isinstance(value, (DynArray, Queue)):
        bind_runtime_collection_elements(value)
    elif isinstance(value, SvStruct):
        for member_name, _member_desc in value.__class__._SvObject__svtypes_members:
            bind_nested(getattr(value, member_name), root, f"{path}.{member_name}", declared)


def path_prefixes(path: str) -> list[str]:
    prefixes = []
    acc = ""
    index = 0
    while index < len(path):
        if path[index] == ".":
            prefixes.append(acc)
            acc += path[index]
            index += 1
            continue
        if path[index] == "[":
            prefixes.append(acc)
            end = path.index("]", index)
            acc += path[index:end + 1]
            index = end + 1
            continue
        if path[index] == "{":
            prefixes.append(acc)
            # Associative entries use ``{@<json>}``.  A string key may itself
            # contain a closing brace, so the matching delimiter is the one
            # following a complete JSON value rather than the first ``}``.
            if not path.startswith("{@", index):
                raise ConstraintError(f"invalid associative-array path {path!r}")
            try:
                _, end = json.JSONDecoder().raw_decode(path, index + 2)
            except json.JSONDecodeError as exc:
                raise ConstraintError(f"invalid associative-array path {path!r}") from exc
            if end >= len(path) or path[end] != "}":
                raise ConstraintError(f"invalid associative-array path {path!r}")
            acc += path[index:end + 1]
            index = end + 1
            continue
        acc += path[index]
        index += 1
    if acc:
        prefixes.append(acc)
    return prefixes
