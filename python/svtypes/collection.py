from __future__ import annotations
import copy
import struct
from typing import Any, Generic, TypeVar, overload
from .base import BuiltInType, TypeBase
from .errors import DeclarationError, EncodeError, ResourceLimitError
from .limits import DEFAULT_MAX_DYNAMIC_LENGTH

class CollectionBase(BuiltInType):
    """Base class for all hardware collection types (arrays, queues, etc.)"""
    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        raise NotImplementedError

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        raise NotImplementedError


class _ArrayMeta(type):
    """Metaclass reserved for static constructor typing of :class:`Array`."""

    pass

T = TypeVar("T", bound=TypeBase)
K = TypeVar("K", bound=TypeBase)
V = TypeVar("V", bound=TypeBase)


def _validate_element_template(codec: TypeBase, location: str) -> None:
    if not isinstance(codec, TypeBase):
        return
    options = codec.field_options
    for policy in ("rand", "plusarg", "dump", "cov"):
        if getattr(options, policy) is True:
            raise DeclarationError(
                f"{policy}=True is a containing-field policy and is invalid on {location}"
            )
    if options.cov_slots is not None:
        raise DeclarationError(
            f"cov_slots is a containing-field policy and is invalid on {location}"
        )


class Array(CollectionBase, Generic[T], metaclass=_ArrayMeta):
    """Fixed-size SystemVerilog array.

    A tuple ``size`` is expanded recursively, so ``Array(T, (3, 2))`` has
    the same concrete nested representation as ``Array(Array(T, 2), 3)``.
    Field policies supplied to the tuple form apply to its outermost array,
    exactly as they do in the explicit nested form.
    """
    _default_cov = False

    @overload
    def __new__(cls, elem_type: T, size: int, **kwargs: Any) -> "Array[T]": ...

    @overload
    def __new__(cls, elem_type: T, size: tuple[int], **kwargs: Any) -> "Array[T]": ...

    @overload
    def __new__(cls, elem_type: T, size: tuple[int, int], **kwargs: Any) -> "Array[Array[T]]": ...

    @overload
    def __new__(cls, elem_type: T, size: tuple[int, int, int], **kwargs: Any) -> "Array[Array[Array[T]]]": ...

    @overload
    def __new__(cls, elem_type: T, size: tuple[int, int, int, int], **kwargs: Any) -> "Array[Array[Array[Array[T]]]]": ...

    @overload
    def __new__(cls, elem_type: T, size: tuple[int, ...], **kwargs: Any) -> "Array[Any]": ...

    def __new__(cls, *args: Any, **kwargs: Any) -> "Array[Any]":
        return super().__new__(cls)

    def __init__(self, elem_type: T, size: int | tuple[int, ...], **kwargs: Any):
        if isinstance(size, tuple):
            if not size:
                raise ValueError("Array size tuple cannot be empty")
            if any(not isinstance(part, int) or isinstance(part, bool) or part <= 0 for part in size):
                raise DeclarationError("Every Array dimension must be a positive integer")
            from math import prod
            if prod(size) > DEFAULT_MAX_DYNAMIC_LENGTH:
                raise DeclarationError(
                    f"Array element count {prod(size)} exceeds declaration limit {DEFAULT_MAX_DYNAMIC_LENGTH}"
                )
            if len(size) == 1:
                elem_type, size = elem_type, size[0]
            else:
                # Do not propagate outer field policies into nested elements.
                elem_type, size = Array(elem_type, size[1:]), size[0]
        elif not isinstance(size, int):
            raise TypeError("size must be int or tuple of ints")

        super().__init__(**kwargs)
        _validate_element_template(elem_type, "Array element template")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise DeclarationError("Array size must be a positive integer")
        if size > DEFAULT_MAX_DYNAMIC_LENGTH:
            raise DeclarationError(
                f"Array size {size} exceeds declaration limit {DEFAULT_MAX_DYNAMIC_LENGTH}"
            )
        self._size = size
        self._elem_template = elem_type
        # Create unique instances for each slot
        self._elements = [copy.deepcopy(self._elem_template) for _ in range(self._size)]

    @property
    def rand(self):
        if self.field_options.rand is None:
            return bool(self._elem_template.rand)
        return self.field_options.rand

    @property
    def value(self) -> list[Any]:
        from .object import ObjectDescriptor
        if isinstance(self._elem_template, ObjectDescriptor):
            return list(self._elements)
        return [e.value for e in self._elements]

    @value.setter
    def value(self, vals: list[Any]):
        if len(vals) != self._size:
            raise ValueError(f"Array size mismatch: expected {self._size}, got {len(vals)}")
        from .object import ObjectDescriptor, SvObject
        for i, v in enumerate(vals):
            if (
                isinstance(self._elem_template, ObjectDescriptor)
                or (isinstance(self._elem_template, SvObject) and isinstance(v, SvObject))
            ):
                self._elements[i] = v
            else:
                self._elements[i].value = v

    def __getitem__(self, i: int) -> T:
        return self._elements[i]

    def __len__(self):
        return self._size

    def pack(self, value: list[Any]) -> bytes:
        if len(value) != self._size:
             raise ValueError(f"Expected list of size {self._size}")

        b = b''
        for i in range(self._size):
            b += self._elem_template.pack(value[i])
        return b

    def unpack(self, bytes_: bytes) -> tuple[list[Any], int]:
        offset = 0
        vals = []
        for i in range(self._size):
            val, count = self._elem_template.unpack(bytes_[offset:])
            vals.append(val)
            offset += count
        return vals, offset

    def sv_decl(self, name: str) -> str:
        return self._elem_template.sv_decl(f"{name} [{self._size}]")

    def cpp_decl(self, name: str) -> str:
        base_t = _cpp_container_elem_type(self._elem_template)
        init = "{}" if _cpp_container_elem_is_object_handle(self._elem_template) else ""
        return f"std::array<{base_t}, {self._size}> {name}{init}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        if isinstance(self._elem_template, CollectionBase):
            inner_indent = indent + self.IND
            lines = [f"{indent}foreach ({name}[i]) begin"]
            lines.extend(self._elem_template.sv_pack_loop(f"{name}[i]", level + 1, inner_indent))
            lines.append(f"{indent}end")
            return lines

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::fixed_array_packer#({elem_t}, {self._size}, {elem_packer})::pack({name}, bytes);"
        ]

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        if isinstance(self._elem_template, CollectionBase):
            inner_indent = indent + self.IND
            lines = [f"{indent}foreach ({name}[i]) begin"]
            lines.extend(self._elem_template.sv_unpack_loop(f"{name}[i]", level + 1, inner_indent))
            lines.append(f"{indent}end")
            return lines

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::fixed_array_packer#({elem_t}, {self._size}, {elem_packer})::unpack({name}, bytes, offset);"
        ]

class DynArray(CollectionBase, Generic[T]):
    """Dynamic array: type name []"""
    def __init__(self, elem_type: T, *, max_length: int = DEFAULT_MAX_DYNAMIC_LENGTH, **kwargs):
        super().__init__(**kwargs)
        _validate_element_template(elem_type, f"{self.__class__.__name__} element template")
        if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
            raise DeclarationError("DynArray max_length must be a positive integer")
        self._max_length = max_length
        self._elem_template = elem_type
        self._elements: list[T] = []

    @property
    def value(self) -> list[Any]:
        from .object import ObjectDescriptor
        if isinstance(self._elem_template, ObjectDescriptor):
            return list(self._elements)
        return [e.value for e in self._elements]

    @value.setter
    def value(self, vals: list[Any]):
        if not isinstance(vals, list):
            raise ValueError(f"Expected list, got {type(vals)}")
        if len(vals) > self._max_length:
            raise EncodeError(
                f"{self.__class__.__name__} length {len(vals)} exceeds encoder limit {self._max_length}"
            )
        from .object import ObjectDescriptor, SvObject
        self._elements = []
        for v in vals:
            if (
                isinstance(self._elem_template, ObjectDescriptor)
                or (isinstance(self._elem_template, SvObject) and isinstance(v, SvObject))
            ):
                self._elements.append(v)
            else:
                new_elem = copy.deepcopy(self._elem_template)
                new_elem.value = v
                self._elements.append(new_elem)
        self._bind_mode_elements()

    def __getitem__(self, i: int) -> T:
        return self._elements[i]

    def __len__(self):
        return len(self._elements)

    def size(self) -> int:
        """Return the current element count, matching SystemVerilog ``size()``."""
        return len(self._elements)

    def _resize_for_randomize(self, size: int) -> None:
        """Apply the SV randomize resize rule while retaining existing elements."""
        if size < 0 or size > self._max_length:
            raise ResourceLimitError(
                f"{self.__class__.__name__} randomize size {size} exceeds limit {self._max_length}"
            )
        del self._elements[size:]
        while len(self._elements) < size:
            self._elements.append(copy.deepcopy(self._elem_template))
        self._bind_mode_elements()

    def _bind_mode_elements(self) -> None:
        """Refresh optional per-element rand_mode handles after mutation."""
        if getattr(self, "_svtypes_mode_root", None) is not None:
            from .constraint.modes import bind_runtime_collection_elements

            bind_runtime_collection_elements(self)

    def pack(self, value: list[Any]) -> bytes:
        if len(value) > self._max_length:
            raise EncodeError(
                f"{self.__class__.__name__} length {len(value)} exceeds encoder limit {self._max_length}"
            )
        b = struct.pack('<I', len(value))
        for v in value:
            b += self._elem_template.pack(v)
        return b

    def unpack(self, bytes_: bytes) -> tuple[list[Any], int]:
        if len(bytes_) < self.DYN_INFO_BYTES:
            raise ValueError(
                f"Not enough bytes to unpack DynArray length: "
                f"need {self.DYN_INFO_BYTES}, got {len(bytes_)}"
            )
        length = struct.unpack('<I', bytes_[:4])[0]
        if length > self._max_length:
            raise ResourceLimitError(
                f"{self.__class__.__name__} length {length} exceeds decoder limit {self._max_length}"
            )
        offset = 4
        vals = []
        for _ in range(length):
            val, count = self._elem_template.unpack(bytes_[offset:])
            vals.append(val)
            offset += count
        return vals, offset

    def sv_decl(self, name: str) -> str:
        return self._elem_template.sv_decl(f"{name} []")

    def cpp_decl(self, name: str) -> str:
        base_t = _cpp_container_elem_type(self._elem_template)
        return f"std::vector<{base_t}> {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::dyn_array_packer#({elem_t}, {elem_packer})::pack({name}, bytes);"
        ]

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::dyn_array_packer#({elem_t}, {elem_packer})::unpack({name}, bytes, offset);"
        ]

class Queue(DynArray[T]):
    """Queue: type name [$]"""
    def sv_decl(self, name: str) -> str:
        return self._elem_template.sv_decl(f"{name} [$]")

    def push_back(self, val: Any):
        if len(self._elements) >= self._max_length:
            raise EncodeError(
                f"Queue length would exceed encoder limit {self._max_length}"
            )
        from .object import ObjectDescriptor, SvObject
        if (
            isinstance(self._elem_template, ObjectDescriptor)
            or (isinstance(self._elem_template, SvObject) and isinstance(val, SvObject))
        ):
            self._elements.append(val)
        else:
            new_elem = copy.deepcopy(self._elem_template)
            new_elem.value = val
            self._elements.append(new_elem)
        self._bind_mode_elements()

    def pop_front(self) -> Any:
        if not self._elements:
            raise IndexError("pop from empty queue")
        elem = self._elements.pop(0)
        # Queue element modes are associated with their index, matching SV:
        # an empty index's explicit mode remains available if it is populated
        # again.  Rebind closures after the indices shift.
        self._bind_mode_elements()
        from .object import ObjectDescriptor
        if isinstance(self._elem_template, ObjectDescriptor):
            return elem
        return elem.value

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::queue_packer#({elem_t}, {elem_packer})::pack({name}, bytes);"
        ]

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        elem_t = SvObject._sv_type_expr(self._elem_template)
        elem_packer = SvObject._sv_packer_expr(self._elem_template)
        return [
            f"{indent}svtypes_pkg::queue_packer#({elem_t}, {elem_packer})::unpack({name}, bytes, offset);"
        ]

class AssocArray(CollectionBase, Generic[K, V]):
    """Associative array: val_type name [key_type]"""
    def __init__(self, key_type: K, val_type: V, *, max_length: int = DEFAULT_MAX_DYNAMIC_LENGTH, **kwargs):
        super().__init__(**kwargs)
        _validate_element_template(key_type, "AssocArray key template")
        _validate_element_template(val_type, "AssocArray value template")
        if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
            raise DeclarationError("AssocArray max_length must be a positive integer")
        self._max_length = max_length
        self._key_template = key_type
        self._val_template = val_type
        self._elements: dict[Any, V] = {}

    @property
    def value(self) -> dict[Any, Any]:
        from .object import ObjectDescriptor
        if isinstance(self._val_template, ObjectDescriptor):
            return dict(self._elements)
        return {k: e.value for k, e in self._elements.items()}

    @value.setter
    def value(self, vals: dict[Any, Any]):
        if not isinstance(vals, dict):
            raise ValueError(f"Expected dict, got {type(vals)}")
        if len(vals) > self._max_length:
            raise EncodeError(
                f"AssocArray length {len(vals)} exceeds encoder limit {self._max_length}"
            )
        from .object import ObjectDescriptor, SvObject

        self._elements = {}
        for k, v in vals.items():
            if (
                isinstance(self._val_template, ObjectDescriptor)
                or (isinstance(self._val_template, SvObject) and isinstance(v, SvObject))
            ):
                self._elements[k] = v
            else:
                new_val_elem = copy.deepcopy(self._val_template)
                new_val_elem.value = v
                self._elements[k] = new_val_elem

    def __getitem__(self, key: Any) -> V:
        return self._elements[key]

    def __len__(self):
        return len(self._elements)

    def size(self) -> int:
        """Return the current entry count, matching SystemVerilog ``size()``."""
        return len(self._elements)

    def pack(self, value: dict[Any, Any]) -> bytes:
        if len(value) > self._max_length:
            raise EncodeError(
                f"AssocArray length {len(value)} exceeds encoder limit {self._max_length}"
            )
        b = struct.pack('<I', len(value))
        sorted_keys = sorted(value.keys(), key=self._key_template.pack)

        for k in sorted_keys:
            b += self._key_template.pack(k)
            b += self._val_template.pack(value[k])
        return b

    def unpack(self, bytes_: bytes) -> tuple[dict[Any, Any], int]:
        if len(bytes_) < self.DYN_INFO_BYTES:
            raise ValueError(
                f"Not enough bytes to unpack AssocArray length: "
                f"need {self.DYN_INFO_BYTES}, got {len(bytes_)}"
            )
        length = struct.unpack('<I', bytes_[:4])[0]
        if length > self._max_length:
            raise ResourceLimitError(
                f"AssocArray length {length} exceeds decoder limit {self._max_length}"
            )
        offset = 4
        vals = {}
        for _ in range(length):
            key, count = self._key_template.unpack(bytes_[offset:])
            offset += count
            val, count = self._val_template.unpack(bytes_[offset:])
            offset += count
            vals[key] = val
        return vals, offset

    def sv_decl(self, name: str) -> str:
        key_base_decl = self._key_template.sv_decl("")
        return self._val_template.sv_decl(f"{name} [{key_base_decl.strip()}]")

    def cpp_decl(self, name: str) -> str:
        val_base_t = _cpp_container_elem_type(self._val_template)
        key_base_t = self._key_template.cpp_decl("").strip()
        return f"std::map<{key_base_t}, {val_base_t}> {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        key_t = SvObject._sv_type_expr(self._key_template)
        val_t = SvObject._sv_type_expr(self._val_template)
        key_packer = SvObject._sv_packer_expr(self._key_template)
        val_packer = SvObject._sv_packer_expr(self._val_template)
        return [
            f"{indent}svtypes_pkg::assoc_array_packer#({key_t}, {val_t}, {key_packer}, {val_packer})::pack({name}, bytes);"
        ]

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        key_t = SvObject._sv_type_expr(self._key_template)
        val_t = SvObject._sv_type_expr(self._val_template)
        key_packer = SvObject._sv_packer_expr(self._key_template)
        val_packer = SvObject._sv_packer_expr(self._val_template)
        return [
            f"{indent}svtypes_pkg::assoc_array_packer#({key_t}, {val_t}, {key_packer}, {val_packer})::unpack({name}, bytes, offset);"
        ]


def _cpp_container_elem_is_object_handle(elem_template: Any) -> bool:
    from .object import ObjectDescriptor, SvObject, SvStruct
    return isinstance(elem_template, ObjectDescriptor) or (
        isinstance(elem_template, SvObject) and not isinstance(elem_template, SvStruct)
    )


def _cpp_container_elem_type(elem_template: Any) -> str:
    from .object import ObjectDescriptor, SvObject, SvStruct

    if isinstance(elem_template, ObjectDescriptor):
        return f"{elem_template.cls_name}*"
    if isinstance(elem_template, SvObject) and not isinstance(elem_template, SvStruct):
        return f"{elem_template.__class__.__name__}*"
    return elem_template.cpp_decl("").strip()
