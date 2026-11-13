"""Opaque local slot identities that do not own their source scopes."""

import weakref
from typing import Any

from ._checked_callbacks import callback_gateway
from .base import TypeBase
from .errors import ExternalStorageClosedError, SvTypesError
from .object import SvObject, SvStruct
from .variable_access import ElementBorrow, _HandleSlot, _local_slot_binding


class SlotIdentity:
    """Compare/hash local slots while every source scope remains open.

    Equal identities need not be the same Python object. Retaining this object
    does not retain a variable, formal, or element lease. It is not a wire ID.
    """

    __slots__ = ('_key', '_sources')

    def __init__(self, key: object, sources: tuple):
        self._key = key
        self._sources = sources

    def require_open(self) -> None:
        for reference, method in self._sources:
            source = reference()
            if source is None:
                raise ExternalStorageClosedError('slot identity source no longer exists')
            if method is not None:
                try:
                    getattr(source, method)()
                except SvTypesError as error:
                    raise ExternalStorageClosedError(f'slot identity source closed: {error}') from error

    @property
    def is_valid(self) -> bool:
        try:
            self.require_open()
        except ExternalStorageClosedError:
            return False
        return True

    def __eq__(self, other: object) -> bool:
        self.require_open()
        if not isinstance(other, SlotIdentity):
            return NotImplemented
        other.require_open()
        return self._key is other._key

    def __hash__(self) -> int:
        self.require_open()
        return hash(self._key)

    def __repr__(self) -> str:
        return f'SlotIdentity({"open" if self.is_valid else "closed"})'

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


@callback_gateway
def slot_identity(variable: Any) -> SlotIdentity:
    """Identify a local variable or its Ref forwards without reading its value.

    Independent value formals have independent identities, including unassigned
    Output. Existing external slots use external_storage_location() instead.
    """
    from .checked_call import _Formal
    from .storage_relocation import _RelocatedStorage

    sources = []
    while True:
        if isinstance(variable, _Formal):
            variable._check_open()
            sources.append((weakref.ref(variable), '_check_open'))
            if variable._target is None or getattr(variable, '_svtypes_value_storage', False):
                break
            variable = variable._target.storage
            continue
        if isinstance(variable, ElementBorrow):
            state = variable._require_open()
            sources.append((weakref.ref(variable), '_require_open'))
            if state.external is not None:
                raise TypeError('external borrows use external_storage_location(), not local slot identity')
            variable = state.location.storage
            continue
        binding = _local_slot_binding(variable)
        if binding is not None:
            binding._check_open()
            if not isinstance(binding.storage, _RelocatedStorage) or binding.path.segments:
                raise TypeError('external slots use external_storage_location(), not local slot identity')
            # The temporary view has its own closing scope, unlike the original
            # local carrier which remains valid after restoration.
            actual = binding.storage.actual()
            if variable is not actual:
                sources.append((weakref.ref(binding), '_check_open'))
            variable = actual
        if not isinstance(variable, (TypeBase, _HandleSlot)) or (
            isinstance(variable, SvObject) and not isinstance(variable, SvStruct)
        ):
            raise TypeError('slot_identity() requires a local variable or scoped formal')
        sources.append((weakref.ref(variable), None))
        break
    key = getattr(variable, '_svtypes_slot_key', None)
    if key is None:
        key = object()
        object.__setattr__(variable, '_svtypes_slot_key', key)
    return SlotIdentity(key, tuple(sources))
