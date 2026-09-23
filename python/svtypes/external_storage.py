"""Owner-scoped external storage for ordinary SvTypes fields.

The protocol in this module deliberately transports only SvTypes bytes and
typed paths.  A backend therefore never has to (and must not) duplicate the
normalisation or codec rules implemented by the descriptors themselves.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping, MutableSequence
import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .errors import CompatibilityError, ExternalStorageClosedError, ExternalStorageError


@dataclass(frozen=True, slots=True)
class FieldIdentity:
    """One declared field, disambiguated across an inheritance hierarchy."""

    declaring_type: type
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.declaring_type, type) or not isinstance(self.name, str) or not self.name:
            raise TypeError("FieldIdentity requires a declaring type and a nonempty field name")


@dataclass(frozen=True, slots=True)
class FieldMember:
    name: str


@dataclass(frozen=True, slots=True)
class FieldIndex:
    index: int

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise TypeError("FieldIndex must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class FieldKey:
    """An associative-array key in its descriptor-defined canonical bytes."""

    encoded: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.encoded, bytes):
            raise TypeError("FieldKey must contain canonical bytes")


@dataclass(frozen=True, slots=True)
class FieldPath:
    """An immutable path below one externally stored field root."""

    segments: tuple[FieldMember | FieldIndex | FieldKey, ...] = ()

    def __iter__(self) -> Iterator[FieldMember | FieldIndex | FieldKey]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def member(self, name: str) -> "FieldPath":
        return FieldPath((*self.segments, FieldMember(name)))

    def index(self, value: int) -> "FieldPath":
        return FieldPath((*self.segments, FieldIndex(value)))

    def key(self, descriptor: Any, value: Any) -> "FieldPath":
        return FieldPath((*self.segments, FieldKey(_pack_value(descriptor, value))))


class FieldOperation(str, Enum):
    SET = "set"
    INSERT = "insert"
    DELETE = "delete"
    APPEND = "append"
    POP = "pop"
    RESIZE = "resize"


@dataclass(frozen=True, slots=True)
class FieldDescriptor:
    """Stable public description of an externally addressable field."""

    codec: Any
    identity: FieldIdentity | None = None

    @property
    def unified_type_name(self) -> str:
        from .schema import unified_type_name

        return unified_type_name(self.codec)

    @property
    def encoding_descriptor(self):
        from .schema import encoding_descriptor

        return encoding_descriptor(self.codec)

    @property
    def schema_descriptor(self):
        from .schema import schema_descriptor

        return schema_descriptor(self.codec)

    def resolve_path(self, path: FieldPath) -> "FieldDescriptor":
        descriptor = self.codec
        for segment in path:
            descriptor = _child_descriptor(descriptor, segment)
        return FieldDescriptor(descriptor, self.identity)


@runtime_checkable
class ExternalFieldStorage(Protocol):
    """Backend protocol.  ``key`` remains entirely private to the backend."""

    def read(self, key: object, descriptor: FieldDescriptor, path: FieldPath) -> bytes: ...

    def write(
        self,
        key: object,
        descriptor: FieldDescriptor,
        path: FieldPath,
        operation: FieldOperation,
        payload: bytes | None,
    ) -> bytes | None: ...


class _BindingState:
    __slots__ = ("closed",)

    def __init__(self) -> None:
        self.closed = False


@dataclass(frozen=True, slots=True)
class _ExternalBinding:
    storage: ExternalFieldStorage
    key: object
    root: FieldDescriptor
    path: FieldPath = FieldPath()
    readonly: bool = False
    state: _BindingState | None = None

    def _check_open(self) -> None:
        if self.state is not None and self.state.closed:
            raise ExternalStorageClosedError("external value binding is closed")

    @property
    def leaf(self) -> FieldDescriptor:
        return self.root.resolve_path(self.path)

    def child_member(self, name: str) -> "_ExternalBinding":
        return self._replace_path(self.path.member(name))

    def child_index(self, index: int) -> "_ExternalBinding":
        return self._replace_path(self.path.index(index))

    def child_key(self, key: Any) -> "_ExternalBinding":
        return self._replace_path(self.path.key(self.leaf.codec._key_template, key))

    def _replace_path(self, path: FieldPath) -> "_ExternalBinding":
        # Validate before publishing a derived view to a backend.
        self.root.resolve_path(path)
        return _ExternalBinding(self.storage, self.key, self.root, path, self.readonly, self.state)

    def read_value(self) -> Any:
        self._check_open()
        try:
            payload = self.storage.read(self.key, self.root, self.path)
        except ExternalStorageError:
            raise
        except Exception as exc:
            raise ExternalStorageError("external field read failed") from exc
        if not isinstance(payload, bytes):
            raise ExternalStorageError("external field read must return bytes")
        return _unpack_exact(self.leaf.codec, payload)

    def write_value(self, value: Any, operation: FieldOperation = FieldOperation.SET) -> Any:
        self._check_open()
        if self.readonly:
            raise ExternalStorageError("external value binding is read-only")
        payload = _pack_value(self.leaf.codec, value)
        # Decode our own outgoing bytes so the return value has precisely the
        # same normalisation as a normal local descriptor assignment.
        normalized = _unpack_exact(self.leaf.codec, payload)
        try:
            returned = self.storage.write(self.key, self.root, self.path, operation, payload)
        except ExternalStorageError:
            raise
        except Exception as exc:
            raise ExternalStorageError("external field write failed") from exc
        if returned is not None:
            if not isinstance(returned, bytes):
                raise ExternalStorageError("external field write must return bytes or None")
            _unpack_exact(self.leaf.codec, returned)
        return normalized

    def write_operation(self, operation: FieldOperation, value: Any | None = None) -> None:
        self._check_open()
        if self.readonly:
            raise ExternalStorageError("external value binding is read-only")
        payload = None if value is None else _pack_value(_operation_payload_descriptor(self.leaf.codec, operation), value)
        try:
            returned = self.storage.write(self.key, self.root, self.path, operation, payload)
        except ExternalStorageError:
            raise
        except Exception as exc:
            raise ExternalStorageError("external field operation failed") from exc
        if returned is not None and not isinstance(returned, bytes):
            raise ExternalStorageError("external field write must return bytes or None")


def _operation_payload_descriptor(descriptor: Any, operation: FieldOperation) -> Any:
    from .collection import AssocArray, Array, DynArray, Queue

    if operation is FieldOperation.RESIZE:
        from .int import Int

        return Int()
    if operation in (FieldOperation.APPEND, FieldOperation.INSERT):
        if isinstance(descriptor, (Array, DynArray, Queue)):
            return descriptor._elem_template
        if isinstance(descriptor, AssocArray):
            return descriptor._val_template
    return descriptor


def _child_descriptor(descriptor: Any, segment: FieldMember | FieldIndex | FieldKey) -> Any:
    from .collection import AssocArray, Array, DynArray, Queue
    from .object import ObjectDescriptor, SvObject

    if isinstance(descriptor, (Array, DynArray, Queue)):
        if not isinstance(segment, FieldIndex):
            raise ExternalStorageError("collection path requires an integer index")
        return descriptor._elem_template
    if isinstance(descriptor, AssocArray):
        if not isinstance(segment, FieldKey):
            raise ExternalStorageError("associative-array path requires a typed key")
        _unpack_exact(descriptor._key_template, segment.encoded)
        return descriptor._val_template
    if isinstance(descriptor, ObjectDescriptor):
        cls = descriptor.registry.get(descriptor.cls_name)
        if cls is None:
            raise ExternalStorageError(f"object descriptor target {descriptor.cls_name!r} is unavailable")
        descriptor = cls
    if isinstance(descriptor, type) and issubclass(descriptor, SvObject):
        if not isinstance(segment, FieldMember):
            raise ExternalStorageError("object path requires a member name")
        for name, child in descriptor._SvObject__svtypes_members:
            if name == segment.name:
                return child
        raise ExternalStorageError(f"unknown object member {segment.name!r}")
    if isinstance(descriptor, SvObject):
        return _child_descriptor(type(descriptor), segment)
    raise ExternalStorageError("path extends a scalar field")


def _pack_value(descriptor: Any, value: Any) -> bytes:
    from .object import PackContext, SvObject

    return SvObject._pack_field_value(descriptor, value, PackContext())


def _unpack_exact(descriptor: Any, payload: bytes, *, session: Any | None = None) -> Any:
    from .object import SvObject, UnpackContext

    try:
        value, count = SvObject._unpack_field_value(descriptor, payload, UnpackContext(session))
    except Exception as exc:
        if isinstance(exc, ExternalStorageError):
            raise
        raise ExternalStorageError("external field payload cannot be decoded") from exc
    if count != len(payload):
        raise ExternalStorageError(
            f"external field payload has trailing bytes: consumed {count}, received {len(payload)}"
        )
    return value


def _set_external_binding(value: Any, binding: _ExternalBinding | None) -> Any:
    if binding is None:
        try:
            object.__delattr__(value, "_svtypes_external_binding")
        except AttributeError:
            pass
    else:
        object.__setattr__(value, "_svtypes_external_binding", binding)
    return value


def external_binding(value: Any) -> _ExternalBinding | None:
    return getattr(value, "_svtypes_external_binding", None)


def is_externally_bound(value: Any) -> bool:
    return external_binding(value) is not None


def _bind_loaded_object(value: Any, binding: _ExternalBinding) -> Any:
    from .object import SvObject

    if isinstance(value, SvObject):
        # Object decode can resolve an existing object from a codec session.
        # Never attach an owner-specific backend binding to that shared
        # registry object: another owner could otherwise inherit this field's
        # storage location.  A detached view keeps the binding per access.
        value = copy.deepcopy(value)
        _set_external_binding(value, binding)
    return value


class _ExternalSequence(MutableSequence[Any]):
    def __init__(self, binding: _ExternalBinding) -> None:
        self._binding = binding

    def _values(self) -> list[Any]:
        values = self._binding.read_value()
        if not isinstance(values, list):
            raise ExternalStorageError("external sequence backend returned a non-list value")
        return values

    def __len__(self) -> int:
        return len(self._values())

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._values()[index]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("external sequence index out of range")
        value = self._binding.child_index(index).read_value()
        return _bind_loaded_object(value, self._binding.child_index(index))

    def __iter__(self) -> Iterator[Any]:
        # A traversal is one backend read rather than one round-trip per
        # element.  Indexed writes still remain leaf operations.
        return iter(self._values())

    def __setitem__(self, index, value) -> None:
        if isinstance(index, slice):
            values = self._values()
            values[index] = value
            self._binding.write_value(values)
            return
        self._binding.child_index(index).write_value(value)

    def __delitem__(self, index) -> None:
        if isinstance(index, slice):
            values = self._values()
            del values[index]
            self._binding.write_value(values)
            return
        self._binding.child_index(index).write_operation(FieldOperation.DELETE)

    def insert(self, index: int, value: Any) -> None:
        # An index is part of the path; a backend that supports INSERT can
        # execute this without a read-modify-write cycle.
        self._binding.child_index(index).write_operation(FieldOperation.INSERT, value)

    def append(self, value: Any) -> None:
        self._binding.write_operation(FieldOperation.APPEND, value)

    def pop(self, index: int = -1):
        values = self._values()
        if index == -1:
            index = len(values) - 1
        result = values[index]
        self._binding.child_index(index).write_operation(FieldOperation.POP)
        return result


class _ExternalMapping(MutableMapping[Any, Any]):
    def __init__(self, binding: _ExternalBinding) -> None:
        self._binding = binding

    def _values(self) -> dict[Any, Any]:
        values = self._binding.read_value()
        if not isinstance(values, dict):
            raise ExternalStorageError("external mapping backend returned a non-dict value")
        return values

    def __getitem__(self, key: Any) -> Any:
        binding = self._binding.child_key(key)
        return _bind_loaded_object(binding.read_value(), binding)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._binding.child_key(key).write_value(value)

    def __delitem__(self, key: Any) -> None:
        self._binding.child_key(key).write_operation(FieldOperation.DELETE)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values())

    def __len__(self) -> int:
        return len(self._values())


def collection_view(binding: _ExternalBinding) -> MutableSequence[Any] | MutableMapping[Any, Any]:
    from .collection import AssocArray

    return _ExternalMapping(binding) if isinstance(binding.leaf.codec, AssocArray) else _ExternalSequence(binding)


class ExternalValueBinding:
    """A temporary external value root with the ordinary ``.value`` API."""

    def __init__(self, binding: _ExternalBinding) -> None:
        self._binding = binding

    @property
    def value(self) -> Any:
        from .collection import CollectionBase

        if isinstance(self._binding.leaf.codec, CollectionBase):
            return collection_view(self._binding)
        return _bind_loaded_object(self._binding.read_value(), self._binding)

    @value.setter
    def value(self, value: Any) -> None:
        self._binding.write_value(value)

    def close(self) -> None:
        assert self._binding.state is not None
        self._binding.state.closed = True


def bind_external_value(
    descriptor: FieldDescriptor | Any,
    storage: ExternalFieldStorage,
    key: object,
    *,
    readonly: bool = False,
) -> ExternalValueBinding:
    """Bind one temporary value root without inventing a second value DSL."""

    if not isinstance(storage, ExternalFieldStorage):
        raise TypeError("storage must implement ExternalFieldStorage.read/write")
    field = descriptor if isinstance(descriptor, FieldDescriptor) else FieldDescriptor(descriptor)
    return ExternalValueBinding(_ExternalBinding(storage, key, field, readonly=readonly, state=_BindingState()))


class MemoryExternalFieldStorage:
    """Small independent reference backend used by tests and embedders.

    It intentionally favours correctness and diagnostics over remote-backend
    performance.  Production backends receive the same leaf operations and
    can execute them natively without decoding a complete field.
    """

    def __init__(self) -> None:
        self._roots: dict[object, tuple[FieldDescriptor, bytes]] = {}
        from .object import CodecSession

        self._session = CodecSession()

    def seed(self, key: object, descriptor: FieldDescriptor | Any, value: Any) -> None:
        field = descriptor if isinstance(descriptor, FieldDescriptor) else FieldDescriptor(descriptor)
        self._roots[key] = (field, _pack_value(field.codec, value))

    def read(self, key: object, descriptor: FieldDescriptor, path: FieldPath) -> bytes:
        root_descriptor, root_payload = self._root(key, descriptor)
        root = _unpack_exact(root_descriptor.codec, root_payload, session=self._session)
        value = _get_path(root, root_descriptor.codec, path)
        return _pack_value(descriptor.resolve_path(path).codec, value)

    def write(self, key: object, descriptor: FieldDescriptor, path: FieldPath, operation: FieldOperation, payload: bytes | None) -> bytes | None:
        root_descriptor, root_payload = self._root(key, descriptor)
        root = _unpack_exact(root_descriptor.codec, root_payload, session=self._session)
        leaf = descriptor.resolve_path(path).codec
        value = None if payload is None else _unpack_exact(_operation_payload_descriptor(leaf, operation), payload)
        root = _apply_operation(root, root_descriptor.codec, path, operation, value)
        self._roots[key] = (root_descriptor, _pack_value(root_descriptor.codec, root))
        return None

    def _root(self, key: object, descriptor: FieldDescriptor) -> tuple[FieldDescriptor, bytes]:
        try:
            actual, payload = self._roots[key]
        except KeyError as exc:
            raise ExternalStorageError("external storage key is not initialized") from exc
        if actual.encoding_descriptor != descriptor.encoding_descriptor:
            raise CompatibilityError("external storage descriptor does not match bound field")
        return actual, payload


def _key_value(descriptor: Any, segment: FieldKey) -> Any:
    return _unpack_exact(descriptor._key_template, segment.encoded)


def _get_path(value: Any, descriptor: Any, path: FieldPath) -> Any:
    for segment in path:
        if isinstance(segment, FieldMember):
            value = getattr(value, segment.name).value
        elif isinstance(segment, FieldIndex):
            value = value[segment.index]
        else:
            value = value[_key_value(descriptor, segment)]
        descriptor = _child_descriptor(descriptor, segment)
    return value


def _container_at(root: Any, descriptor: Any, path: FieldPath) -> tuple[Any, Any, FieldMember | FieldIndex | FieldKey | None]:
    if not path.segments:
        return None, root, None
    parent_path = FieldPath(path.segments[:-1])
    return (
        _get_path(root, descriptor, parent_path),
        FieldDescriptor(descriptor).resolve_path(parent_path).codec,
        path.segments[-1],
    )


def _apply_operation(root: Any, descriptor: Any, path: FieldPath, operation: FieldOperation, value: Any) -> Any:
    from .collection import Array, AssocArray, DynArray, Queue

    parent, parent_desc, last = _container_at(root, descriptor, path)
    if operation is FieldOperation.SET:
        if last is None:
            # The caller replaces the root in the only place that can observe
            # it: mutate object/collections in place when possible.
            if isinstance(root, list):
                root[:] = value
            elif isinstance(root, dict):
                root.clear(); root.update(value)
            elif hasattr(root, "value"):
                root.value = value
            else:
                return value
            return root
        if isinstance(last, FieldMember):
            getattr(parent, last.name).value = value
        elif isinstance(last, FieldIndex):
            parent[last.index] = value
        else:
            parent[_key_value(parent_desc, last)] = value
        return root
    target = _get_path(root, descriptor, path)
    target_desc = FieldDescriptor(descriptor).resolve_path(path).codec
    if operation is FieldOperation.APPEND:
        if not isinstance(target, list) or not isinstance(target_desc, (DynArray, Queue)):
            raise ExternalStorageError("append requires a dynamic array or queue")
        target.append(value)
    elif operation is FieldOperation.INSERT:
        if (
            not isinstance(parent, list)
            or not isinstance(last, FieldIndex)
            or not isinstance(parent_desc, (DynArray, Queue))
        ):
            raise ExternalStorageError("insert requires an indexed sequence path")
        parent.insert(last.index, value)
    elif operation in (FieldOperation.DELETE, FieldOperation.POP):
        if (
            isinstance(last, FieldIndex)
            and isinstance(parent, list)
            and isinstance(parent_desc, (DynArray, Queue))
        ):
            del parent[last.index]
        elif isinstance(last, FieldKey) and isinstance(parent, dict):
            del parent[_key_value(parent_desc, last)]
        else:
            raise ExternalStorageError("delete/pop requires an indexed or keyed element path")
    elif operation is FieldOperation.RESIZE:
        if (
            not isinstance(target, list)
            or not isinstance(target_desc, (DynArray, Queue))
            or not isinstance(value, int)
            or value < 0
        ):
            raise ExternalStorageError("resize requires a sequence and a nonnegative length")
        del target[value:]
        while len(target) < value:
            target.append(_unpack_exact(target_desc._elem_template, _pack_value(target_desc._elem_template, 0)))
    else:
        raise ExternalStorageError(f"unsupported external operation {operation!r}")
    return root
