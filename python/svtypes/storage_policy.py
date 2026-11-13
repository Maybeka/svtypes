"""Optional backend policies for checked value arguments and Ref borrowing."""

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Protocol, runtime_checkable


class BorrowPurpose(str, Enum):
    EXPLICIT = 'explicit'
    OPERATION = 'operation'
    REF = 'ref'


_purpose = ContextVar('svtypes_borrow_purpose', default=BorrowPurpose.EXPLICIT)


def current_borrow_purpose() -> BorrowPurpose:
    """Purpose of the current acquisition; never a substitute for validation."""
    return _purpose.get()


@contextmanager
def _borrow_purpose_scope(purpose):
    token = _purpose.set(purpose)
    try:
        yield
    finally:
        _purpose.reset(token)


@runtime_checkable
class ExternalRefValidationStorage(Protocol):
    """Optional check each time an existing token binds to a real Ref formal."""

    def validate_ref_borrow(self, token: object) -> None: ...


@runtime_checkable
class ExternalValueParameterStorage(Protocol):
    """Explicit opt-in to captured-address Output/Inout copyout, never Ref."""

    @property
    def value_arguments_by_path(self) -> bool: ...


def _value_argument_paths(variable):
    from .variable_access import _BorrowTokenStorage, _value_binding
    from .storage_relocation import _RelocatedStorage

    binding = _value_binding(variable)
    if binding is None:
        return False
    binding._check_open()
    storage = binding.storage
    while isinstance(storage, (_BorrowTokenStorage, _RelocatedStorage)):
        storage = storage.storage if isinstance(storage, _BorrowTokenStorage) else storage.backend
    return getattr(storage, 'value_arguments_by_path', False) is True
