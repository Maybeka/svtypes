"""Regression coverage for external values and constructor-free codec use."""

import copy
import threading

import pytest

from svtypes import (
    AssocArray, Bit, CodecSession, ExternalStorageClosedError, FieldIdentity,
    FieldOperation, FieldPath, Int, MemoryExternalFieldStorage, Object,
    ObjectRegistry, Queue, RandomContext, String, SvObject, SvStruct,
    bind_external_value, svobj,
)


class UncopyableKey:
    def __deepcopy__(self, memo):
        raise AssertionError("opaque backend keys must never be copied")


class LockedStorage(MemoryExternalFieldStorage):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.writes = []

    def write(self, key, descriptor, path, operation, payload):
        self.writes.append((key, path, operation))
        return super().write(key, descriptor, path, operation, payload)


@pytest.mark.parametrize("descriptor, values", [
    (Queue[Queue[Int]](rand=False, cov=False), [[1, 2], [3]]),
    (AssocArray[String, Queue[Int]](rand=False, cov=False), {"row": [1, 2]}),
    (Queue[AssocArray[String, Int]](rand=False, cov=False), [{"a": 1}, {"b": 2}]),
])
def test_nested_external_snapshot_never_copies_backend_or_key(descriptor, values):
    class Packet(SvObject):
        count = Bit[2]()
        rows = descriptor

    storage, key = LockedStorage(), UncopyableKey()
    storage.seed(key, descriptor, values)
    packet = Packet()
    packet.bind_external_storage(storage, {FieldIdentity(Packet, "rows"): key})
    cloned = copy.deepcopy(packet)
    assert cloned.rows.value == values
    assert cloned._SvObject__svtypes_external_storage is None
    assert storage.writes == []
    with RandomContext(seed=401):
        assert packet.randomize()
    assert packet.rows.value == values
    assert storage.writes == [(key, FieldPath(), FieldOperation.SET)]
    storage.seed(key, descriptor, values)
    assert cloned.rows.value == values


def test_external_randomization_publishes_null_and_shared_container_handles():
    registry, callbacks = ObjectRegistry(), []

    @svobj(registry=registry)
    class Child(SvObject):
        choice = Bit[2](randc=True)

        def post_randomize(self):
            callbacks.append(self)

    @svobj(registry=registry)
    class Parent(SvObject):
        count = Bit[2]()
        absent = Object[Child](registry=registry, rand=False)
        child = Object[Child](registry=registry, rand=True)
        shared = Object[Child](registry=registry, rand=True)
        children = Queue[Object[Child](registry=registry, rand=True)](rand=True, cov=False)

        def post_randomize(self):
            callbacks.append(self)

    storage, parent, child = LockedStorage(), Parent(), Child()
    storage.seed("count", Parent.count, 0)
    parent.absent = None
    parent.child = parent.shared = child
    parent.children.value = [child, None, child]
    parent.bind_external_storage(storage, {FieldIdentity(Parent, "count"): "count"})
    choices = []
    with RandomContext(seed=401):
        for _ in range(4):
            assert parent.randomize()
            assert parent.absent is None
            assert parent.child is parent.shared is child
            assert parent.children.value == [child, None, child]
            choices.append(child.choice.value)
    assert set(choices) == {0, 1, 2, 3}
    assert callbacks == [child, parent] * 4
    assert storage.writes == [("count", FieldPath(), FieldOperation.SET)] * 4


def test_value_copy_preserves_null_shared_and_cyclic_handles():
    registry = ObjectRegistry()

    @svobj(registry=registry)
    class Node(SvObject):
        count = Int()
        link = Object["Node"](registry=registry)

    source, target = Node(), Node()
    source.count.value = 12
    source.link = source
    target.link = target
    target.value = source
    assert target.link is target
    assert target.count.value == 12
    source.link = None
    target.value = source
    assert target.link is None


@pytest.mark.parametrize("base", [SvObject, SvStruct])
def test_codec_prototype_bypasses_user_construction(base):
    calls = []

    class Required(base):
        field = Int(7)

        def __new__(cls, *args, **kwargs):
            calls.append("new")
            return super().__new__(cls)

        def __init__(self, seed):
            calls.append("init")
            super().__init__()
            self.field.value = seed

    session = CodecSession()
    codec = Required.codec_template(session=session)
    assert calls == []
    assert codec.field.value == 7
    assert session.allocate_object_number() == (session.origin << 48) | 1
    if base is SvObject:
        assert codec.svtypes_object_number == 0
        assert session.get(0) is None
    live = Required(12)
    assert calls == ["new", "init"]
    assert live.field.value == 12
    payload = codec.pack(live)
    decoded, consumed = (codec.unpack(payload, session.unpack_context())
                         if base is SvObject else codec.unpack(payload))
    assert decoded.field.value == 12
    assert consumed == len(payload)
    assert calls == ["new", "init"]


def test_external_null_handle_root_is_published_without_allocating_a_child():
    registry = ObjectRegistry()

    @svobj(registry=registry)
    class Child(SvObject):
        field = Int()

    @svobj(registry=registry)
    class Parent(SvObject):
        count = Bit[2]()
        child = Object[Child](registry=registry)

    parent, storage = Parent(), LockedStorage()
    storage.seed("count", Parent.count, 0)
    storage.seed("child", Parent.child, None)
    parent.bind_external_storage(storage, {
        FieldIdentity(Parent, "count"): "count",
        FieldIdentity(Parent, "child"): "child",
    })
    assert parent.randomize()
    assert parent.child is None
    assert storage.writes == [(key, FieldPath(), FieldOperation.SET) for key in ("count", "child")]


@pytest.mark.parametrize("selection, expected_index", [(slice(None), 0), (slice(None, None, -1), 1)])
@pytest.mark.parametrize("mapping", [False, True])
def test_nested_slice_keeps_original_path_and_close_state(selection, expected_index, mapping):
    element = AssocArray[String, Int] if mapping else Queue[Int]
    descriptor = Queue[element](rand=False, cov=False)
    values = [{"a": 1}, {"a": 2}] if mapping else [[1], [2]]
    storage = LockedStorage()
    storage.seed("rows", descriptor, values)
    root = bind_external_value(descriptor, storage, "rows")
    sliced = root.value[selection]
    assert isinstance(sliced, list)
    child = sliced[0]
    child["a" if mapping else 0] = 9
    path = FieldPath().index(expected_index)
    path = path.key(String(), "a") if mapping else path.index(0)
    assert storage.writes == [("rows", path, FieldOperation.SET)]
    assert root.value[expected_index]["a" if mapping else 0] == 9
    root.close()
    with pytest.raises(ExternalStorageClosedError):
        child["a" if mapping else 0] = 10
