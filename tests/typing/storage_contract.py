"""Public storage integration annotations available to downstream type checkers."""

from collections.abc import Callable
from typing import Any
from svtypes import (
    BorrowPurpose, ExternalRefValidationStorage, ExternalValueParameterStorage,
    Int, MemoryExternalFieldStorage, SlotIdentity, StorageFactory,
    StorageRelocationError, current_borrow_purpose, relocated_slot, slot_identity,
)


def create_storage(initial: Any, identity: SlotIdentity) -> tuple[MemoryExternalFieldStorage, str, Callable[[], None]]:
    identity.require_open()
    storage = MemoryExternalFieldStorage()
    storage.seed('slot', Int(), initial)
    return storage, 'slot', lambda: None


factory: StorageFactory = create_storage


def use(value: Int) -> bool:
    identity: SlotIdentity = slot_identity(value)
    try:
        with relocated_slot(value, factory) as view:
            view.value = 4
    except StorageRelocationError as error:
        restored: bool = error.restored
        return restored
    return identity.is_valid


def policies(backend: object) -> BorrowPurpose:
    if isinstance(backend, ExternalRefValidationStorage):
        backend.validate_ref_borrow(object())
    if isinstance(backend, ExternalValueParameterStorage):
        enabled: bool = backend.value_arguments_by_path
        assert isinstance(enabled, bool)
    return current_borrow_purpose()
