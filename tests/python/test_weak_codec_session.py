import gc
import struct
import weakref

import pytest

from svtypes import CodecSession, Int, Object, RegistryError, SvObject, checked_unpack, encoding_descriptor, get_package, svobj


registry = get_package('weak_codec_session_test')


@svobj(registry=registry)
class Node(SvObject):
    number = Int(cov=False)
    link = Object['Node'](registry=registry)


@svobj(registry=registry)
class Graph(SvObject):
    first = Object['Node'](registry=registry)
    shared = Object['Node'](registry=registry)


def test_weak_session_preserves_live_identity_and_collects_unreachable_cycles():
    source = CodecSession(origin=2)
    session = CodecSession(retain_objects=False)
    graph = Graph(session=source)
    node = Node(session=source)
    graph.first.value = graph.shared.value = node
    node.link.value = node
    payload = graph.pack(graph)
    codec = Graph.codec_template(session=session)
    decoded, _ = checked_unpack(codec, payload, encoding_descriptor(codec), session.unpack_context())
    repeated, _ = checked_unpack(codec, payload, encoding_descriptor(codec), session.unpack_context())
    assert repeated is decoded
    child = decoded.first.value
    assert child is decoded.shared.value and child.link.value is child
    identities = decoded.svtypes_object_number, child.svtypes_object_number
    references = weakref.ref(decoded), weakref.ref(child)
    del decoded, repeated, child
    gc.collect()
    assert all(reference() is None for reference in references)
    assert all(session.get(identity) is None for identity in identities)
    assert not session.retain_objects
    source.close()


def test_owning_session_remains_compatible():
    session = CodecSession()
    value = Node(session=session)
    reference, number = weakref.ref(value), value.svtypes_object_number
    del value
    gc.collect()
    assert reference() is session.get(number)
    assert session.retain_objects
    session.close()
    gc.collect()
    assert reference() is None


def test_child_handle_keeps_referent_live_until_graph_releases_it():
    session = CodecSession(retain_objects=False)
    graph = Graph(session=session)
    child = Node(session=session)
    number, reference = child.svtypes_object_number, weakref.ref(child)
    graph.first.value = graph.shared.value = child
    del child
    gc.collect()
    assert reference() is graph.first.value is session.get(number)
    graph.first.value = None
    gc.collect()
    assert reference() is graph.shared.value
    graph.shared.value = None
    gc.collect()
    assert reference() is None and session.get(number) is None


def test_weak_session_does_not_reissue_collected_numbers():
    session = CodecSession(retain_objects=False)
    value = Node(session=session, svtypes_object_number=(session.origin << 48) | 20)
    reference = weakref.ref(value)
    del value
    gc.collect()
    assert reference() is None
    assert Node(session=session).svtypes_object_number == (session.origin << 48) | 21


def test_live_collision_and_stale_reference_are_still_errors():
    session = CodecSession(retain_objects=False)
    value = Node(session=session)
    number = value.svtypes_object_number
    with pytest.raises(RegistryError, match='object number collision'):
        Node(session=session, svtypes_object_number=number)
    assert session.get(number) is value
    codec = Node.codec_template(session=session)
    reference = b'\x02' + struct.pack('<Q', number)
    assert codec.unpack(reference)[0] is value
    del value
    gc.collect()
    with pytest.raises(ValueError, match='unresolved object reference'):
        codec.unpack(reference)


@pytest.mark.parametrize('value', [None, 0, 1, 'weak'])
def test_retention_policy_requires_boolean(value):
    with pytest.raises(TypeError, match='retain_objects must be a bool'):
        CodecSession(retain_objects=value)


def test_clear_and_remove_work_without_invalidating_live_objects():
    session = CodecSession(retain_objects=False)
    value = Node(session=session)
    number = value.svtypes_object_number
    assert session.remove(number) is value
    assert session.get(number) is None
    session.register(value)
    session.clear()
    assert session.get(number) is None
    value.number.value = 8
    assert value.number.value == 8


def test_pack_context_retains_seen_objects_only_for_its_own_lifetime():
    session = CodecSession(retain_objects=False)
    value = Node(session=session)
    number, reference = value.svtypes_object_number, weakref.ref(value)
    context = session.pack_context()
    assert value.pack(value, context)[0] == 1
    assert value.pack(value, context) == b'\x02' + struct.pack('<Q', number)
    del value
    gc.collect()
    assert session.get(number) is reference() and reference() is not None
    del context
    gc.collect()
    assert reference() is None and session.get(number) is None


def test_failed_decode_leaves_existing_weak_graph_and_context_unchanged():
    source = CodecSession(origin=7)
    node = Node(session=source)
    node.number.value = 10
    node.link.value = node
    payload = node.to_bytes()
    destination = CodecSession(retain_objects=False)
    codec = Node.codec_template(session=destination)
    context = destination.unpack_context()
    decoded, count = codec.unpack(payload, context)
    assert count == len(payload)
    decoded.number.value = 99
    number = decoded.svtypes_object_number
    with pytest.raises(ValueError):
        codec.unpack(payload[:-1], context)
    assert decoded.number.value == 99 and decoded.link.value is decoded
    assert destination.get(number) is decoded and context.objects == {}
    reference = weakref.ref(decoded)
    del decoded
    gc.collect()
    assert reference() is None and destination.get(number) is None
    reconstructed, count = codec.unpack(payload, context)
    assert count == len(payload) and reconstructed.number.value == 10
    assert reconstructed.link.value is reconstructed
    source.close()


def test_weak_session_uses_identical_wire_bytes_to_owning_session():
    payloads = []
    for retention in (True, False):
        session = CodecSession(origin=3, retain_objects=retention)
        graph = Graph(session=session)
        node = Node(session=session)
        graph.first.value = graph.shared.value = node
        node.link.value = node
        payloads.append(graph.to_bytes())
        session.close()
    assert payloads[0] == payloads[1]
