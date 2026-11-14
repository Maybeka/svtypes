"""Checked edits for local and externally bound collections."""

import copy
from operator import index as integer_index

from .collection import Array, AssocArray, DynArray, _container_elem_is_object_handle, _new_element
from .errors import ResourceLimitError
from .external_storage import FieldOperation, _unpack_exact
from .object import SvStruct
from .variable_access import (
    _bound_location, _has_handle, _location, _prepare, _prepare_external, _snapshot, read_value, write_value,
)


_MISSING = object()


_SEQUENCE_EDITS = frozenset(("append", "insert", "extend", "pop", "remove", "clear"))
_MAPPING_EDITS = frozenset(("pop", "popitem", "clear", "update", "setdefault"))
_ORDER_EDITS = frozenset(("reverse", "sort"))


def _element_storage(codec, value):
    if _container_elem_is_object_handle(codec):
        return _new_element(codec, value)
    prepared = _prepare(codec, value)
    if isinstance(codec, SvStruct):
        return prepared
    element = copy.deepcopy(codec)
    element.value = prepared
    return element


def _mapping_updates(codec, other, kwargs):
    normalized = {}
    for key, value in dict(other, **kwargs).items():
        canonical = _prepare(codec._key_template, key)
        if canonical in normalized:
            raise ValueError(f"duplicate normalized associative key {canonical!r}")
        normalized[canonical] = value
    return normalized


def _check_capacity(codec, size):
    if size > codec._max_length:
        raise ResourceLimitError(f"{type(codec).__name__} length exceeds {codec._max_length}")


class _LocalEdits:
    def __init__(self, owner, invoke_key):
        self.owner = owner
        self.invoke_key = invoke_key

    @property
    def elements(self):
        return self.owner._elements

    def _insert(self, index, values):
        elements = [_element_storage(self.owner._elem_template, value) for value in values]
        _check_capacity(self.owner, len(self.elements) + len(elements))
        self.elements[index:index] = elements
        self.owner._bind_mode_elements()

    def append(self, value, /):
        self._insert(len(self.elements), (value,))

    def insert(self, index, value, /):
        self._insert(integer_index(index), (value,))

    def extend(self, values, /):
        self._insert(len(self.elements), values)

    def pop(self, index=-1, /):
        value = read_value(self.elements[index])
        del self.elements[index]
        self.owner._bind_mode_elements()
        return value

    def remove(self, value, /):
        index = read_value(self.owner).index(value)
        del self.elements[index]
        self.owner._bind_mode_elements()

    def clear(self):
        self.elements.clear()
        if isinstance(self.owner, DynArray):
            self.owner._bind_mode_elements()

    def reverse(self):
        if isinstance(self.owner, Array):
            write_value(self.owner, read_value(self.owner)[::-1])
        else:
            self.elements.reverse()
            self.owner._bind_mode_elements()

    def sort(self, *, key=None, reverse=False):
        def value_key(slot):
            value = read_value(slot)
            return value if key is None else self.invoke_key(key, value)

        if isinstance(self.owner, Array):
            ordered = sorted(self.elements, key=value_key, reverse=reverse)
            write_value(self.owner, [read_value(slot) for slot in ordered])
            return
        try:
            self.elements.sort(key=value_key, reverse=reverse)
        finally:
            self.owner._bind_mode_elements()

    def pop_key(self, key, default=_MISSING, /):
        key = _prepare(self.owner._key_template, key)
        if key not in self.elements:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = read_value(self.elements[key])
        del self.elements[key]
        return value

    def popitem(self):
        if not self.elements:
            raise KeyError("popitem(): associative array is empty")
        key = next(reversed(self.elements))
        return key, self.pop_key(key)

    def update(self, other=(), /, **kwargs):
        updates = _mapping_updates(self.owner, other, kwargs)
        _check_capacity(self.owner, len(self.elements.keys() | updates.keys()))
        additions = {}
        writes = []
        for key, value in updates.items():
            if key in self.elements:
                writes.append(_location(self.elements[key]).prepare(value))
            else:
                additions[key] = _element_storage(self.owner._val_template, value)
        for publish in writes:
            publish()
        self.elements.update(additions)

    def setdefault(self, key, default=None, /):
        key = _prepare(self.owner._key_template, key)
        if key not in self.elements:
            self.update({key: default})
        return read_value(self.elements[key])


class _ExternalEdits:
    def __init__(self, binding):
        self.binding = binding
        self.codec = binding.leaf.codec

    def _read(self):
        return _bound_location(self.binding).read()

    def _payload(self, codec, value):
        return _prepare_external(codec, value, self.binding, handles=_has_handle(codec))

    def _child(self, key):
        if isinstance(self.codec, AssocArray):
            return self.binding.child_key(_prepare(self.codec._key_template, key))
        index = integer_index(key)
        if index < 0:
            index += len(self._read())
        if index < 0:
            raise IndexError("collection index out of range")
        return self.binding.child_index(index)

    def append(self, value, /):
        self.binding._check_writable()
        payload = self._payload(self.codec._elem_template, value)
        self.binding._publish_operation(FieldOperation.APPEND, payload)

    def insert(self, index, value, /):
        self.binding._check_writable()
        index = integer_index(index)
        size = len(self._read())
        index = max(0, index + size) if index < 0 else min(index, size)
        payload = self._payload(self.codec._elem_template, value)
        self.binding.child_index(index)._publish_operation(FieldOperation.INSERT, payload)

    def extend(self, values, /):
        self.binding._check_writable()
        payloads = [self._payload(self.codec._elem_template, value) for value in values]
        if payloads:
            _check_capacity(self.codec, len(self._read()) + len(payloads))
        for payload in payloads:
            self.binding._publish_operation(FieldOperation.APPEND, payload)

    def pop(self, key=-1, /):
        self.binding._check_writable()
        child = self._child(key)
        value = _bound_location(child).read()
        returned = child._publish_operation(FieldOperation.POP, None)
        if returned is None:
            return value
        codec = child.leaf.codec
        handles = _has_handle(codec)
        return _snapshot(codec, _unpack_exact(codec, returned,
                         session=child.session if handles else None, resolve_only=handles,
                         diagnostics=child.diagnostics))

    def pop_key(self, key, default=_MISSING, /):
        key = _prepare(self.codec._key_template, key)
        if key not in self._read():
            if default is _MISSING:
                raise KeyError(key)
            return default
        return self.pop(key)

    def remove(self, value, /):
        self.binding._check_writable()
        self.delete(self._read().index(value))

    def clear(self):
        self.binding._check_writable()
        if isinstance(self.codec, AssocArray):
            for key in self._read():
                self.delete(key)
        else:
            self.delete(slice(None))

    def popitem(self):
        self.binding._check_writable()
        current = self._read()
        if not current:
            raise KeyError("popitem(): associative array is empty")
        key = next(reversed(current))
        return key, self.pop(key)

    def update(self, other=(), /, **kwargs):
        self.binding._check_writable()
        updates = _mapping_updates(self.codec, other, kwargs)
        current = self._read()
        _check_capacity(self.codec, len(current.keys() | updates.keys()))
        writes = [_bound_location(self.binding.child_key(key)).prepare(value)
                  for key, value in updates.items()]
        for publish in writes:
            publish()

    def setdefault(self, key, default=None, /):
        key = _prepare(self.codec._key_template, key)
        current = self._read()
        if key in current:
            return current[key]
        _check_capacity(self.codec, len(current) + 1)
        child = _bound_location(self.binding.child_key(key))
        child.prepare(default)()
        return child.read()

    def delete(self, key):
        self.binding._check_writable()
        if not isinstance(key, slice):
            self._child(key)._publish_operation(FieldOperation.DELETE, None)
            return
        positions = range(*key.indices(len(self._read())))
        for position in sorted(positions, reverse=True):
            self.binding.child_index(position)._publish_operation(FieldOperation.DELETE, None)

    def replace_slice(self, key, values):
        self.binding._check_writable()
        current = self._read()
        values = list(values)
        replacement = current.copy()
        replacement[key] = values
        codec = self.codec
        if isinstance(codec, Array) and len(replacement) != codec._size:
            raise ValueError(f"Array slice assignment must preserve its size {codec._size}")
        if isinstance(codec, DynArray) and len(replacement) > codec._max_length:
            raise ResourceLimitError(f"{type(codec).__name__} length exceeds {codec._max_length}")
        payloads = [self._payload(codec._elem_template, value) for value in values]
        start, stop, step = key.indices(len(current))
        positions = list(range(start, stop, step))
        if isinstance(codec, Array):
            for index, payload in zip(positions, payloads):
                self.binding.child_index(index)._publish_payload(payload, resolve_only=_has_handle(codec._elem_template))
            return
        if step == 1:
            for index in reversed(positions):
                self.binding.child_index(index)._publish_operation(FieldOperation.DELETE, None)
            for index, payload in enumerate(payloads, start):
                self.binding.child_index(index)._publish_operation(FieldOperation.INSERT, payload)
            return
        for index, payload in zip(positions, payloads):
            child = self.binding.child_index(index)
            child._publish_operation(FieldOperation.DELETE, None)
            child._publish_operation(FieldOperation.INSERT, payload)
