"""Lifetime-checked metadata for forwarding existing external variable slots."""

from dataclasses import dataclass, field as dataclass_field
from typing import Any

from ._checked_callbacks import callback_gateway
from .external_storage import FieldDescriptor, FieldPath
from .variable_access import ElementBorrow, _HandleSlot, _value_binding


@dataclass(frozen=True, slots=True)
class ExternalStorageLocation:
    """An existing field path or opaque borrow, never a new value or lease.

    Framework consumers must honor readonly and use a borrow token directly;
    they must not turn a stable borrow back into an index/key lookup.
    """

    _kind: str
    _storage: Any
    _descriptor: FieldDescriptor
    _key: Any = None
    _path: FieldPath | None = None
    _token: Any = None
    _readonly: bool = False
    _session: Any = None
    _checks: tuple = dataclass_field(default=(), repr=False, compare=False)

    def require_open(self):
        for check in self._checks:
            check()

    @property
    def kind(self):
        self.require_open()
        return self._kind

    @property
    def storage(self):
        self.require_open()
        return self._storage

    @property
    def descriptor(self):
        self.require_open()
        return self._descriptor

    @property
    def key(self):
        self.require_open()
        if self._kind != 'field':
            raise AttributeError('stable borrow locations use token, not a field key')
        return self._key

    @property
    def path(self):
        self.require_open()
        if self._kind != 'field':
            raise AttributeError('stable borrow locations do not expose an index/key path')
        return self._path

    @property
    def token(self):
        self.require_open()
        if self._kind != 'borrow':
            raise AttributeError('field locations use key/path, not a borrow token')
        return self._token

    @property
    def readonly(self):
        self.require_open()
        return self._readonly

    @property
    def session(self):
        self.require_open()
        return self._session


@callback_gateway
def external_storage_location(variable) -> ExternalStorageLocation | None:
    """Locate an existing external slot without reading or copying its value.

    Independent local variables/formals return None. Forwarded Ref formals keep
    the original endpoint; stable element borrows retain their opaque token.
    The result does not acquire ownership or extend any parameter/borrow scope.
    """
    from .checked_call import _Formal, _is_carrier

    checks = []
    while True:
        if isinstance(variable, _Formal):
            variable._check_open()
            checks.append(variable._check_open)
            if variable._target is None:
                return None
            variable = variable._target.storage
            if variable is None:
                return None
            continue
        if isinstance(variable, ElementBorrow):
            state = variable._require_open()
            checks.append(variable._require_open)
            if state.external is not None:
                binding, storage, token, codec = state.external
                binding._check_open()
                return ExternalStorageLocation(
                    'borrow', storage, FieldDescriptor(codec), _token=token,
                    _readonly=binding.readonly, _session=binding.session,
                    _checks=(*checks, binding._check_open),
                )
            variable = state.location.storage
            if variable is None:
                return None
            continue
        binding = variable._binding() if isinstance(variable, _HandleSlot) else _value_binding(variable)
        if binding is not None:
            binding._check_open()
            from .storage_relocation import _RelocatedStorage
            storage = binding.storage
            if isinstance(storage, _RelocatedStorage):
                storage = storage.backend
            return ExternalStorageLocation(
                'field', storage, binding.root, _key=binding.key,
                _path=binding.path, _readonly=binding.readonly, _session=binding.session,
                _checks=(*checks, binding._check_open),
            )
        if _is_carrier(variable):
            return None
        raise TypeError('external_storage_location() requires a SvTypes variable or scoped formal')
