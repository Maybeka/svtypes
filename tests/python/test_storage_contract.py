"""Public storage policy, scoped identity and relocation contract."""

import asyncio
import gc
import weakref

import pytest
from svtypes import (
    Array, AssocArray, Bit, BorrowPurpose, CheckedAccessError, DynArray,
    ExternalRefValidationStorage, ExternalStorageClosedError, ExternalStorageError,
    ExternalValueParameterStorage, FieldPath, Inout, Input, Int, MemoryExternalFieldStorage,
    Output, Queue, Real, Ref, SlotIdentity, StorageRelocationError, String, SvObject,
    bind_external_value, borrow_element, checked_library_sources,
    current_borrow_purpose, read_value, relocated_slot, run_checked, slot_identity,
    write_value,
)


def allocator(codec, events, storage=None):
    storage = storage or MemoryExternalFieldStorage()

    def create(initial, identity):
        events.append(('create', identity))
        storage.seed('slot', codec, initial)
        return storage, 'slot', lambda: events.append(('release', identity))
    return create


def test_identity_is_metadata_and_not_a_value_or_python_formal_identity():
    actual, other = Int(3), Int(3)
    expected = slot_identity(actual)
    assert isinstance(expected, SlotIdentity)
    assert expected != slot_identity(other)
    actual.value = 4
    assert expected == slot_identity(actual)
    assert {expected: 3}[slot_identity(actual)] == 3
    seen = []

    def inner(first: Ref[Int], second: Ref[Int]):
        assert first is not second
        seen.extend((slot_identity(first), slot_identity(second)))
        assert seen[-1] == seen[-2] == expected
        first.value += 2

    def outer(value: Ref[Int]):
        run_checked(inner, value, value)
        assert slot_identity(value) == expected

    with checked_library_sources(__file__):
        run_checked(outer, actual)
    assert actual.value == 6
    assert expected.is_valid
    for identity in seen:
        assert not identity.is_valid
        with pytest.raises(ExternalStorageClosedError):
            identity.require_open()


def test_identity_does_not_keep_local_variable_or_borrow_alive():
    actual = Int(2)
    identity = slot_identity(actual)
    reference = weakref.ref(actual)
    del actual
    gc.collect()
    assert reference() is None and not identity.is_valid
    with pytest.raises(ExternalStorageClosedError):
        hash(identity)

    values = Queue[Int]()
    values.value = [1]
    lease = borrow_element(values, 0)
    forwarded = lease.forward()
    first, second = slot_identity(lease), slot_identity(forwarded)
    assert first == second == slot_identity(values[0])
    lease.close()
    assert not first.is_valid and second.is_valid
    assert forwarded.value == 1
    reference = weakref.ref(forwarded)
    del forwarded
    gc.collect()
    assert reference() is None and not second.is_valid


@pytest.mark.parametrize('direction', [Input, Output, Inout])
def test_value_formal_identity_remains_independent_across_relocation(direction):
    actual = Int(3)
    actual_identity = slot_identity(actual)
    saved, events = [], []

    def work(value):
        before = slot_identity(value)
        assert before != actual_identity
        if direction is Output:
            with pytest.raises(CheckedAccessError, match='not been assigned'):
                with relocated_slot(value, allocator(Int(), events)):
                    pytest.fail('unassigned Output reached factory')
            assert events == []
        value.value = 5
        with relocated_slot(value, allocator(Int(), events)) as view:
            assert slot_identity(value) == before == slot_identity(view)
            assert actual.value == 3
            value.value = 9
        assert slot_identity(value) == before
        saved.append(before)

    work.__annotations__ = {'value': direction[Int]}
    with checked_library_sources(__file__):
        run_checked(work, actual)
    assert actual.value == (3 if direction is Input else 9)
    assert not saved[0].is_valid


def test_business_code_cannot_save_or_return_identity():
    def save(value: Ref[Int]):
        saved = slot_identity(value)

    def escape(value: Ref[Int]):
        return slot_identity(value)

    for operation in (save, escape):
        with pytest.raises(CheckedAccessError, match='cannot be stored|returned a variable carrier'):
            run_checked(operation, Int())


def test_existing_and_new_refs_share_one_allocation_and_original_identity():
    actual = Int(2)
    expected = slot_identity(actual)
    events, saved = [], []

    def inner(value: Ref[Int]):
        assert slot_identity(value) == expected
        with relocated_slot(value, allocator(Int(), events)) as nested:
            value.value += 1
            assert nested.value == 12

    def outer(first: Ref[Int], second: Ref[Int]):
        with relocated_slot(first, allocator(Int(), events)) as view:
            view_id = slot_identity(view)
            assert view_id == expected == slot_identity(second)
            first.value = 11
            assert second.value == 11
            run_checked(inner, actual)
            saved.extend((view, view_id))
        assert first.value == second.value == 12

    with checked_library_sources(__file__):
        run_checked(outer, actual, actual)
    assert [event[0] for event in events] == ['create', 'release']
    assert actual.value == 12 and expected.is_valid
    assert not saved[1].is_valid
    with pytest.raises(ExternalStorageClosedError):
        read_value(saved[0])


@pytest.mark.parametrize('codec, initial, changed', [
    (Int(), 1, -9), (Bit[96](), 1, 2 ** 90), (Real(), 1.25, 3.5),
    (String(), 'first', 'last'), (Array[Int, 2](), [1, 2], [3, 4]),
    (DynArray[Int](), [1], [3, 4]), (Queue[Int](), [1], [3, 4]),
    (AssocArray[String, Int](), {'a': 1}, {'b': 2}),
])
def test_typed_roots_restore_latest_value(codec, initial, changed):
    import copy
    actual = copy.deepcopy(codec)
    write_value(actual, initial)
    events = []
    with relocated_slot(actual, allocator(codec, events)):
        write_value(actual, changed)
        assert read_value(actual) == changed
    assert read_value(actual) == changed
    assert [event[0] for event in events] == ['create', 'release']


@pytest.mark.parametrize('error_type', [ValueError, asyncio.CancelledError, KeyboardInterrupt])
def test_body_failure_preserves_completed_writes_and_exact_exception(error_type):
    actual, events = Int(1), []
    error = error_type('body')
    with pytest.raises(error_type) as caught:
        with relocated_slot(actual, allocator(Int(), events)):
            actual.value = 8
            raise error
    assert caught.value is error and actual.value == 8
    assert [event[0] for event in events] == ['create', 'release']


def test_readback_and_release_failures_preserve_body_and_all_causes():
    class Failing(MemoryExternalFieldStorage):
        broken = False

        def read(self, *args):
            if self.broken:
                raise OSError('backend unavailable')
            return super().read(*args)

    storage, actual, releases = Failing(), Int(1), []
    body = ValueError('body failed')

    def create(initial, identity):
        storage.seed('slot', Int(), initial)

        def release():
            releases.append(1)
            raise OSError('release unavailable')
        return storage, 'slot', release

    with pytest.raises(StorageRelocationError) as caught:
        with relocated_slot(actual, create) as view:
            actual.value = 9
            storage.broken = True
            raise body
    error = caught.value
    assert error.phase == 'readback' and not error.restored
    assert error.body_error is body and error.__cause__ is body
    assert [f.phase for f in error.failures] == ['readback', 'release']
    assert 'backend unavailable' in str(error) and 'release unavailable' in str(error)
    assert releases == [1]
    with pytest.raises(ExternalStorageClosedError):
        read_value(view)
    # Failure is explicit: stale original state must never be reported as the latest.
    assert actual.value == 1
    with relocated_slot(actual, allocator(Int(), [])):
        actual.value = 10
    assert actual.value == 10


def test_release_failure_reports_successful_restoration():
    actual = Int(1)
    storage = MemoryExternalFieldStorage()

    def release():
        raise RuntimeError('release failed')

    def create(initial, identity):
        storage.seed('slot', Int(), initial)
        return storage, 'slot', release

    with pytest.raises(StorageRelocationError) as caught:
        with relocated_slot(actual, create):
            actual.value = 7
    assert actual.value == 7
    assert caught.value.phase == 'release' and caught.value.restored
    assert caught.value.body_error is None


def test_invalid_backend_releases_allocation_and_leaves_original_usable():
    actual, releases = Int(4), []
    with pytest.raises(StorageRelocationError) as caught:
        with relocated_slot(actual, lambda value, identity: (object(), 'key', lambda: releases.append(1))):
            pytest.fail('invalid backend entered')
    assert caught.value.phase == 'prepare' and caught.value.restored
    assert releases == [1] and actual.value == 4


def test_same_slot_reentrant_preparation_is_rejected_without_second_allocation():
    actual, events = Int(2), []

    def create(initial, identity):
        with pytest.raises(ExternalStorageError, match='still preparing'):
            with relocated_slot(actual, allocator(Int(), events)):
                pytest.fail('reentered preparing slot')
        return allocator(Int(), events)(initial, identity)

    with relocated_slot(actual, create):
        actual.value = 6
    assert actual.value == 6
    assert [event[0] for event in events] == ['create', 'release']


def test_owner_lookup_keeps_active_relocation():
    class Owner(SvObject):
        count = Int()

    owner, events = Owner(), []
    original = slot_identity(owner.count)
    with relocated_slot(owner.count, allocator(Int(), events)):
        owner.count.value = 9
        assert slot_identity(owner.count) == original
        assert owner.count.value == 9
    assert owner.count.value == 9


class PolicyStorage(MemoryExternalFieldStorage):
    value_arguments_by_path = True

    def __init__(self):
        super().__init__()
        self.cells = [[1], [2]]
        self.events = []
        self.deny_ref = False

    def read(self, key, descriptor, path):
        self.seed(key, descriptor, [cell[0] for cell in self.cells])
        return super().read(key, descriptor, path)

    def write(self, key, descriptor, path, operation, payload):
        self.seed(key, descriptor, [cell[0] for cell in self.cells])
        result = super().write(key, descriptor, path, operation, payload)
        values, _ = Queue[Int]().unpack(super().read(key, descriptor, FieldPath()))
        assert len(values) == len(self.cells)  # This fixture exercises value edits only.
        for cell, value in zip(self.cells, values):
            cell[0] = value
        return result

    def acquire_borrow(self, key, descriptor, path):
        self.events.append(('acquire', current_borrow_purpose()))
        return self.cells[path.segments[0].index]

    def release_borrow(self, token):
        self.events.append(('release', token))

    def read_borrow(self, token):
        return Int().pack(token[0])

    def write_borrow(self, token, payload):
        token[0], _ = Int().unpack(payload)

    def validate_ref_borrow(self, token):
        self.events.append(('validate', current_borrow_purpose()))
        if self.deny_ref:
            raise ExternalStorageError('Ref disabled by backend')


def test_optional_protocols_and_purpose_restore_after_success_and_failure():
    backend = PolicyStorage()
    assert isinstance(backend, ExternalRefValidationStorage)
    assert isinstance(backend, ExternalValueParameterStorage)
    assert not isinstance(MemoryExternalFieldStorage(), ExternalRefValidationStorage)
    assert not isinstance(MemoryExternalFieldStorage(), ExternalValueParameterStorage)
    bound = bind_external_value(Queue[Int](), backend, 'slot')
    entered = []

    def edit(value: Ref[Int]):
        entered.append(1)
        value.value += 1

    def main():
        edit(value=bound[0])

    run_checked(main)
    assert backend.events[:2] == [('acquire', BorrowPurpose.REF), ('validate', BorrowPurpose.REF)]
    assert current_borrow_purpose() is BorrowPurpose.EXPLICIT
    backend.events.clear()
    backend.deny_ref = True
    with pytest.raises(ExternalStorageError, match='Ref disabled'):
        run_checked(main)
    assert backend.events[-1][0] == 'release'
    assert current_borrow_purpose() is BorrowPurpose.EXPLICIT and entered == [1]

    backend.events.clear()
    with borrow_element(bound, 0) as existing:
        assert backend.events == [('acquire', BorrowPurpose.EXPLICIT)]
        with pytest.raises(ExternalStorageError, match='Ref disabled'):
            run_checked(edit, existing)
        assert existing.value == 2
    assert backend.events[-1][0] == 'release'


def test_relocated_backend_preserves_borrow_and_addressed_copyout_capabilities():
    codec = Queue[Int]()
    actual = Queue[Int]()
    actual.value = [1, 2]
    storage = PolicyStorage()
    events = []

    def outputs(first: Output[Int], second: Inout[Int]):
        first.value = 4
        second.value += 1

    def edit():
        outputs(actual[0], actual[1])

    with relocated_slot(actual, allocator(codec, events, storage)):
        run_checked(edit)
        assert read_value(actual) == [4, 3]
        # No hidden stable lease for value arguments.
        assert storage.events == []
        with borrow_element(actual, 0) as selected:
            assert selected.value == 4
            selected.value = 7
            assert storage.cells[0] == [7]
    assert read_value(actual) == [7, 3]


def test_restore_failure_is_distinct_from_readback_and_still_releases():
    class FallibleInt(Int):
        reject_restore = False

        @property
        def value(self):
            return Int.value.fget(self)

        @value.setter
        def value(self, value):
            if self.reject_restore:
                raise ValueError('local assignment failed')
            Int.value.fset(self, value)

    actual, events = FallibleInt(), []
    actual.value = 2
    with pytest.raises(StorageRelocationError) as caught:
        with relocated_slot(actual, allocator(FallibleInt(), events)):
            actual.value = 8
            actual.reject_restore = True
    assert caught.value.phase == 'restore' and not caught.value.restored
    assert 'local assignment failed' in str(caught.value)
    assert [event[0] for event in events] == ['create', 'release']
    actual.reject_restore = False
    actual.value = 3
    assert actual.value == 3


def test_plain_operation_purpose_is_not_ref_and_context_does_not_leak():
    from contextvars import Context

    class Observed(PolicyStorage):
        def acquire_borrow(self, *args):
            assert Context().run(current_borrow_purpose) is BorrowPurpose.EXPLICIT
            return super().acquire_borrow(*args)

    storage = Observed()
    storage.deny_ref = True
    bound = bind_external_value(Queue[Int](), storage, 'slot')

    def main():
        bound[0].value += 3

    run_checked(main)
    assert storage.cells[0] == [4]
    assert storage.events[0] == ('acquire', BorrowPurpose.OPERATION)
    assert not any(event[0] == 'validate' for event in storage.events)
    assert current_borrow_purpose() is BorrowPurpose.EXPLICIT


def test_explicit_scope_and_validation_are_kept_on_forwarded_ref():
    storage = PolicyStorage()
    bound = bind_external_value(Queue[Int](), storage, 'slot')

    class Operations:
        def inner(self, value: Ref[Int]):
            value.value += 1

        def outer(self, value: Ref[Int]):
            self.inner(value)

    with borrow_element(bound, 0) as lease:
        run_checked(Operations().outer, lease)
        assert lease.value == 2
    assert [event[0] for event in storage.events] == ['acquire', 'validate', 'validate', 'release']


def test_independent_equal_roots_do_not_reuse_allocation():
    first, second, events = Int(3), Int(3), []
    with relocated_slot(first, allocator(Int(), events)):
        with relocated_slot(second, allocator(Int(), events)):
            first.value = 7
            second.value = 9
            assert first.value == 7
    assert first.value == 7 and second.value == 9
    assert [event[0] for event in events] == ['create', 'create', 'release', 'release']


def test_existing_external_slot_uses_existing_endpoint_and_never_allocates():
    storage, calls = MemoryExternalFieldStorage(), []
    storage.seed('slot', Int(), 1)
    bound = bind_external_value(Int(), storage, 'slot', readonly=True)
    with pytest.raises(TypeError, match='external_storage_location'):
        slot_identity(bound)
    with pytest.raises(TypeError, match='external_storage_location'):
        with relocated_slot(bound, allocator(Int(), calls)):
            pytest.fail('existing external storage was relocated')
    assert calls == []
    with pytest.raises(ExternalStorageError, match='read-only'):
        bound.value = 2
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        slot_identity(bound)


def test_borrowed_aggregate_adapter_preserves_addressed_value_parameter_policy():
    class Aggregate(MemoryExternalFieldStorage):
        value_arguments_by_path = True

        def __init__(self):
            super().__init__()
            self.active = {}

        def acquire_borrow(self, key, descriptor, path):
            token = object()
            self.active[token] = key, descriptor, path
            return token

        def read_borrow(self, token):
            return self.read(*self.active[token])

        def write_borrow(self, token, payload):
            from svtypes import FieldOperation
            return self.write(*self.active[token], FieldOperation.SET, payload)

        def release_borrow(self, token):
            del self.active[token]

        def read_borrow_path(self, token, path):
            key, descriptor, prefix = self.active[token]
            return self.read(key, descriptor, FieldPath((*prefix.segments, *path.segments)))

        def write_borrow_path(self, token, path, operation, payload):
            key, descriptor, prefix = self.active[token]
            return self.write(key, descriptor, FieldPath((*prefix.segments, *path.segments)), operation, payload)

    storage, codec = Aggregate(), Array[Queue[Int], 1]()
    storage.seed('slot', codec, [[1, 2]])
    bound = bind_external_value(codec, storage, 'slot')

    def outputs(first: Output[Int], second: Inout[Int]):
        first.value = 8
        second.value += 1

    def edit(values: Ref[Queue[Int]]):
        outputs(values[0], values[1])

    def main():
        edit(bound[0])

    run_checked(main)
    assert read_value(bound) == [[8, 3]] and not storage.active


def test_value_formal_aggregate_keeps_child_overlap_checks(tmp_path):
    events = []

    def attempt(parent: Ref[Queue[Int]], child: Ref[Int]):
        with pytest.raises(ExternalStorageError, match='active child borrow or Ref'):
            with relocated_slot(parent, allocator(Queue[Int](), events)):
                pytest.fail('active value-formal child was detached')
        child.value += 1
        assert parent.value == [4]

    # Separate user source and native framework source, just as an integration
    # does, so only the framework may hold storage scopes and metadata.
    source = tmp_path / 'business.py'
    source.write_text('def work(values: Input[Queue[Int]]):\n    attempt(values, values[0])\n    return values.value\n')
    namespace = dict(Input=Input, Queue=Queue, Int=Int, attempt=attempt)
    exec(compile(source.read_text(), str(source), 'exec'), namespace)
    actual = Queue[Int]()
    actual.value = [3]
    with checked_library_sources(__file__):
        assert run_checked(namespace['work'], actual) == [4]
    assert actual.value == [3] and events == []


def test_relocated_location_exposes_public_backend_without_adapter_internals():
    from svtypes import external_storage_location
    actual, events, backend = Int(2), [], MemoryExternalFieldStorage()
    with relocated_slot(actual, allocator(Int(), events, backend)):
        location = external_storage_location(actual)
        assert location.storage is backend
        assert location.key == 'slot' and location.path == FieldPath()
    with pytest.raises(ExternalStorageClosedError):
        _ = location.storage


def test_packed_enum_and_remote_reference_roots_relocate_without_encoding_changes():
    from svtypes import Enum, Logic, RemoteRef, RemoteRefValue, SvPackedStruct

    class Color(Enum[Bit[8]]):
        RED = 1
        BLUE = 2

    class Packed(SvPackedStruct):
        tag = Bit[4]()
        count = Bit[4]()

    cases = [
        (Color(), 1, 2),
        (Logic[4](), "10xz", "z001"),
        (RemoteRef['acme.Node'](), RemoteRefValue('acme.Node', 0), RemoteRefValue('acme.Node', 17)),
        (Packed(), {'tag': 1, 'count': 2}, {'tag': 3, 'count': 4}),
    ]
    for actual, initial, changed in cases:
        write_value(actual, initial)
        events = []
        # The public memory backend accepts the struct's typed value input.
        def create(value, identity):
            import copy
            codec = copy.deepcopy(actual)
            write_value(codec, value)
            backend = MemoryExternalFieldStorage()
            backend.seed('slot', codec, codec.value)
            return backend, 'slot', lambda: events.append('release')

        with relocated_slot(actual, create):
            write_value(actual, changed)
            expected = read_value(actual)
        assert read_value(actual) == expected and events == ['release']
