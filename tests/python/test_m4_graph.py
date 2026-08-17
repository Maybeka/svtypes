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
    next = Object("GraphNode", registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphPair(SvObject):
    left = Object("GraphNode", registry=graph_pkg)
    right = Object("GraphNode", registry=graph_pkg)


@svobj(registry=graph_pkg)
class GraphQueue(SvObject):
    nodes = Queue(GraphNode())


@svobj(registry=graph_pkg)
class GraphRefQueue(SvObject):
    nodes = Queue(Object("GraphNode", registry=graph_pkg))


def test_self_reference_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 11
    node.next = node

    payload = node.to_bytes()
    assert payload[0] == 1
    assert b"\x02" in payload

    decoded = GraphNode()
    out, count = decoded.unpack(payload)
    assert count == len(payload)
    assert out.data.value == 11
    assert out.next is out


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
    assert node.next.codec_session is session
    assert session.get(node.next.svtypes_object_number) is node.next

    queue = GraphQueue(session=session)
    assert queue.nodes._elem_template.codec_session is session


def test_operation_contexts_are_isolated_and_reusable_codec_is_reentrant():
    session = CodecSession(origin=0x30)
    node = GraphNode(session=session)
    node.data.value = 9
    node.next = node

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
    assert first_decode.next is first_decode


def test_two_object_cycle_roundtrip_preserves_identity():
    clear_object_registry()

    a = GraphNode()
    b = GraphNode()
    a.data.value = 1
    b.data.value = 2
    a.next = b
    b.next = a

    out, count = GraphNode().unpack(a.to_bytes())
    assert count == len(a.to_bytes())
    assert out.data.value == 1
    assert out.next.data.value == 2
    assert out.next.next is out


def test_shared_child_roundtrip_preserves_identity():
    clear_object_registry()

    child = GraphNode()
    child.data.value = 33
    child.next = None
    pair = GraphPair()
    pair.left = child
    pair.right = child

    out, count = GraphPair().unpack(pair.to_bytes())
    assert count == len(pair.to_bytes())
    assert out.left is out.right
    assert out.left.data.value == 33


def test_queue_repeated_reference_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 44
    node.next = None
    queue = GraphQueue()
    queue.nodes.value = [node, node]

    out, count = GraphQueue().unpack(queue.to_bytes())
    assert count == len(queue.to_bytes())
    assert out.nodes[0] is out.nodes[1]
    assert out.nodes[0].data.value == 44


def test_reference_queue_roundtrip_preserves_identity():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 45
    node.next = None
    queue = GraphRefQueue()
    queue.nodes.value = [node, node]

    payload = queue.to_bytes()
    out, count = GraphRefQueue().unpack(payload)
    assert count == len(payload)
    assert out.nodes.value[0] is out.nodes.value[1]
    assert out.nodes[0].data.value == 45


def test_existing_registered_object_is_updated_in_place():
    source_session = CodecSession(origin=0x60)
    source = GraphNode(session=source_session)
    source.data.value = 55
    source.next = None
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
    first.next = None

    second_session = CodecSession(origin=0x63)
    second = GraphNode(svtypes_object_number=first.svtypes_object_number, session=second_session)
    second.data.value = 2
    second.next = None

    pair_session = CodecSession(origin=0x64)
    pair = GraphPair(session=pair_session)
    pair.left = first
    pair.right = second

    payload = pair.to_bytes()
    decode_session = CodecSession(origin=0x65)
    out, count = GraphPair(session=decode_session).unpack(
        payload, decode_session.unpack_context()
    )
    assert count == len(payload)
    assert out.left is out.right
    assert out.left.data.value == 2
    assert decode_session.get(first.svtypes_object_number) is out.left


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
    source.next = None
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
    child.next = None
    pair = GraphPair(session=source_session)
    pair.left = child
    pair.right = child
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
