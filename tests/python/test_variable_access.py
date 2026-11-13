import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    ExternalStorageError,
    Int,
    MemoryExternalFieldStorage,
    Object,
    ObjectRegistry,
    Queue,
    String,
    SvObject,
    SvStruct,
    bind_external_value,
    field,
    read_value,
    write_value,
)


def test_explicit_handle_slot_is_stable_and_does_not_construct_a_referent():
    registry = ObjectRegistry()
    constructed = []

    class Child(SvObject):
        code = Int()

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            constructed.append(self)

    class Derived(Child):
        extra = Int()

    class Parent(SvObject):
        child = Object["Child"](registry=registry)

    registry.register(Child)
    owner = Parent()
    slot = field(owner, "child")
    assert owner.child is slot
    assert owner.child.value is None
    with pytest.raises(AttributeError, match=r"child\.value"):
        owner.child = None
    assert read_value(slot) is None
    assert not constructed

    child = Derived()
    write_value(slot, child)
    captured = read_value(slot)
    assert captured is child
    with pytest.raises(TypeError, match="expected Child, got Parent"):
        write_value(slot, owner)
    assert slot.value is child
    write_value(slot, None)
    assert field(owner, "child") is owner.child is slot
    assert slot.value is None
    assert captured is child


def test_struct_value_mapping_disambiguates_value_field_and_copies_aggregates():
    registry = ObjectRegistry()

    class Child(SvObject):
        value = Int()

    registry.register(Child)

    class Entry(SvStruct):
        value = Bit[8]()
        child = Object["Child"](registry=registry)

    class Packet(SvStruct):
        entries = Queue[Entry]()
        labels = AssocArray[String, Int]()

    packet = Packet()
    child = Child()
    expected = {"entries": [{"value": 7, "child": child}], "labels": {"tag": 2}}
    write_value(packet, expected)

    snapshot = read_value(packet)
    assert snapshot == expected
    assert snapshot["entries"][0]["child"] is child
    entry = packet.entries[0]
    write_value(field(entry, "value"), 42)
    assert read_value(entry) == {"value": 42, "child": child}
    assert snapshot["entries"][0]["value"] == 7
    snapshot["labels"]["tag"] = 9
    assert read_value(packet)["labels"]["tag"] == 2
    assert read_value(field(child, "value")) == 0


def test_invalid_aggregate_assignment_preserves_previous_value():
    class Packet(SvStruct):
        tag = Int()
        words = Array[Bit[8], 2]()

    packet = Packet()
    write_value(packet, {"tag": 7, "words": [1, 2]})
    with pytest.raises(ValueError, match="Array size mismatch"):
        write_value(packet, {"tag": 9, "words": [3]})
    assert read_value(packet) == {"tag": 7, "words": [1, 2]}


def test_external_aggregate_value_is_detached_and_keeps_write_permissions():
    class Packet(SvStruct):
        words = Queue[Int]()

    storage = MemoryExternalFieldStorage()
    packet = Packet()
    write_value(packet, {"words": [3, 4]})
    storage.seed("packet", Packet(), packet)
    bound = bind_external_value(Packet(), storage, "packet")
    view = bound.value
    snapshot = view.value
    snapshot["words"].append(5)
    assert read_value(view) == {"words": [3, 4]}
    view.value = snapshot
    assert read_value(view) == {"words": [3, 4, 5]}

    readonly = bind_external_value(Packet(), storage, "packet", readonly=True)
    with pytest.raises(ExternalStorageError, match="read-only"):
        write_value(readonly.value, {"words": [9]})
    assert read_value(view) == {"words": [3, 4, 5]}


def test_local_element_borrows_keep_detached_elements_and_nested_fields():
    from svtypes import ExternalStorageClosedError, FieldPath, borrow_element

    class Entry(SvStruct):
        code = Int()

    entries = Queue[Entry]()
    write_value(entries, [{"code": 10}, {"code": 20}])
    original = borrow_element(entries, 1, path=FieldPath().member("code"))
    second = borrow_element(entries, 1, path=FieldPath().member("code"))
    entries.pop_front()
    assert read_value(original) == 20
    with original.forward() as inner:
        write_value(inner, 21)
        assert read_value(entries) == [{"code": 21}]
    write_value(entries, [{"code": 99}])
    write_value(second, 22)
    assert read_value(original) == 22
    assert read_value(entries) == [{"code": 99}]
    original.close()
    with pytest.raises(ExternalStorageClosedError):
        read_value(original)
    assert read_value(second) == 22
    second.close()


def test_associative_borrow_does_not_rebind_when_key_is_recreated():
    from svtypes import borrow_element

    labels = AssocArray[String, Int]()
    write_value(labels, {"key": 12})
    with borrow_element(labels, "key") as old:
        write_value(labels, {})
        write_value(labels, {"key": 34})
        old.value = 56
        assert old.value == 56
        assert read_value(labels) == {"key": 34}


def test_external_borrow_uses_stable_token_and_reference_counted_release():
    from svtypes import ExternalStorageClosedError, FieldPath, borrow_element

    class TokenStorage(MemoryExternalFieldStorage):
        def __init__(self):
            self.cells = [[3], [4], [5]]
            self.acquired = []
            self.released = []

        def read(self, key, descriptor, path):
            raise AssertionError("borrow must not read by path after acquisition")

        def write(self, key, descriptor, path, operation, payload):
            raise AssertionError("borrow must not write by path after acquisition")

        def acquire_borrow(self, key, descriptor, path):
            self.acquired.append(path)
            return self.cells[path.segments[0].index]

        def read_borrow(self, token):
            return Int().pack(token[0])

        def write_borrow(self, token, payload):
            token[0], _ = Int().unpack(payload)

        def release_borrow(self, token):
            self.released.append(token)

    backend = TokenStorage()
    bound = bind_external_value(Queue[Int](), backend, "queue")
    outer = borrow_element(bound, 1)
    assert backend.acquired == [FieldPath().index(1)]
    backend.cells.insert(0, [99])
    assert outer.value == 4
    with outer.forward() as inner:
        inner.value = 7
    assert backend.released == []
    assert backend.cells == [[99], [3], [7], [5]]
    backend.cells.reverse()
    assert outer.value == 7
    sibling = borrow_element(bound, 1)
    sibling.value = 8
    assert outer.value == 8
    detached = backend.cells.pop(1)
    outer.value = 9
    assert sibling.value == 9
    sibling.close()
    assert len(backend.released) == 1 and backend.released[0] is detached
    outer.value = 10
    assert backend.cells == [[5], [3], [99]]
    assert outer.value == 10
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        _ = outer.value
    outer.close()
    assert len(backend.released) == 2 and backend.released[1] is detached

    readonly = bind_external_value(Queue[Int](), backend, "queue", readonly=True)
    with borrow_element(readonly.value, 0) as element:
        with pytest.raises(ExternalStorageError, match="read-only"):
            element.value = 0

    unsupported = bind_external_value(Queue[Int](), MemoryExternalFieldStorage(), "queue")
    with pytest.raises(ExternalStorageError, match="does not support stable element borrowing"):
        borrow_element(unsupported, 0)


def test_handle_container_borrows_track_slots_and_preserve_referents():
    from svtypes import DynArray, borrow_element, svobj

    @svobj(name="BorrowedContainerChild")
    class Child(SvObject):
        count = Int()

    class Derived(Child):
        pass

    element = Object[Child]
    fixed = Array[element, 2]()
    queue = Queue[element]()
    dynamic = DynArray[element]()
    keyed = AssocArray[String, element]()
    target = Derived()
    replacement = Child()

    assert read_value(fixed) == [None, None]
    assert fixed[0].value is None
    assert fixed[0] is fixed[:1][0]
    with pytest.raises(TypeError, match=r"container\[index\]\.value"):
        fixed[0] = target
    with borrow_element(fixed, 0) as first:
        write_value(fixed, [target, None])
        assert first.value is target
        with pytest.raises(TypeError, match="expected BorrowedContainerChild"):
            write_value(fixed, [None, SvObject()])
        assert first.value is target
        assert read_value(fixed) == [target, None]

    write_value(queue, [target, target])
    selected_slot = queue[1]
    assert queue[0] is not selected_slot
    assert queue[0].value is selected_slot.value is target
    with borrow_element(queue, 1) as selected, borrow_element(queue, 1) as second:
        assert queue.pop_front() is target
        assert queue[0] is selected_slot
        selected.value = replacement
        assert second.value is replacement
        assert read_value(queue) == [replacement]
        assert queue.pop_front() is replacement
        queue.push_back(target)
        second.value = None
        assert selected.value is None
        assert read_value(queue) == [target]

    write_value(dynamic, [target])
    with borrow_element(dynamic, 0) as old:
        write_value(dynamic, [replacement])
        assert old.value is target
        old.value = None
        assert read_value(dynamic) == [replacement]

    write_value(keyed, {"item": target})
    assert keyed["item"].value is target
    with borrow_element(keyed, "item") as old:
        write_value(keyed, {})
        write_value(keyed, {"item": replacement})
        assert old.value is target
        old.value = None
        assert read_value(keyed) == {"item": replacement}


def test_materialized_handle_container_observes_late_target_registration():
    from svtypes import borrow_element

    class Owner(SvObject):
        slots = Array[Object["ForwardHandleContainerChild"], 1]()

    class Child(SvObject):
        pass

    owner = Owner()
    with borrow_element(owner.slots, 0) as selected:
        assert selected.value is None
        child = Child()
        with pytest.raises(TypeError, match="unresolved object target"):
            selected.value = child
        Object["ForwardHandleContainerChild"]().registry.register(Child, "ForwardHandleContainerChild")
        selected.value = child
        assert read_value(owner.slots) == [child]


def test_handle_container_codecs_keep_shared_identity_between_fields():
    from svtypes import CodecSession, svobj

    @svobj(name="ContainerCodecChild")
    class Child(SvObject):
        count = Int()

    class Owner(SvObject):
        entries = AssocArray[String, Object[Child]]()
        queue = Queue[Object[Child]]()

    owner = Owner(session=CodecSession())
    child = Child(session=owner.codec_session)
    child.count.value = 19
    write_value(owner.entries, {"a": child, "b": child})
    write_value(owner.queue, [None, child])
    payload = owner.to_bytes()
    codec = Owner.codec_template(session=CodecSession())
    decoded, consumed = codec.unpack(payload)
    assert consumed == len(payload)
    assert decoded.entries["a"].value is decoded.entries["b"].value is decoded.queue[1].value
    assert decoded.queue[1].value is not child
    assert decoded.queue[1].value.count.value == 19


def test_external_identity_access_keeps_referents_without_replaying_their_state():
    from svtypes import CodecSession, FieldIdentity, svobj

    @svobj(name="NativeIdentityChild")
    class Child(SvObject):
        count = Int()

    class Packet(SvStruct):
        child = Object[Child]()
        others = Queue[Object[Child]]()

    class Owner(SvObject):
        child = Object[Child]()

    session = CodecSession()
    first, second = Child(session=session), Child(session=session)
    first.count.value = 4
    packet = Packet()
    write_value(packet, {"child": first, "others": [first, None]})
    storage = MemoryExternalFieldStorage()
    storage.seed("child", Owner.child, first)
    storage.seed("packet", Packet(), packet)
    first.count.value = 45

    owner = Owner(session=session)
    owner.bind_external_storage(storage, {FieldIdentity(Owner, "child"): "child"})
    slot = field(owner, "child")
    retained = read_value(slot)
    assert retained is first
    assert retained.count.value == 45

    bound = bind_external_value(Packet(), storage, "packet", session=session)
    snapshot = read_value(bound)
    assert snapshot["child"] is snapshot["others"][0] is retained
    snapshot["others"].clear()
    assert len(read_value(bound)["others"]) == 2
    assert retained.count.value == 45

    slot.value = second
    assert slot.value is second
    with pytest.raises(TypeError):
        slot.value = SvObject()
    assert slot.value is second
    same_number_in_another_session = Child(session=CodecSession())
    with pytest.raises(ExternalStorageError, match="is not bound to that referent"):
        slot.value = same_number_in_another_session
    assert slot.value is second
    slot.value = None
    assert slot.value is None

    value = {"child": second, "others": [first, second]}
    write_value(bound, value)
    value["others"].clear()
    assert read_value(bound)["others"] == [first, second]

    readonly = bind_external_value(Packet(), storage, "packet", readonly=True, session=session)
    assert read_value(readonly)["child"] is second
    read_value(readonly)["child"].count.value = 71
    assert read_value(readonly)["child"].count.value == 71
    with pytest.raises(ExternalStorageError, match="read-only"):
        write_value(readonly, {"child": None, "others": []})
    bound.close()
    with pytest.raises(ExternalStorageError, match="closed"):
        read_value(bound)

    unknown = bind_external_value(Packet(), storage, "packet", session=CodecSession())
    with pytest.raises(ExternalStorageError, match="unavailable in the handle read session"):
        read_value(unknown)



def test_external_handle_index_is_a_slot_and_does_not_replay_referent():
    from svtypes import CodecSession, FieldIdentity, svobj

    @svobj(name="IndexedExternalChild")
    class Child(SvObject):
        count = Int()

    class Owner(SvObject):
        children = Queue[Object[Child]]()

    session = CodecSession()
    first, second = Child(session=session), Child(session=session)
    first.count.value = 3
    storage = MemoryExternalFieldStorage()
    storage.seed("children", Owner.children, [first, None])
    owner = Owner(session=session)
    owner.bind_external_storage(storage, {FieldIdentity(Owner, "children"): "children"})
    first.count.value = 37

    assert owner.children[0].value is first
    assert owner.children[-2].value.count.value == 37
    assert owner.children[-1].value is None
    owner.children[1].value = second
    assert read_value(owner.children) == [first, second]
    assert [slot.value for slot in owner.children] == [first, second]

    bound = bind_external_value(Owner.children, storage, "children", readonly=True, session=session)
    assert [slot.value for slot in bound[:]] == [first, second]
    assert [slot.value for slot in bound] == [first, second]
    with pytest.raises(ExternalStorageError, match="read-only"):
        bound[-1].value = None
    slot = bound[0]
    bound.close()
    with pytest.raises(ExternalStorageError, match="closed"):
        _ = slot.value


def test_unpacked_value_property_uses_a_detached_mapping_with_an_explicit_named_slot():
    class Entry(SvStruct):
        value = Int(7)
        numbers = Queue[Int]()

    class Packet(SvObject):
        entry = Entry()

    packet = Packet()
    entry = packet.entry
    assert entry.value == {"value": 7, "numbers": []}
    slot = field(entry, "value")
    entry.value = {"value": 9, "numbers": [1, 2]}
    assert field(entry, "value") is slot and slot.value == 9

    detached = entry.value
    detached["numbers"].append(3)
    assert entry.value == {"value": 9, "numbers": [1, 2]}
    with pytest.raises(ValueError, match="fields differ"):
        entry.value = {"value": 4}
    assert slot.value == 9

    clone = Packet()
    clone.from_bytes(packet.to_bytes())
    assert clone.entry.value == entry.value
    assert field(clone.entry, "value").value == 9


def test_named_struct_value_field_is_sampled_and_randomized_by_declaration():
    from svtypes import CovPoint, bins, constraint, covergroup

    class Entry(SvStruct):
        value = Bit[3](rand=True)

    class Packet(SvObject):
        entry = Entry(rand=True)

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

        @constraint
        def choose(self):
            self.entry.value == 5

        @covergroup
        def cg(self):
            class selected(CovPoint, source=self.entry.value):
                five = bins[5]

    packet = Packet()
    assert packet.randomize()
    assert packet.entry.value == {"value": 5}
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["selected"]["hits"] == {"five": 1}
