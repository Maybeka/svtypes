"""Explicit access to declared variables and their Python values."""

import copy
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from .base import TypeBase
from .collection import Array, AssocArray, DynArray, Queue
from .errors import ExternalStorageError, ResourceLimitError
from .external_storage import (
    ExternalBorrowFieldStorage,
    ExternalBorrowPathStorage,
    ExternalBorrowStorage,
    ExternalValueBinding,
    FieldDescriptor,
    FieldIndex,
    FieldMember,
    FieldOperation,
    FieldPath,
    FieldKey,
    _ExternalBinding,
    _ExternalMapping,
    _pack_value,
    _unpack_exact,
    _ExternalSequence,
    external_binding,
)
from .object import ObjectDescriptor, SvObject, SvStruct, _default_session, _declared_member


class _HandleSlot:
    __slots__ = ("_owner", "_descriptor", "_name", "_local_value", "_svtypes_slot_key", "__weakref__")

    def __init__(self, descriptor: ObjectDescriptor | SvObject, owner: SvObject | None = None, name: str = ""):
        self._owner = owner
        self._descriptor = descriptor
        self._name = name
        self._local_value: SvObject | None = None

    def __deepcopy__(self, memo):
        # Collection storage owns independent slots. Retain the declared
        # type and let the caller's graph memo govern the referent.
        slot = _HandleSlot(self._descriptor)
        memo[id(self)] = slot
        slot._local_value = copy.deepcopy(self._read_local(), memo)
        return slot

    def _binding(self):
        if self._owner is not None:
            return self._owner._external_field_binding(self._name, self._descriptor)
        return None

    def _read_local(self) -> SvObject | None:
        if self._owner is None:
            return self._local_value
        return self._owner.__dict__.get(self._descriptor._cache_key)

    def _write_local(self, value: SvObject | None) -> None:
        if self._owner is None:
            self._local_value = value
        else:
            self._owner.__dict__[self._descriptor._cache_key] = value

    @property
    def value(self) -> SvObject | None:
        binding = self._binding()
        if binding is not None:
            return _bound_location(binding).read()
        return self._read_local()

    @value.setter
    def value(self, value: SvObject | None) -> None:
        if self._owner is None:
            self._write_local(_prepare(self._descriptor, value))
        else:
            self._descriptor._validate_assignment(self._owner, value, require_strict=True)
            binding = self._binding()
            if binding is None:
                self._write_local(value)
            else:
                _bound_location(binding).prepare(value)()


def _handle_slot(owner, name, descriptor):
    slots = owner.__dict__.setdefault("_svtypes_handle_slots", {})
    if name not in slots:
        slots[name] = _HandleSlot(descriptor, owner, name)
    return slots[name]


def field(owner: SvObject, name: str) -> Any:
    """Access the declared slot by name, including a member named `value`."""
    if not isinstance(owner, SvObject):
        raise TypeError("field() requires a declared SvTypes object or struct")
    for field_name, descriptor in owner._SvObject__svtypes_members:
        if field_name != name:
            continue
        if not isinstance(descriptor, ObjectDescriptor):
            return _declared_member(owner, field_name)
        return _handle_slot(owner, field_name, descriptor)
    raise AttributeError(f"{type(owner).__name__} has no declared field {name!r}")


def _has_handle(codec: Any) -> bool:
    if isinstance(codec, SvStruct):
        return any(_has_handle(child) for _, child in codec._SvObject__svtypes_members)
    if isinstance(codec, AssocArray):
        return _has_handle(codec._val_template)
    if isinstance(codec, (Array, DynArray, Queue)):
        return _has_handle(codec._elem_template)
    return isinstance(codec, (ObjectDescriptor, SvObject))


def _snapshot(codec: Any, value: Any) -> Any:
    if isinstance(codec, SvStruct):
        return {
            name: read_value(field(value, name))
            for name, _ in codec._SvObject__svtypes_members
        }
    if isinstance(codec, AssocArray):
        return {key: _snapshot(codec._val_template, item) for key, item in value.items()}
    if isinstance(codec, (Array, DynArray, Queue)):
        return [_snapshot(codec._elem_template, item) for item in value]
    return value


def _value_binding(variable: Any):
    if isinstance(variable, (ExternalValueBinding, _ExternalMapping, _ExternalSequence)):
        return variable._binding
    return external_binding(variable)


def _has_indexed_binding(variable: Any) -> bool:
    binding = _value_binding(variable)
    return binding is not None and any(
        isinstance(segment, (FieldIndex, FieldKey)) for segment in binding.path
    )


def read_value(variable: Any) -> Any:
    """Read a value, copying aggregates while preserving object handles."""
    if isinstance(variable, (_HandleSlot, ElementBorrow)):
        return variable.value
    if isinstance(variable, SvObject) and not isinstance(variable, SvStruct):
        return variable
    binding = _value_binding(variable)
    if binding is not None:
        return _bound_location(binding).read()
    if not isinstance(variable, TypeBase):
        raise TypeError("read_value() requires a SvTypes variable")
    if isinstance(variable, SvStruct):
        return _snapshot(variable, variable)
    return _snapshot(variable, variable.value)


def _prepare(codec: Any, value: Any) -> Any:
    if isinstance(codec, SvStruct):
        if not isinstance(value, Mapping):
            raise TypeError(f"{type(codec).__name__} requires a mapping of declared fields")
        members = dict(codec._SvObject__svtypes_members)
        if value.keys() != members.keys():
            missing = set(members) - value.keys()
            extra = value.keys() - set(members)
            raise ValueError(f"{type(codec).__name__} fields differ: missing {missing!r}, extra {extra!r}")
        prepared = type(codec).codec_template()
        for name, desc in members.items():
            SvObject._assign_unpacked_field(prepared, name, desc, _prepare(desc, value[name]))
        return prepared
    if isinstance(codec, ObjectDescriptor):
        if value is not None:
            target = codec.registry.get(codec.cls_name)
            if target is None:
                raise TypeError(f"unresolved object target {codec.cls_name!r}")
            if not isinstance(value, target):
                raise TypeError(f"expected {codec.cls_name}, got {type(value).__name__}")
        return value
    if isinstance(codec, SvObject):
        return codec._normalize(value)
    if isinstance(codec, AssocArray):
        if not isinstance(value, Mapping):
            raise TypeError("AssocArray requires a mapping")
        if len(value) > codec._max_length:
            raise ResourceLimitError(f"AssocArray length {len(value)} exceeds {codec._max_length}")
        prepared = {}
        for key, item in value.items():
            normalized_key = _prepare(codec._key_template, key)
            if normalized_key in prepared:
                raise ValueError(f"duplicate normalized associative key {normalized_key!r}")
            prepared[normalized_key] = _prepare(codec._val_template, item)
        return prepared
    if isinstance(codec, (Array, DynArray, Queue)):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError(f"{type(codec).__name__} requires a sequence")
        if isinstance(codec, Array):
            if len(value) != codec._size:
                raise ValueError(f"Array size mismatch: expected {codec._size}, got {len(value)}")
        elif len(value) > codec._max_length:
            raise ResourceLimitError(f"{type(codec).__name__} length {len(value)} exceeds {codec._max_length}")
        return [_prepare(codec._elem_template, item) for item in value]
    if isinstance(value, (TypeBase, ObjectDescriptor, _HandleSlot)):
        raise TypeError("assignment requires a value, not a variable carrier")
    return codec._normalize(value)


@dataclass(frozen=True, slots=True)
class _Location:
    codec: Any
    read: Callable[[], Any]
    prepare: Callable[[Any], Callable[[], None]]
    storage: Any = None


def _check_handle_session(codec, value, session):
    if isinstance(codec, SvStruct):
        for name, member in codec._SvObject__svtypes_members:
            _check_handle_session(member, value[name], session)
    elif isinstance(codec, AssocArray):
        for item in value.values():
            _check_handle_session(codec._val_template, item, session)
    elif isinstance(codec, (Array, DynArray)):
        for item in value:
            _check_handle_session(codec._elem_template, item, session)
    elif isinstance(codec, (ObjectDescriptor, SvObject)) and value is not None:
        if session.get(value.svtypes_object_number) is not value:
            raise ExternalStorageError(
                f"object number {value.svtypes_object_number} is not bound to that referent in the handle session"
            )


def _prepare_external(codec, value, binding, *, handles):
    prepared = _prepare(codec, value)
    if handles:
        _check_handle_session(codec, _snapshot(codec, prepared), binding.session or _default_session)
    return _pack_value(codec, prepared)


def _bound_location(binding):
    codec = binding.leaf.codec
    handles = _has_handle(codec)

    def read():
        return _snapshot(codec, binding.read_value(resolve_only=handles))

    def prepare(value):
        binding._check_writable()
        payload = _prepare_external(codec, value, binding, handles=handles)
        return lambda: binding._publish_payload(payload, resolve_only=handles)

    return _Location(codec, read, prepare, ExternalValueBinding(binding))


def _location(variable: Any) -> _Location | None:
    """Capture a storage destination without reading its value."""
    if isinstance(variable, ElementBorrow):
        state = variable._require_open()

        def read():
            return variable._require_open().location.read()

        def prepare(value):
            publish = variable._require_open().location.prepare(value)

            def publish_open():
                variable._require_open()
                publish()

            return publish_open

        return _Location(state.location.codec, read, prepare, variable)
    if isinstance(variable, _HandleSlot):
        codec = variable._descriptor
        binding = variable._binding()
        if binding is not None:
            return _bound_location(binding)

        def prepare_handle(value):
            prepared = _prepare(codec, value)
            return lambda: variable._write_local(prepared)

        return _Location(codec, variable._read_local, prepare_handle, variable)
    binding = _value_binding(variable)
    if binding is not None:
        return _bound_location(binding)
    if not isinstance(variable, TypeBase) or (
        isinstance(variable, SvObject) and not isinstance(variable, SvStruct)
    ):
        return None

    def prepare_local(value):
        prepared = _prepare(variable, value)
        if isinstance(variable, SvStruct):
            return lambda: SvStruct.value.fset(variable, prepared)
        return lambda: setattr(variable, "value", prepared)

    return _Location(variable, lambda: read_value(variable), prepare_local, variable)


def write_value(variable: Any, value: Any) -> None:
    """Assign a value through its declaration, validating aggregate shape first."""
    if isinstance(variable, _HandleSlot):
        variable.value = value
        return
    location = _location(variable)
    if location is None:
        raise TypeError("write_value() requires a SvTypes variable")
    location.prepare(value)()


_local_leases = WeakKeyDictionary()


def _retain_local_slot(lease, slot):
    if isinstance(slot, (TypeBase, _HandleSlot)) and _local_slot_binding(slot) is None:
        # The lease already retains its slot. Record only identity so this
        # registry cannot keep an abandoned lease/owner cycle alive.
        _local_leases[lease] = id(slot)


def _release_local_slot(lease):
    _local_leases.pop(lease, None)


def _local_slot_binding(slot):
    return slot._binding() if isinstance(slot, _HandleSlot) else _value_binding(slot)


def _owned_value_slots(root):
    """Visit value storage only, without following object handle referents."""
    yield root
    if isinstance(root, SvStruct):
        for name, _ in root._SvObject__svtypes_members:
            yield from _owned_value_slots(field(root, name))
    elif isinstance(root, AssocArray):
        for child in root._elements.values():
            yield from _owned_value_slots(child)
    elif isinstance(root, (Array, DynArray)):
        for child in root._elements:
            yield from _owned_value_slots(child)


def _check_relocation_overlap(root):
    children = list(_owned_value_slots(root))[1:]
    identities = {id(child) for child in children}
    if any(identity in identities for identity in tuple(_local_leases.values())):
        raise ExternalStorageError("cannot relocate an aggregate with an active child borrow or Ref")
    if any(_local_slot_binding(child) is not None for child in children):
        raise ExternalStorageError("cannot relocate an aggregate containing externally bound child storage")


class _BorrowState:
    def __init__(self, location: _Location, release: Callable[[], None], project=None, *, external=None):
        self.location = location
        self.release = release
        self.project = project
        self.external = external
        self.leases = 1


class ElementBorrow:
    """One scope of access to an element selected at acquisition."""

    __slots__ = ("_state", "__weakref__")

    def __init__(self, state: _BorrowState):
        self._state: _BorrowState | None = state

    def _require_open(self) -> _BorrowState:
        from .errors import ExternalStorageClosedError

        if self._state is None:
            raise ExternalStorageClosedError("element borrow is closed")
        return self._state

    @property
    def value(self) -> Any:
        return self._require_open().location.read()

    @value.setter
    def value(self, value: Any) -> None:
        self._require_open().location.prepare(value)()

    def _projection(self):
        state = self._require_open()
        return state.location.storage if state.project is None else state.project(self)

    def child(self, path: FieldPath) -> "ElementBorrow":
        """Borrow a subslot with a lease independent of this lease."""
        if not isinstance(path, FieldPath):
            raise TypeError("borrow path must be a FieldPath")
        if not len(path):
            return self.forward()
        target = self._projection()
        if target is None:
            raise ExternalStorageError("borrowed storage does not expose aggregate subslots")
        binding = _value_binding(target)
        if binding is not None:
            selected = FieldPath(binding.path.segments + path.segments)
            return _borrow_binding(binding._replace_path(selected))
        return _borrow_local(target, path)

    def forward(self) -> "ElementBorrow":
        """Create an inner lease to the same element."""
        state = self._require_open()
        state.leases += 1
        return ElementBorrow(state)

    def close(self) -> None:
        state, self._state = self._state, None
        if state is not None:
            state.leases -= 1
            if state.leases == 0:
                _release_local_slot(state)
                state.release()

    def __enter__(self) -> "ElementBorrow":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __copy__(self):
        raise TypeError("element borrows cannot be copied; forward a scoped lease")

    def __deepcopy__(self, memo):
        return self.__copy__()


def borrow_element(container: Any, index: Any, *, path=None) -> ElementBorrow:
    """Borrow an element or a declared subfield of that element.

    Closing a lease releases its share of the stable target. A backend with
    only ordinary FieldPath access cannot provide a stable external borrow.
    """
    from operator import index as integer_index

    if isinstance(container, ElementBorrow):
        container = container._projection()
        if container is None:
            raise ExternalStorageError("borrowed storage does not expose aggregate subslots")
    binding = _value_binding(container)
    codec = binding.leaf.codec if binding is not None else container
    if not isinstance(codec, (Array, AssocArray, DynArray, Queue)):
        raise TypeError("borrow_element() requires a SvTypes container")
    suffix = FieldPath() if path is None else path
    if not isinstance(suffix, FieldPath):
        raise TypeError("borrow path must be a FieldPath")
    if isinstance(codec, AssocArray):
        index = _prepare(codec._key_template, index)
        element_path = FieldPath().key(codec._key_template, index)
    else:
        index = integer_index(index)
        if index < 0:
            index += len(container) if binding is None else len(_bound_location(binding).read())
        if index < 0:
            raise IndexError("borrow element index is out of range")
        element_path = FieldPath().index(index)

    relative = FieldPath(element_path.segments + suffix.segments)
    if binding is None:
        return _borrow_local(container, relative)
    selected = FieldPath(binding.path.segments + relative.segments)
    return _borrow_binding(binding._replace_path(selected))


def _borrow_local(target, path: FieldPath) -> ElementBorrow:
    for segment in path:
        if isinstance(target, _HandleSlot):
            target = target.value
        if isinstance(segment, FieldMember):
            target = field(target, segment.name)
        elif isinstance(segment, FieldIndex) and isinstance(target, (Array, DynArray)):
            target = target._elements[segment.index]
        elif isinstance(segment, FieldKey) and isinstance(target, AssocArray):
            key = _unpack_exact(target._key_template, segment.encoded)
            target = target._elements[key]
        else:
            raise TypeError("borrow path does not match the element declaration")
    location = _location(target)
    if location is None:
        raise TypeError("borrow requires an element slot, not a bare object handle")
    state = _BorrowState(location, lambda: None)
    _retain_local_slot(state, target)
    return ElementBorrow(state)


class _BorrowTokenStorage:
    """Adapt leaf paths to the stable aggregate selected by an active lease."""

    def __init__(self, lease, binding, storage, token):
        self.lease = lease
        self.binding = binding
        self.storage = storage
        self.token = token

    def _acquire_child(self, path):
        self.lease._require_open()
        self.binding._check_open()
        if not isinstance(self.storage, ExternalBorrowPathStorage):
            raise ExternalStorageError("borrowed aggregate backend does not support stable child borrowing")
        return self.storage.acquire_borrow_child(self.token, path)

    def borrow(self, path, codec):
        if not len(path):
            return self.lease.forward()
        return _token_borrow(self.binding, self.storage, self._acquire_child(path), codec)

    @contextmanager
    def _selected(self, path):
        if not len(path):
            self.lease._require_open()
            yield self.token
            return
        token = self._acquire_child(path)
        try:
            yield token
        finally:
            self.storage.release_borrow(token)

    def read(self, key, descriptor, path):
        if isinstance(self.storage, ExternalBorrowFieldStorage):
            self.lease._require_open()
            self.binding._check_open()
            return self.storage.read_borrow_path(self.token, path)
        with self._selected(path) as token:
            return self.storage.read_borrow(token)

    def write(self, key, descriptor, path, operation, payload):
        self.binding._check_writable()
        if isinstance(self.storage, ExternalBorrowFieldStorage):
            self.lease._require_open()
            return self.storage.write_borrow_path(self.token, path, operation, payload)
        if operation is not FieldOperation.SET:
            raise ExternalStorageError("borrowed aggregate backend does not expose structural edits")
        with self._selected(path) as token:
            self.storage.write_borrow(token, payload)


def _borrow_binding(binding) -> ElementBorrow:
    from .storage_relocation import _RelocatedStorage

    binding._check_open()
    storage = binding.storage
    if isinstance(storage, _RelocatedStorage):
        storage = storage.backend
    codec = binding.leaf.codec
    if isinstance(storage, _BorrowTokenStorage):
        return storage.borrow(binding.path, codec)
    if not isinstance(storage, ExternalBorrowStorage):
        raise ExternalStorageError("external storage does not support stable element borrowing")
    token = storage.acquire_borrow(binding.key, binding.root, binding.path)
    return _token_borrow(binding, storage, token, codec)


def _token_borrow(binding, storage, token, codec) -> ElementBorrow:
    handles = _has_handle(codec)

    def read():
        binding._check_open()
        payload = storage.read_borrow(token)
        return _snapshot(codec, _unpack_exact(codec, payload, session=binding.session, resolve_only=handles))

    def prepare(value):
        binding._check_writable()
        payload = _prepare_external(codec, value, binding, handles=handles)

        def publish():
            binding._check_writable()
            storage.write_borrow(token, payload)

        return publish

    project = None
    if isinstance(storage, (ExternalBorrowPathStorage, ExternalBorrowFieldStorage)) and isinstance(codec, (SvStruct, Array, DynArray, AssocArray)):
        def project(lease):
            adapter = _BorrowTokenStorage(lease, binding, storage, token)
            return ExternalValueBinding(
                _ExternalBinding(
                    adapter, token, FieldDescriptor(codec), readonly=binding.readonly,
                    state=binding.state, session=binding.session,
                )
            )

    location = _Location(codec, read, prepare)
    return ElementBorrow(_BorrowState(
        location, lambda: storage.release_borrow(token), project,
        external=(binding, storage, token, codec),
    ))
