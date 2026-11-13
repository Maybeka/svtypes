"""Scoped migration of existing local value slots to external storage."""

import copy
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .base import TypeBase
from .collection import Array, AssocArray, DynArray
from .errors import ExternalStorageError, StorageRelocationError, StorageRelocationFailure
from .external_storage import (
    ExternalFieldStorage, ExternalValueBinding, bind_external_value,
    external_binding, _set_external_binding,
)
from .object import ObjectDescriptor, SvObject, SvStruct
from .slot_identity import SlotIdentity, slot_identity
from .variable_access import (
    ElementBorrow, read_value, write_value, _value_binding, _location,
    _check_relocation_overlap,
)


class _RelocatedStorage:
    def __init__(self, backend, actual):
        self.backend = backend
        self.actual = weakref.ref(actual)

    def read(self, key, descriptor, path):
        return self.backend.read(key, descriptor, path)

    def write(self, key, descriptor, path, operation, payload):
        return self.backend.write(key, descriptor, path, operation, payload)


def _actual(variable):
    from .checked_call import _Formal

    while True:
        if isinstance(variable, _Formal):
            variable._check_open()
            if variable._target is None or variable._target.storage is None:
                raise TypeError('relocated_slot() requires an actual variable, not a value formal')
            variable = variable._target.storage
        elif isinstance(variable, ElementBorrow):
            state = variable._require_open()
            if state.external is not None:
                raise TypeError('relocated_slot() does not migrate existing external element borrows')
            variable = state.location.storage
        else:
            break
    binding = _value_binding(variable)
    if binding is not None:
        binding._check_open()
        if not isinstance(binding.storage, _RelocatedStorage):
            raise TypeError('existing external slots use external_storage_location(); they are not relocated')
    if binding is not None and isinstance(binding.storage, _RelocatedStorage):
        binding._check_open()
        if binding.path.segments:
            raise TypeError('relocated_slot() does not relocate aggregate subslots')
        variable = binding.storage.actual()
    if not isinstance(variable, TypeBase) or (
        isinstance(variable, SvObject) and not isinstance(variable, SvStruct)
    ):
        raise TypeError('relocated_slot() requires a value-root actual, not an object or borrowed subslot')
    return variable


@contextmanager
def _formal_storage(variable):
    from .checked_call import _Formal

    formal = variable
    while isinstance(formal, _Formal):
        formal._check_open()
        if formal._target is None:
            formal._check_assigned()
            # Aggregate formals already own storage. Keep it so existing child
            # leases remain visible to the overlap check before allocation.
            carrier = formal._value if formal._aggregate else copy.deepcopy(formal._codec)
            if not formal._aggregate:
                write_value(carrier, formal.value)
            object.__setattr__(carrier, '_svtypes_slot_key', slot_identity(formal)._key)
            formal._target = _location(carrier)
            formal._svtypes_value_storage = True
            try:
                yield
            finally:
                try:
                    if not formal._aggregate:
                        value = read_value(carrier)
                finally:
                    formal._target = None
                    formal._svtypes_value_storage = False
                if not formal._aggregate:
                    formal.value = value
            return
        formal = formal._target.storage
    yield


def _value_handles(codec, value):
    if isinstance(codec, SvStruct):
        for name, child in codec._SvObject__svtypes_members:
            yield from _value_handles(child, value[name])
    elif isinstance(codec, AssocArray):
        for child in value.values():
            yield from _value_handles(codec._val_template, child)
    elif isinstance(codec, (Array, DynArray)):
        for child in value:
            yield from _value_handles(codec._elem_template, child)
    elif isinstance(codec, (ObjectDescriptor, SvObject)) and value is not None:
        yield value


def _relocation_codec(actual, initial):
    owner = getattr(actual, '_svtypes_mode_root', None)
    memo = {} if owner is None else {id(owner): None}
    session = None
    for referent in _value_handles(actual, initial):
        if session is not None and referent.codec_session is not session:
            raise ExternalStorageError('cannot relocate handles from different codec sessions')
        session = referent.codec_session
        if session.get(referent.svtypes_object_number) is not referent:
            raise ExternalStorageError('relocation handle is not registered in its codec session')
        memo[id(referent)] = referent
    if session is None:
        session = getattr(owner, 'codec_session', None)
    # Copy value storage and declaration metadata, never pointed-to objects.
    codec = copy.deepcopy(actual, memo)
    return codec, session


StorageFactory = Callable[[Any, SlotIdentity], tuple[ExternalFieldStorage, object, Callable[[], None]]]


@contextmanager
def relocated_slot(variable: Any, create_storage: StorageFactory) -> Iterator[ExternalValueBinding]:
    """Use one external slot for a local variable and its existing Ref aliases.

    The factory receives a detached initial value and a scoped slot identity,
    and returns (backend, key, release). It owns cleanup until that return.
    Nested scopes reuse the first allocation. The last exit restores the latest
    value, closes the temporary view and calls release, including on exceptions.
    Restoration/cleanup failures raise StorageRelocationError with all causes.
    """
    with _formal_storage(variable):
        with _relocated_slot(variable, create_storage) as view:
            yield view


def _failure(phase, function, failures):
    try:
        return function()
    except BaseException as error:
        failures.append(StorageRelocationFailure(phase, error))
        return None


@contextmanager
def _relocated_slot(variable, create_storage):
    actual = _actual(variable)
    state = getattr(actual, '_svtypes_migration', None)
    if state is not None:
        if state['view'] is None:
            raise ExternalStorageError('slot relocation is still preparing')
        state['users'] += 1
    else:
        if external_binding(actual) is not None:
            raise TypeError('relocated_slot() does not migrate existing external slots; use external_storage_location()')
        _check_relocation_overlap(actual)
        initial = read_value(actual)
        codec, session = _relocation_codec(actual, initial)
        state = dict(users=1, view=None, release=None)
        object.__setattr__(actual, '_svtypes_migration', state)
        try:
            backend, key, release = create_storage(initial, slot_identity(variable))
            if not callable(release):
                raise TypeError('storage factory release must be callable')
            state['release'] = release
            if not isinstance(backend, ExternalFieldStorage):
                raise TypeError('storage factory backend must implement ExternalFieldStorage.read/write')
            view = bind_external_value(codec, _RelocatedStorage(backend, actual), key, session=session)
            state['view'] = view
            _set_external_binding(actual, view._binding)
        except BaseException as error:
            state['users'] = 0
            _set_external_binding(actual, None)
            object.__delattr__(actual, '_svtypes_migration')
            failures = [StorageRelocationFailure('prepare', error)]
            if state['view'] is not None:
                _failure('close', state['view'].close, failures)
            if state['release'] is not None:
                _failure('release', state['release'], failures)
            raise StorageRelocationError(tuple(failures), restored=True) from error
    body_error = None
    try:
        yield state['view']
    except BaseException as error:
        body_error = error
        raise
    finally:
        state['users'] -= 1
        if state['users'] == 0:
            failures = []
            restored = False
            final = _failure('readback', lambda: read_value(actual), failures)
            _set_external_binding(actual, None)
            if not failures:
                _failure('restore', lambda: write_value(actual, final), failures)
                restored = not failures
            object.__delattr__(actual, '_svtypes_migration')
            _failure('close', state['view'].close, failures)
            _failure('release', state['release'], failures)
            if failures:
                cause = body_error if body_error is not None else failures[0].cause
                raise StorageRelocationError(tuple(failures), restored=restored,
                                             body_error=body_error) from cause
