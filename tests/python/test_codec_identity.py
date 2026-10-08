import pytest

from svtypes import (
    CodecSession, Int, Object, RegistryError, SvObject, SvStruct, checked_unpack,
    encoding_descriptor, get_package, svobj,
)


registry = get_package('codec_identity_test')


@svobj(registry=registry)
class Node(SvObject):
    number = Int()
    link = Object['Node'](registry=registry)


@svobj(registry=registry)
class Graph(SvObject):
    first = Object['Node'](registry=registry)
    shared = Object['Node'](registry=registry)


def test_codec_template_does_not_consume_or_register_identity():
    session = CodecSession()
    template = Graph.codec_template(session=session)
    assert template.svtypes_object_number == 0
    assert session.get(0) is None
    assert Graph(session=session).svtypes_object_number == (session.origin << 48) | 1


def test_checked_decode_reuses_graph_identity_and_reserves_imported_numbers():
    source = CodecSession()
    receiver = CodecSession()
    codec = Graph.codec_template(session=receiver)
    descriptor = encoding_descriptor(codec)
    received_graphs = []
    for index in range(3):
        graph = Graph(session=source)
        graph.first = graph.shared = None
        if index:
            node = Node(session=source)
            node.number.value = index
            node.link = node
            graph.first = graph.shared = node
        payload = graph.pack(graph, source.pack_context())
        value, consumed = checked_unpack(codec, payload, descriptor, receiver.unpack_context())
        assert consumed == len(payload)
        assert receiver.get(value.svtypes_object_number) is value
        if index:
            assert value.first is value.shared
            assert value.first.link is value.first
            assert value.first.number.value == index
        else:
            assert value.first is value.shared is None
        again, _ = checked_unpack(codec, payload, descriptor, receiver.unpack_context())
        assert again is value
        received_graphs.append(value)
    local = Graph(session=receiver)
    assert local.svtypes_object_number > received_graphs[-1].first.svtypes_object_number
    assert receiver.get(local.svtypes_object_number) is local


def test_imported_number_is_not_reissued_after_removal():
    session = CodecSession()
    imported = Graph(session=session, svtypes_object_number=(session.origin << 48) | 20)
    session.remove(imported.svtypes_object_number)
    assert Graph(session=session).svtypes_object_number == (session.origin << 48) | 21


def test_real_duplicate_identity_is_still_rejected():
    session = CodecSession()
    first = Graph(session=session)
    with pytest.raises(RegistryError, match='object number collision'):
        Node(session=session, svtypes_object_number=first.svtypes_object_number)
    assert session.get(first.svtypes_object_number) is first


def test_struct_codec_remains_by_value_with_a_decode_context():
    class Value(SvStruct):
        number = Int()

    session = CodecSession()
    codec = Value.codec_template(session=session)
    value = Value()
    value.number.value = 12
    payload = codec.pack(value)
    result, count = checked_unpack(
        codec, payload, encoding_descriptor(codec), session.unpack_context()
    )
    assert result.number.value == 12
    assert count == len(payload)
    assert session.allocate_object_number() == (session.origin << 48) | 1
