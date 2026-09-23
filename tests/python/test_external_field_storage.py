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
    Int,
    MemoryExternalFieldStorage,
    Object,
    ObjectRegistry,
    Queue,
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
    code = Bit(8)


@svobj
class ExternalPacket(SvObject):
    count = Bit(8)
    words = Array(Bit(8), 3)
    queue = Queue(Int())
    labels = AssocArray(String(), Bit(8))
    inner = ExternalInner()
    local = Bit(8)


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

    descriptor = Bit(4)
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


def test_external_object_handle_uses_a_detached_owner_view() -> None:
    registry = ObjectRegistry()

    @svobj
    class Child(SvObject):
        value = Bit(8)

    registry.register(Child)

    @svobj
    class Parent(SvObject):
        child = Object("Child", registry=registry)

    storage = RecordingStorage()
    child = Child()
    child.value.value = 4
    storage.seed("child", Parent.__dict__["child"], child)
    parent = Parent()
    parent.bind_external_storage(storage, {FieldIdentity(Parent, "child"): "child"})

    parent.child.value.value = 8
    assert parent.child.value.value == 8
