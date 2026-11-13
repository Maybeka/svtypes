"""Identity, value-copy and scoped relocation review regressions."""

import gc
import weakref

import pytest

from svtypes import (
    Array, AssocArray, CodecSession, DynArray, ExternalStorageClosedError,
    ExternalStorageError, FieldIdentity, FieldPath, Int, MemoryExternalFieldStorage,
    Object, ObjectRegistry, Queue, Ref, String, SvObject, SvStruct,
    bind_external_value, borrow_element, checked_library_sources, field,
    read_value, run_checked, write_value,
)
from svtypes import relocated_slot, slot_identity


_registry = ObjectRegistry()


class Child(SvObject):
    count = Int()


_registry.register(Child)
_handle = Object['Child'](registry=_registry)


class Record(SvStruct):
    child = Object['Child'](registry=_registry)
    children = Queue[_handle]()


def factory(codec, events):
    def allocate(initial, identity):
        value = initial
        if isinstance(codec, SvStruct):
            value = type(codec).codec_template()
            write_value(value, initial)
        storage = MemoryExternalFieldStorage()
        storage.seed('slot', codec, value)
        events.append(('allocate', identity))
        return storage, 'slot', lambda: events.append(('release', identity))
    return allocate


@pytest.mark.parametrize('kind', ['array', 'dynamic', 'queue', 'mapping'])
def test_external_collection_views_resolve_handles_without_copying_or_replay(kind):
    session = CodecSession()
    child = Child(session=session)
    child.count.value = 4
    codec = {
        'array': Array[_handle, 3], 'dynamic': DynArray[_handle],
        'queue': Queue[_handle], 'mapping': AssocArray[String, _handle],
    }[kind]()
    mapping = kind == 'mapping'
    initial = {'first': child, 'null': None, 'shared': child} if mapping else [child, None, child]
    storage = MemoryExternalFieldStorage()
    storage.seed('slot', codec, initial)
    child.count.value = 45
    identities = dict(session._objects)
    bound = bind_external_value(codec, storage, 'slot', session=session)
    view = bound.value
    first = 'first' if mapping else 0
    assert bound[first].value is view[first] is child
    if mapping:
        assert dict(view) == initial
    else:
        assert list(view) == view[:] == [child, None, child]
    assert child.count.value == 45
    assert dict(session._objects) == identities
    view[first].count.value = 46
    assert child.count.value == 46
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        _ = view[first]
    assert child.count.value == 46


def test_struct_views_keep_nested_handle_identity_and_write_permissions():
    session = CodecSession()
    child = Child(session=session)
    record = Record()
    write_value(record, {'child': child, 'children': [child]})
    storage = MemoryExternalFieldStorage()
    storage.seed('slot', Queue[Record](), [record])
    child.count.value = 42
    bound = bind_external_value(Queue[Record](), storage, 'slot', session=session, readonly=True)
    view = bound.value[0]
    assert view.child.value is view.children.value[0] is child
    assert child.count.value == 42
    with pytest.raises(ExternalStorageError, match='read-only'):
        view.child.value = None
    with pytest.raises(ExternalStorageError, match='read-only'):
        view.children.value[0] = None
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        _ = view.child.value


def test_nested_struct_values_can_be_assigned_through_collection_views():
    session = CodecSession()
    child = Child(session=session)
    record = Record()
    write_value(record, {'child': child, 'children': [child]})
    codec = Queue[Queue[Record]]()
    storage = MemoryExternalFieldStorage()
    storage.seed('slot', codec, [[]])
    bound = bind_external_value(codec, storage, 'slot', session=session)
    bound.value[0] = [record]
    assert bound.value[0][0].child.value is child


def test_collection_view_writes_and_pop_keep_session_and_validate_before_publication():
    from svtypes import FieldOperation

    class ReturningStorage(MemoryExternalFieldStorage):
        def write(self, key, descriptor, path, operation, payload):
            removed = self.read(key, descriptor, path) if operation is FieldOperation.POP else None
            super().write(key, descriptor, path, operation, payload)
            return removed

    session = CodecSession()
    first, second = Child(session=session), Child(session=session)
    other_session = CodecSession()
    foreign = Child(session=other_session)
    storage = ReturningStorage()
    codec = Queue[_handle]()
    storage.seed('slot', codec, [first, None])
    bound = bind_external_value(codec, storage, 'slot', session=session)
    view = bound.value
    first.count.value = 41
    view[1] = second
    view.append(first)
    assert list(view) == [first, second, first]
    assert view.pop() is first
    assert first.count.value == 41
    with pytest.raises(ExternalStorageError, match='handle session'):
        view[:] = [None, foreign]
    assert list(view) == [first, second]


@pytest.mark.parametrize('external_destination', [False, True])
@pytest.mark.parametrize('separate_session', [False, True])
def test_object_value_copy_reads_external_handles_and_preserves_graph_copy_semantics(external_destination, separate_session):
    class Owner(SvObject):
        left = Object['Child'](registry=_registry)
        right = Object['Child'](registry=_registry)

    session = CodecSession()
    child = Child(session=session)
    child.count.value = 17
    storage = MemoryExternalFieldStorage()
    for key in ('left', 'right'):
        storage.seed(key, getattr(Owner, key), child)
    source = Owner(session=session)
    source.bind_external_storage(storage, {FieldIdentity(Owner, key): key for key in ('left', 'right')})
    child.count.value = 33
    destination_session = CodecSession() if separate_session else session
    destination = Owner(session=destination_session)
    if external_destination:
        for key in ('left', 'right'):
            storage.seed('out_' + key, getattr(Owner, key), None)
        destination.bind_external_storage(storage, {FieldIdentity(Owner, key): 'out_' + key for key in ('left', 'right')})
    destination.value = source
    assert destination.left.value is destination.right.value
    assert destination.left.value is not child
    assert destination.left.value.count.value == 33
    assert destination.left.value.codec_session is destination_session
    source.left.value = None
    destination.value = source
    assert destination.left.value is None
    assert destination.right.value.count.value == 33


@pytest.mark.parametrize('kind', ['array', 'dynamic', 'queue', 'mapping', 'struct'])
def test_aggregate_relocation_rejects_live_child_borrow_before_allocation(kind):
    class Numbers(SvStruct):
        data = Queue[Int]()

    if kind == 'struct':
        actual = Numbers()
        write_value(actual, {'data': [1, 2]})
        parent, key = actual.data, 0
    elif kind == 'mapping':
        actual = AssocArray[String, Int]()
        write_value(actual, {'a': 1, 'b': 2})
        parent, key = actual, 'a'
    else:
        actual = {'array': Array[Int, 2], 'dynamic': DynArray[Int], 'queue': Queue[Int]}[kind]()
        write_value(actual, [1, 2])
        parent, key = actual, 0
    events = []
    selected = borrow_element(parent, key)
    forwarded = selected.forward()
    selected.close()
    with pytest.raises(ExternalStorageError, match='active child borrow or Ref'):
        with relocated_slot(actual, factory(actual, events)):
            pytest.fail('overlapping relocation entered')
    assert events == []
    forwarded.value = 9
    assert read_value(parent[key]) == 9
    forwarded.close()
    with relocated_slot(actual, factory(actual, events)):
        assert read_value(actual) == ({'data': [9, 2]} if kind == 'struct' else {'a': 9, 'b': 2} if kind == 'mapping' else [9, 2])
    assert [event[0] for event in events] == ['allocate', 'release']


def test_aggregate_relocation_rejects_active_child_ref_without_an_explicit_borrow():
    values = Queue[Int]()
    write_value(values, [1, 2])
    events = []

    def work(element: Ref[Int]):
        with relocated_slot(values, factory(values, events)):
            raise AssertionError('body entered')

    with checked_library_sources(__file__):
        with pytest.raises(ExternalStorageError, match='active child borrow or Ref'):
            run_checked(work, values[0])
    assert events == []
    with relocated_slot(values, factory(values, events)):
        write_value(values, [3, 4])
    assert read_value(values) == [3, 4]


def test_detached_element_borrow_does_not_block_unrelated_aggregate_relocation():
    values = Queue[Int]()
    write_value(values, [1, 2])
    with borrow_element(values, 0) as detached:
        values.pop_front()
        with relocated_slot(values, factory(values, [])):
            write_value(values, [3])
            detached.value = 9
            assert read_value(values) == [3]
        assert detached.value == 9


def test_element_relocation_still_follows_existing_borrows_and_nested_scopes():
    values = Queue[Int]()
    write_value(values, [1, 2])
    events = []
    with borrow_element(values, 0) as first, borrow_element(values, 0) as second:
        assert slot_identity(first) == slot_identity(second)
        with relocated_slot(first, factory(Int(), events)) as outer:
            with relocated_slot(second, factory(Int(), events)) as inner:
                assert inner is outer
                first.value = 9
                assert second.value == 9
                assert read_value(values) == [9, 2]
        assert first.value == second.value == 9
    assert [event[0] for event in events] == ['allocate', 'release']


def test_abandoned_local_borrow_does_not_retain_its_owner_cycle():
    values = Queue[Int]()
    write_value(values, [1])
    selected = borrow_element(values, 0)
    values[0].saved_lease = selected
    reference = weakref.ref(values[0])
    del values, selected
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize('exception', [False, True])
def test_relocation_keeps_handles_and_their_explicit_session_without_cloning(exception):
    session = CodecSession()
    child, replacement = Child(session=session), Child(session=session)
    child.count.value = 45
    actual = Record()
    write_value(actual, {'child': child, 'children': [child]})
    events = []
    identities = dict(session._objects)
    try:
        with relocated_slot(actual, factory(Record(), events)) as view:
            assert view.value.child.value is child
            assert read_value(view)['children'][0] is child
            assert session._objects == identities
            view.value.child.value.count.value = 99
            write_value(view, {'child': replacement, 'children': [child, replacement]})
            assert read_value(actual)['child'] is replacement
            if exception:
                raise ValueError('body')
    except ValueError:
        assert exception
    assert read_value(actual) == {'child': replacement, 'children': [child, replacement]}
    assert child.count.value == 99
    assert session._objects == identities
    assert [event[0] for event in events] == ['allocate', 'release']
    with pytest.raises(ExternalStorageClosedError):
        read_value(view)


def test_relocation_rejects_mixed_handle_sessions_before_allocating():
    first, second = Child(session=CodecSession()), Child(session=CodecSession())
    actual = Record()
    write_value(actual, {'child': first, 'children': [second]})
    events = []
    with pytest.raises(ExternalStorageError, match='different codec sessions'):
        with relocated_slot(actual, factory(Record(), events)):
            pytest.fail('mixed sessions reached backend')
    assert read_value(actual) == {'child': first, 'children': [second]}
    assert events == []


def test_aggregate_relocation_does_not_replace_independent_external_handle_storage():
    actual = Record()
    storage = MemoryExternalFieldStorage()
    storage.seed('child', Record.child, None)
    actual.bind_external_storage(storage, {FieldIdentity(Record, 'child'): 'child'})
    events = []
    with pytest.raises(ExternalStorageError, match='externally bound child storage'):
        with relocated_slot(actual, factory(Record(), events)):
            pytest.fail('independent child binding was replaced')
    assert events == []
    assert actual.child.value is None
