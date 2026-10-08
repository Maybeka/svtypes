from __future__ import annotations

import copy

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    ExternalStorageClosedError,
    ExternalStorageError,
    FieldIdentity,
    FieldOperation,
    FieldPath,
    Int,
    MemoryExternalFieldStorage,
    Object,
    ObjectRegistry,
    Queue,
    RemoteRef,
    String,
    SvObject,
    SvStruct,
    bind_external_value,
    svobj,
)


class RecordingStorage(MemoryExternalFieldStorage):
    def __init__(self) -> None:
        super().__init__()
        self.operations: list[tuple[object, object, FieldOperation]] = []

    def write(self, key, descriptor, path, operation, payload):
        self.operations.append((key, path, operation))
        return super().write(key, descriptor, path, operation, payload)


@svobj
class ExternalInner(SvStruct):
    code = Bit[8]()


@svobj
class ExternalPacket(SvObject):
    count = Bit[8]()
    words = Array[Bit[8], 3]()
    queue = Queue[Int]()
    labels = AssocArray[String, Bit[8]]()
    inner = ExternalInner()
    local = Bit[8]()


def _bound_packet(storage: RecordingStorage) -> ExternalPacket:
    for key, name, value in (
        ("count", "count", 1),
        ("words", "words", [2, 3, 4]),
        ("queue", "queue", [5]),
        ("labels", "labels", {"mode": 6}),
        ("inner", "inner", ExternalInner()),
    ):
        if name == "inner":
            value.code.value = 7
        storage.seed(key, ExternalPacket.__dict__[name], value)
    packet = ExternalPacket()
    packet.local.value = 9
    packet.bind_external_storage(
        storage,
        {
            FieldIdentity(ExternalPacket, "count"): "count",
            FieldIdentity(ExternalPacket, "words"): "words",
            FieldIdentity(ExternalPacket, "queue"): "queue",
            FieldIdentity(ExternalPacket, "labels"): "labels",
            FieldIdentity(ExternalPacket, "inner"): "inner",
        },
    )
    return packet


def test_owner_binding_preserves_normal_fields_and_uses_normalization() -> None:
    storage = RecordingStorage()
    external = _bound_packet(storage)
    local = ExternalPacket()

    assert external.count.value == 1
    external.count.value = 0x123
    assert external.count.value == 0x23
    assert local.count.value == 0
    assert external.local.value == 9
    assert storage.operations[-1][2] is FieldOperation.SET


def test_nested_container_access_retains_owner_path_for_local_mutation() -> None:
    storage = RecordingStorage()
    descriptor = AssocArray[String, Queue[Int]]()
    storage.seed('nested', descriptor, {'first': [1, 2], 'sibling': [3]})
    bound = bind_external_value(descriptor, storage, 'nested')
    child = bound.value['first']
    storage.operations.clear()
    child[1] = 9
    assert list(bound.value['first']) == [1, 9]
    assert list(bound.value['sibling']) == [3]
    _, path, operation = storage.operations[-1]
    assert path.segments[0] == FieldPath().key(String(), 'first').segments[0]
    assert path.segments[1].index == 1
    assert operation is FieldOperation.SET
    child.append(10)
    assert bound.value == {'first': [1, 9, 10], 'sibling': [3]}
    assert storage.operations[-1][1] == FieldPath().key(String(), 'first')
    assert storage.operations[-1][2] is FieldOperation.APPEND


def test_iterated_nested_sequences_keep_addressed_binding() -> None:
    storage = RecordingStorage()
    descriptor = Queue[Queue[Int]]()
    storage.seed('matrix', descriptor, [[1, 2], [3, 4]])
    bound = bind_external_value(descriptor, storage, 'matrix')
    rows = list(bound.value)
    storage.operations.clear()
    rows[1][0] = 8
    assert list(bound.value) == [[1, 2], [8, 4]]
    _, path, operation = storage.operations[-1]
    assert [segment.index for segment in path.segments] == [1, 0]
    assert operation is FieldOperation.SET


def test_insert_nested_container_uses_addressed_element_codec() -> None:
    storage = RecordingStorage()
    descriptor = Queue[Queue[Int]]()
    storage.seed('matrix', descriptor, [[1, 2], []])
    bound = bind_external_value(descriptor, storage, 'matrix')
    storage.operations.clear()
    bound.value.insert(1, [3, 4])
    assert [list(row) for row in bound.value] == [[1, 2], [3, 4], []]
    assert storage.operations == [('matrix', FieldPath().index(1), FieldOperation.INSERT)]
    bound.value[1].insert(0, 5)
    assert [list(row) for row in bound.value] == [[1, 2], [5, 3, 4], []]
    assert storage.operations[-1] == ('matrix', FieldPath().index(1).index(0), FieldOperation.INSERT)


def test_none_handle_insertion_is_encoded_value_not_missing_payload() -> None:
    storage = RecordingStorage()
    descriptor = Queue[RemoteRef['sv://test/Item']]()
    storage.seed('handles', descriptor, [])
    bound = bind_external_value(descriptor, storage, 'handles')
    bound.value.append(None)
    bound.value.insert(0, None)
    assert [reference.object_number for reference in bound.value] == [0, 0]
    assert [operation for _, _, operation in storage.operations] == [FieldOperation.APPEND, FieldOperation.INSERT]


def test_external_slices_write_only_addressed_elements() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)
    packet.queue.value = [1, 2, 3, 4, 5]
    storage.operations.clear()
    packet.queue.value[1:3] = [21, 22, 23]
    assert list(packet.queue.value) == [1, 21, 22, 23, 4, 5]
    assert [path.segments[-1].index for _, path, _ in storage.operations] == [1, 2, 3]
    assert [operation for _, _, operation in storage.operations] == [
        FieldOperation.SET, FieldOperation.SET, FieldOperation.INSERT]
    storage.operations.clear()
    packet.queue.value[1:4] = [31]
    assert list(packet.queue.value) == [1, 31, 4, 5]
    assert [operation for _, _, operation in storage.operations] == [
        FieldOperation.SET, FieldOperation.DELETE, FieldOperation.DELETE]
    storage.operations.clear()
    packet.queue.value[::-2] = [51, 41]
    assert list(packet.queue.value) == [1, 41, 4, 51]
    assert [path.segments[-1].index for _, path, _ in storage.operations] == [3, 1]
    storage.operations.clear()
    del packet.queue.value[::2]
    assert list(packet.queue.value) == [41, 51]
    assert [path.segments[-1].index for _, path, _ in storage.operations] == [2, 0]
    assert all(operation is FieldOperation.DELETE for _, _, operation in storage.operations)
    storage.operations.clear()
    packet.words.value[0:2] = [0x102, 0x103]
    assert list(packet.words.value) == [2, 3, 4]
    assert [path.segments[-1].index for _, path, _ in storage.operations] == [0, 1]


def test_invalid_external_slice_shape_does_not_issue_writes() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)
    for field, index, values in ((packet.words, slice(0, 1), [1, 2]),
                                  (packet.queue, slice(None, None, 2), [1, 2])):
        storage.operations.clear()
        with pytest.raises(ValueError):
            field.value[index] = values
        assert storage.operations == []
    assert list(packet.words.value) == [2, 3, 4]
    assert list(packet.queue.value) == [5]


def test_external_collections_use_leaf_operations_and_live_views() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)

    packet.words[1].value = 0x1FF
    assert list(packet.words.value) == [2, 0xFF, 4]
    assert storage.operations[-1][1].segments[-1].index == 1

    packet.queue.value.append(8)
    assert list(packet.queue.value) == [5, 8]
    assert storage.operations[-1][2] is FieldOperation.APPEND

    packet.labels.value["mode"] = 11
    assert packet.labels["mode"].value == 11
    del packet.labels.value["mode"]
    assert dict(packet.labels.value) == {}


def test_external_nested_path_pack_and_copy_semantics() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)

    packet.inner.code.value = 0x101
    assert packet.inner.code.value == 1
    encoded = packet.pack(packet)

    target = ExternalPacket()
    target.bind_external_storage(
        storage,
        {FieldIdentity(ExternalPacket, name): name for name in ("count", "words", "queue", "labels", "inner")},
    )
    target.from_bytes(encoded)
    assert target.inner.code.value == 1

    copied = copy.deepcopy(packet)
    copied.count.value = 33
    assert copied.count.value == 33
    assert packet.count.value != 33


def test_unbind_and_temporary_value_binding_lifetime() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)
    packet.unbind_external_storage()
    assert packet.count.value == 0

    descriptor = Bit[4]()
    storage.seed("temporary", descriptor, 2)
    bound = bind_external_value(descriptor, storage, "temporary")
    assert bound.value == 2
    bound.value = 31
    assert bound.value == 15
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        _ = bound.value


def test_backend_failure_never_falls_back_to_local_value() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)
    storage._roots.clear()
    with pytest.raises(ExternalStorageError):
        _ = packet.count.value


def test_external_randomization_commits_only_after_a_successful_solve() -> None:
    storage = RecordingStorage()
    packet = _bound_packet(storage)
    storage.operations.clear()

    assert packet.randomize()
    count_writes = [item for item in storage.operations if item[0] == "count"]
    assert len(count_writes) == 1
    assert count_writes[0][2] is FieldOperation.SET


def test_external_randomization_does_not_repeat_required_constructor_or_register_trials():
    from svtypes import CodecSession, constraint

    constructed = []

    @svobj
    class RequiredConstructor(SvObject):
        count = Bit[8]()

        @constraint
        def legal(self):
            self.count <= 7

        def __init__(self, seed, session):
            super().__init__(session=session)
            constructed.append(seed)
            self.count.value = seed

    session = CodecSession()
    packet = RequiredConstructor(3, session)
    storage = RecordingStorage()
    storage.seed("count", RequiredConstructor.count, 3)
    packet.bind_external_storage(storage, {FieldIdentity(RequiredConstructor, "count"): "count"})
    identities = set(session._objects)
    assert packet.randomize()
    assert 0 <= packet.count.value <= 7
    assert constructed == [3]
    assert set(session._objects) == identities
    duplicate = copy.deepcopy(packet)
    assert duplicate.count.value == packet.count.value
    assert constructed == [3]
    assert duplicate.svtypes_object_number != packet.svtypes_object_number


def test_external_randc_cycle_survives_mode_pause():
    from svtypes import RandomContext

    class Cycle(SvObject):
        choice = Bit[2](randc=True)

    packet = Cycle()
    storage = RecordingStorage()
    storage.seed('choice', Cycle.choice, 0)
    packet.bind_external_storage(storage, {FieldIdentity(Cycle, 'choice'): 'choice'})
    with RandomContext(seed=401):
        values = []
        for _ in range(2):
            assert packet.randomize()
            values.append(packet.choice.value)
        before = copy.deepcopy(packet._SvObject__svtypes_randc_state)
        assert before
        packet.choice.rand_mode(0)
        assert packet.randomize()
        assert packet.choice.value == values[-1]
        assert packet._SvObject__svtypes_randc_state == before
        packet.choice.rand_mode(1)
        for _ in range(2):
            assert packet.randomize()
            values.append(packet.choice.value)
    assert set(values) == {0, 1, 2, 3}


def test_external_unsat_does_not_consume_randc_history():
    from svtypes import RandomContext, constraint

    class LimitedCycle(SvObject):
        choice = Bit[2](randc=True)
        limit = Bit[3](rand=False)

        @constraint
        def legal(self):
            self.choice < self.limit

    packet = LimitedCycle()
    packet.limit.value = 3
    storage = RecordingStorage()
    storage.seed('choice', LimitedCycle.choice, 0)
    packet.bind_external_storage(storage, {FieldIdentity(LimitedCycle, 'choice'): 'choice'})
    with RandomContext(seed=402):
        values = []
        for _ in range(2):
            assert packet.randomize()
            values.append(packet.choice.value)
        before = copy.deepcopy(packet._SvObject__svtypes_randc_state)
        assert before
        held = packet.choice.value
        packet.limit.value = 0
        storage.operations.clear()
        assert packet.randomize() is False
        assert packet.choice.value == held
        assert packet._SvObject__svtypes_randc_state == before
        assert storage.operations == []
        packet.limit.value = 3
        assert packet.randomize()
        values.append(packet.choice.value)
    assert set(values) == {0, 1, 2}


def test_external_randomize_callbacks_keep_receiver_and_python_state():
    from svtypes import constraint

    class Packet(SvObject):
        count = Bit[4]()
        gate = Bit[4](rand=False)

        @constraint
        def selected(self):
            self.count == self.gate

        def __init__(self):
            super().__init__()
            self.audit = []

        def pre_randomize(self):
            self.audit.append(('pre', self.count.value))
            self.gate.value = 3

        def post_randomize(self):
            self.audit.append(('post', self.count.value))

    packet = Packet()
    storage = RecordingStorage()
    storage.seed('count', Packet.count, 0)
    packet.bind_external_storage(storage, {FieldIdentity(Packet, 'count'): 'count'})
    assert packet.randomize()
    assert packet.count.value == 3
    assert packet.audit == [('pre', 0), ('post', 3)]


def test_external_unsat_calls_original_pre_without_post():
    from svtypes import constraint

    seen = []

    class Packet(SvObject):
        count = Bit[4]()

        @constraint
        def impossible(self):
            self.count == 1
            self.count == 2

        def pre_randomize(self):
            seen.append(('pre', self))

        def post_randomize(self):
            seen.append(('post', self))

    packet = Packet()
    storage = RecordingStorage()
    storage.seed('count', Packet.count, 7)
    packet.bind_external_storage(storage, {FieldIdentity(Packet, 'count'): 'count'})
    assert packet.randomize() is False
    assert seen == [('pre', packet)]
    assert packet.count.value == 7
    assert storage.operations == []


def test_external_layered_callbacks_observe_original_stage_context():
    from svtypes import rand_layer

    seen = []

    class Packet(SvObject):
        high = Bit[4]()
        low = Bit[4]()

        @rand_layer(5)
        def high_layer(self):
            self.high

        @rand_layer(-2)
        def low_layer(self):
            self.low

        def pre_randomize(self):
            seen.append(('pre', self, self.svtypes_layered_randomize_active(), self.svtypes_layered_randomize_priority()))

        def post_randomize(self):
            seen.append(('post', self, self.svtypes_layered_randomize_active(), self.svtypes_layered_randomize_priority()))

    packet = Packet()
    storage = RecordingStorage()
    storage.seed('high', Packet.high, 0)
    storage.seed('low', Packet.low, 0)
    packet.bind_external_storage(storage, {
        FieldIdentity(Packet, 'high'): 'high', FieldIdentity(Packet, 'low'): 'low',
    })
    assert packet.layered_randomize()
    assert seen == [(kind, packet, True, priority) for priority in (5, 0, -2) for kind in ('pre', 'post')]
    assert packet.svtypes_layered_randomize_active() is False
    assert packet.svtypes_layered_randomize_priority() == 0


def test_external_random_graph_keeps_child_callbacks_and_randc_history():
    from svtypes import RandomContext

    registry = ObjectRegistry()
    seen = []

    @svobj(registry=registry)
    class Child(SvObject):
        choice = Bit[2](randc=True)

        def pre_randomize(self):
            seen.append(('child_pre', self))

        def post_randomize(self):
            seen.append(('child_post', self))

    @svobj(registry=registry)
    class Parent(SvObject):
        tag = Bit[1](rand=False)
        child = Object[Child](registry=registry, rand=True)

        def pre_randomize(self):
            seen.append(('parent_pre', self))

        def post_randomize(self):
            seen.append(('parent_post', self))

    child = Child()
    parent = Parent()
    parent.child = child
    storage = RecordingStorage()
    storage.seed('tag', Parent.tag, 0)
    parent.bind_external_storage(storage, {FieldIdentity(Parent, 'tag'): 'tag'})
    with RandomContext(seed=401):
        values = []
        for _ in range(4):
            assert parent.randomize()
            assert parent.child is child
            values.append(child.choice.value)
    assert set(values) == {0, 1, 2, 3}
    assert seen == [('parent_pre', parent), ('child_pre', child),
                    ('child_post', child), ('parent_post', parent)] * 4


def test_external_object_handle_uses_a_detached_owner_view() -> None:
    registry = ObjectRegistry()

    @svobj
    class Child(SvObject):
        value = Bit[8]()

    registry.register(Child)

    @svobj
    class Parent(SvObject):
        child = Object["Child"](registry=registry)

    storage = RecordingStorage()
    child = Child()
    child.value.value = 4
    storage.seed("child", Parent.__dict__["child"], child)
    parent = Parent()
    parent.bind_external_storage(storage, {FieldIdentity(Parent, "child"): "child"})

    parent.child.value.value = 8
    assert parent.child.value.value == 8
