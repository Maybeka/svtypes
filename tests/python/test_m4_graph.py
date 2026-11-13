import pytest

from svtypes import (
    CodecSession,
    Int,
    Object,
    Queue,
    RegistryError,
    SvObject,
    clear_object_registry,
    get_package,
    get_object,
    svobj,
)


graph_pkg = get_package("test_m4_graph")


@svobj(registry=graph_pkg)
class GraphNode(SvObject):
    data = Int()
    next = Object["GraphNode"](registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphPair(SvObject):
    left = Object["GraphNode"](registry=graph_pkg)
    right = Object["GraphNode"](registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphQueue(SvObject):
    nodes = Queue[GraphNode]()


@svobj(registry=graph_pkg)
class GraphRefQueue(SvObject):
    nodes = Queue[Object["GraphNode"](registry=graph_pkg)]()


def test_self_reference_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 11
    node.next.value = node

    payload = node.to_bytes()
    assert payload[0] == 1
    assert b"\x02" in payload

    decoded = GraphNode()
    out, count = decoded.unpack(payload)
    assert count == len(payload)
    assert out.data.value == 11
    assert out.next.value is out


def test_explicit_codec_sessions_isolate_ids_and_registries():
    first_session = CodecSession(origin=0x10)
    second_session = CodecSession(origin=0x20)
    first = GraphNode(session=first_session)
    second = GraphNode(session=second_session)

    assert first.svtypes_object_number >> 48 == 0x10
    assert second.svtypes_object_number >> 48 == 0x20
    assert first_session.get(first.svtypes_object_number) is first
    assert second_session.get(first.svtypes_object_number) is None
    first_session.close()
    assert first_session.get(first.svtypes_object_number) is None


def test_nested_descriptors_and_collection_templates_inherit_parent_session():
    session = CodecSession(origin=0x21)
    node = GraphNode(session=session)
    assert node.next.value is None
    node.next.value = GraphNode(session=session)
    assert node.next.value.codec_session is session
    assert session.get(node.next.value.svtypes_object_number) is node.next.value

    queue = GraphQueue(session=session)
    assert queue.nodes._elem_template.codec_session is session


def test_operation_contexts_are_isolated_and_reusable_codec_is_reentrant():
    session = CodecSession(origin=0x30)
    node = GraphNode(session=session)
    node.data.value = 9
    node.next.value = node

    outer = session.pack_context()
    inner = session.pack_context()
    outer_payload = node.pack(node, outer)
    inner_payload = node.pack(node, inner)
    assert outer_payload == inner_payload
    assert outer.seen is not inner.seen

    first_decode = GraphNode(session=session).unpack(
        outer_payload, session.unpack_context()
    )[0]
    second_decode = GraphNode(session=session).unpack(
        inner_payload, session.unpack_context()
    )[0]
    assert first_decode is second_decode
    assert first_decode.next.value is first_decode


def test_two_object_cycle_roundtrip_preserves_identity():
    clear_object_registry()

    a = GraphNode()
    b = GraphNode()
    a.data.value = 1
    b.data.value = 2
    a.next.value = b
    b.next.value = a

    out, count = GraphNode().unpack(a.to_bytes())
    assert count == len(a.to_bytes())
    assert out.data.value == 1
    assert out.next.value.data.value == 2
    assert out.next.value.next.value is out


def test_shared_child_roundtrip_preserves_identity():
    clear_object_registry()

    child = GraphNode()
    child.data.value = 33
    child.next.value = None
    pair = GraphPair()
    pair.left.value = child
    pair.right.value = child

    out, count = GraphPair().unpack(pair.to_bytes())
    assert count == len(pair.to_bytes())
    assert out.left.value is out.right.value
    assert out.left.value.data.value == 33


def test_queue_repeated_reference_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 44
    node.next.value = None
    queue = GraphQueue()
    queue.nodes.value = [node, node]

    out, count = GraphQueue().unpack(queue.to_bytes())
    assert count == len(queue.to_bytes())
    assert out.nodes[0].value is out.nodes[1].value
    assert out.nodes[0].value.data.value == 44


def test_reference_queue_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 45
    node.next.value = None
    queue = GraphRefQueue()
    queue.nodes.value = [node, node]

    payload = queue.to_bytes()
    out, count = GraphRefQueue().unpack(payload)
    assert count == len(payload)
    assert out.nodes.value[0] is out.nodes.value[1]
    assert out.nodes[0].value.data.value == 45


def test_existing_registered_object_is_updated_in_place():
    source_session = CodecSession(origin=0x60)
    source = GraphNode(session=source_session)
    source.data.value = 55
    source.next.value = None
    payload = source.to_bytes()

    target_session = CodecSession(origin=0x61)
    existing = GraphNode(svtypes_object_number=source.svtypes_object_number, session=target_session)
    existing.data.value = 1
    out, _ = GraphNode(session=target_session).unpack(
        payload, target_session.unpack_context()
    )

    assert out is existing
    assert existing.data.value == 55
    assert target_session.get(source.svtypes_object_number) is existing


def test_duplicate_inline_definition_updates_same_registered_object():
    first_session = CodecSession(origin=0x62)
    first = GraphNode(session=first_session)
    first.data.value = 1
    first.next.value = None

    second_session = CodecSession(origin=0x63)
    second = GraphNode(svtypes_object_number=first.svtypes_object_number, session=second_session)
    second.data.value = 2
    second.next.value = None

    pair_session = CodecSession(origin=0x64)
    pair = GraphPair(session=pair_session)
    pair.left.value = first
    pair.right.value = second

    payload = pair.to_bytes()
    decode_session = CodecSession(origin=0x65)
    out, count = GraphPair(session=decode_session).unpack(
        payload, decode_session.unpack_context()
    )
    assert count == len(payload)
    assert out.left.value is out.right.value
    assert out.left.value.data.value == 2
    assert decode_session.get(first.svtypes_object_number) is out.left.value


def test_object_number_collision_is_rejected_without_overwriting_binding():
    session = CodecSession(origin=0x66)
    first = GraphNode(session=session)
    with pytest.raises(RegistryError, match="object number collision"):
        GraphNode(svtypes_object_number=first.svtypes_object_number, session=session)
    assert session.get(first.svtypes_object_number) is first


def test_reference_type_mismatch_rejected():
    clear_object_registry()

    pair = GraphPair()
    payload = b"\x02" + pair.svtypes_object_number.to_bytes(8, "little")

    with pytest.raises(TypeError, match="type mismatch"):
        GraphNode().unpack(payload)


def test_unresolved_reference_rejected():
    clear_object_registry()
    payload = b"\x02" + (0x0001000000001234).to_bytes(8, "little")
    with pytest.raises(ValueError, match="unresolved object reference"):
        GraphNode().unpack(payload)


def test_registry_clear_makes_reference_unresolved():
    clear_object_registry()

    node = GraphNode()
    payload = b"\x02" + node.svtypes_object_number.to_bytes(8, "little")
    clear_object_registry()

    with pytest.raises(ValueError, match="unresolved object reference"):
        GraphNode().unpack(payload)


def test_zero_reference_id_rejected():
    payload = b"\x02" + b"\x00" * 8
    with pytest.raises(ValueError, match="reference has id 0"):
        GraphNode().unpack(payload)


def test_failed_root_decode_rolls_back_target_identity_and_fields():
    source_session = CodecSession(origin=0x40)
    source = GraphNode(session=source_session)
    source.data.value = 123
    source.next.value = None
    truncated = source.to_bytes()[:-1]

    target_session = CodecSession(origin=0x41)
    target = GraphNode(session=target_session)
    target.data.value = 77
    original_id = target.svtypes_object_number

    with pytest.raises(ValueError):
        target.from_bytes(truncated)

    assert target.svtypes_object_number == original_id
    assert target.data.value == 77
    assert target_session.get(original_id) is target
    assert target_session.get(source.svtypes_object_number) is None


def test_failed_nested_decode_does_not_mutate_registered_child():
    source_session = CodecSession(origin=0x42)
    child = GraphNode(session=source_session)
    child.data.value = 456
    child.next.value = None
    pair = GraphPair(session=source_session)
    pair.left.value = child
    pair.right.value = child
    truncated = pair.to_bytes()[:-1]

    target_session = CodecSession(origin=0x43)
    existing_child = GraphNode(svtypes_object_number=child.svtypes_object_number, session=target_session)
    existing_child.data.value = 88
    codec = GraphPair(session=target_session)

    with pytest.raises(ValueError):
        codec.unpack(truncated, target_session.unpack_context())

    assert existing_child.data.value == 88
    assert target_session.get(child.svtypes_object_number) is existing_child
    assert target_session.get(pair.svtypes_object_number) is None


def test_strict_handle_assignment_rejects_unresolved_target_without_replacing_value():
    from svtypes import ObjectRegistry

    registry = ObjectRegistry()

    class Child(SvObject):
        data = Int()

    class Parent(SvObject):
        child = Object["Child"](registry=registry, strict_set=True)

    registry.register(Child)
    parent = Parent(session=CodecSession())
    old = Child(session=parent.codec_session)
    parent.child.value = old
    registry.clear()
    with pytest.raises(TypeError, match=r"Parent.child: unresolved object target 'Child'"):
        parent.child.value = old
    assert parent.child.value is old
    parent.child.value = None
    assert parent.child.value is None


@pytest.mark.parametrize("diagnostics", ["off", "strict"])
def test_base_handle_decodes_registered_derived_inline_and_reference(diagnostics):
    from svtypes import ObjectRegistry, UnpackContext

    registry = ObjectRegistry()

    class Base(SvObject):
        base_value = Int()

    class Derived(Base):
        extra = Int()

    class Pair(SvObject):
        first = Object["Base"](registry=registry, strict_set=True)
        second = Object["Base"](registry=registry, strict_set=True)
        missing = Object["Base"](registry=registry, strict_set=True)

    registry.register(Base)
    registry.register(Derived, name="WireDerived")
    source = Pair(session=CodecSession(origin=0x40))
    child = Derived(session=source.codec_session)
    child.base_value.value = 12
    child.extra.value = 34
    source.first.value = source.second.value = child
    source.missing.value = None
    payload = source.to_bytes()

    session = CodecSession(origin=0x41)
    restored, used = Pair.codec_template(session=session).unpack(
        payload, UnpackContext(session, diagnostics=diagnostics)
    )
    assert used == len(payload)
    assert type(restored.first.value) is Derived
    assert restored.first.value is restored.second.value
    assert session.get(child.svtypes_object_number) is restored.first.value
    assert restored.first.value.base_value.value == 12
    assert restored.first.value.extra.value == 34
    assert restored.missing.value is None


@pytest.mark.parametrize("register_dynamic", [False, True])
def test_base_handle_rejects_unknown_or_unrelated_inline_type(register_dynamic):
    from svtypes import ObjectRegistry

    registry = ObjectRegistry()

    class Base(SvObject):
        pass

    class Unrelated(SvObject):
        extra = Int()

    class Parent(SvObject):
        child = Object["Base"](registry=registry)

    registry.register(Base)
    if register_dynamic:
        registry.register(Unrelated)
    source = Parent(session=CodecSession(origin=0x42))
    # Forge the invalid graph on the encoding side. Public assignment now
    # rejects it before the decoder can be exercised.
    source.__dict__[Parent.child._cache_key] = Unrelated(session=source.codec_session)
    session = CodecSession(origin=0x43)
    match = "object type mismatch" if register_dynamic else "unknown dynamic object type"
    with pytest.raises(ValueError, match=match):
        Parent.codec_template(session=session).unpack(source.to_bytes())
    assert session.get(source.svtypes_object_number) is None
    assert session.get(source.child.value.svtypes_object_number) is None
