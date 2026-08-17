import pytest

from svtypes import (
    AssocArray,
    CodecSession,
    DecodeLimits,
    DynArray,
    EncodeError,
    Int,
    ResourceLimitError,
    String,
    schema_descriptor,
)
from test_m4_graph import GraphNode, GraphQueue


def test_standalone_dynamic_codecs_reject_lengths_before_allocation():
    with pytest.raises(ResourceLimitError, match="DynArray length 3"):
        DynArray(Int(), max_length=2).unpack((3).to_bytes(4, "little"))

    with pytest.raises(ResourceLimitError, match="AssocArray length 3"):
        AssocArray(Int(), Int(), max_length=2).unpack((3).to_bytes(4, "little"))

    with pytest.raises(ResourceLimitError, match="String length 3"):
        String(max_bytes=2).unpack((3).to_bytes(4, "little"))


def test_dynamic_codecs_enforce_the_same_limit_while_encoding():
    with pytest.raises(EncodeError, match="encoder limit"):
        DynArray(Int(), max_length=2).pack([1, 2, 3])
    with pytest.raises(EncodeError, match="encoder limit"):
        AssocArray(Int(), Int(), max_length=2).pack({1: 1, 2: 2, 3: 3})
    with pytest.raises(EncodeError, match="encoder limit"):
        String(max_bytes=2).pack("abc")


def test_local_decoder_limits_change_source_schema_but_not_encoding_identity():
    normal = schema_descriptor(DynArray(Int(), max_length=10))
    strict = schema_descriptor(DynArray(Int(), max_length=2))
    assert normal.schema_fingerprint != strict.schema_fingerprint
    assert normal.encoding_fingerprint == strict.encoding_fingerprint


def test_context_collection_limit_rolls_back_object_registration():
    source_session = CodecSession(origin=0x50)
    source = GraphQueue(session=source_session)
    source.nodes.value = []
    payload = bytearray(source.to_bytes())
    payload[-4:] = (3).to_bytes(4, "little")

    target_session = CodecSession(origin=0x51)
    codec = GraphQueue(session=target_session)
    context = target_session.unpack_context(
        DecodeLimits(max_dynamic_length=2)
    )
    with pytest.raises(ResourceLimitError, match="Queue length 3"):
        codec.unpack(bytes(payload), context)
    assert target_session.get(source.svtypes_object_number) is None


def test_context_nesting_and_input_size_limits_are_enforced():
    source_session = CodecSession(origin=0x52)
    source = GraphNode(session=source_session)
    source.next = source
    payload = source.to_bytes()

    target_session = CodecSession(origin=0x53)
    codec = GraphNode(session=target_session)
    with pytest.raises(ResourceLimitError, match="nesting depth"):
        codec.unpack(
            payload,
            target_session.unpack_context(DecodeLimits(max_nesting_depth=1)),
        )

    with pytest.raises(ResourceLimitError, match="input size"):
        codec.unpack(
            payload,
            target_session.unpack_context(DecodeLimits(max_input_bytes=len(payload) - 1)),
        )
