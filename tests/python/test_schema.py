from dataclasses import FrozenInstanceError

import pytest

from svtypes import (
    Array,
    Bits,
    CompatibilityError,
    Int,
    String,
    SvObject,
    EncodingDescriptor,
    unified_type_name,
    checked_unpack,
    get_package,
    schema_descriptor,
    svobj,
    encoding_descriptor,
)


def test_bits_shape_has_distinct_identity_but_flat_encoding_bytes():
    shaped = Bits((2, 8))
    flat = Bits(16)

    assert unified_type_name(shaped) == "svtypes.Bits[shape=(2,8),signed=false,state=2state]"
    assert unified_type_name(flat) == "svtypes.Bits[width=16,signed=false,state=2state]"
    assert shaped.pack(0x1234) == flat.pack(0x1234)
    assert encoding_descriptor(shaped) != encoding_descriptor(flat)


def test_schema_and_encoding_fingerprints_have_separate_policy_behavior():
    baseline = schema_descriptor(Int(rand=True, dump=True))
    changed = schema_descriptor(Int(rand=False, dump=False))

    assert baseline.schema_fingerprint != changed.schema_fingerprint
    assert baseline.encoding_fingerprint == changed.encoding_fingerprint
    assert len(baseline.schema_fingerprint) == 32
    assert len(baseline.schema_fingerprint_hex) == 64


def test_pack_bytes_changes_object_encoding_fields_and_fingerprint():
    class AllFields(SvObject):
        first = Int()
        second = Int()

    class OneField(SvObject):
        first = Int()
        second = Int(pack_bytes=False)

    all_fields = schema_descriptor(AllFields)
    one_field = schema_descriptor(OneField)

    assert len(all_fields.encoding_schema["fields"]) == 2
    assert len(one_field.encoding_schema["fields"]) == 1
    assert all_fields.encoding_fingerprint != one_field.encoding_fingerprint


def test_registered_object_uses_qualified_package_type_id():
    package = get_package("descriptor_test")
    package.clear()

    @svobj(registry=package)
    class Payload(SvObject):
        value = Array(String(), 2)

    assert unified_type_name(Payload) == "descriptor_test.Payload"
    assert schema_descriptor(Payload).unified_type_name == "descriptor_test.Payload"


def test_encoding_descriptor_text_roundtrip_and_immutability():
    descriptor = encoding_descriptor(Bits((2, 8)))
    restored = EncodingDescriptor.from_dict(descriptor.to_dict())

    assert restored == descriptor
    with pytest.raises(FrozenInstanceError):
        descriptor.binary_format_version = 7

    class Payload(SvObject):
        value = Int()

    schema = schema_descriptor(Payload)
    with pytest.raises(TypeError):
        schema.schema["fields"][0]["name"] = "changed"


def test_checked_unpack_rejects_before_decoding():
    codec = Int()
    descriptor = encoding_descriptor(codec)
    assert checked_unpack(codec, codec.pack(42), descriptor) == (42, 4)

    wrong_type = EncodingDescriptor("svtypes.String[encoding=utf-8]", descriptor.encoding_fingerprint)
    with pytest.raises(CompatibilityError, match="canonical type mismatch"):
        checked_unpack(codec, b"", wrong_type)

    wrong_fingerprint = EncodingDescriptor(descriptor.unified_type_name, bytes(32))
    with pytest.raises(CompatibilityError, match="encoding fingerprint mismatch"):
        checked_unpack(codec, b"", wrong_fingerprint)


def test_object_envelope_v2_rejects_fingerprint_before_target_mutation():
    class Payload(SvObject):
        data = Int()

    source = Payload()
    source.data.value = 42
    encoded = bytearray(source.to_bytes())
    assert encoded[1:5] == b"SVXO"
    assert int.from_bytes(encoded[5:7], "little") == 2

    type_name_length = int.from_bytes(encoded[17:21], "little")
    fingerprint_offset = 21 + type_name_length
    encoded[fingerprint_offset] ^= 0xFF

    target = Payload()
    target.data.value = 7
    target_id = target.svtypes_object_number
    with pytest.raises(ValueError, match="encoding fingerprint mismatch"):
        target.from_bytes(bytes(encoded))
    assert target.data.value == 7
    assert target.svtypes_object_number == target_id


def test_object_envelope_rejects_prototype_version_one():
    class Payload(SvObject):
        data = Int()

    encoded = bytearray(Payload().to_bytes())
    encoded[5:7] = (1).to_bytes(2, "little")
    with pytest.raises(ValueError, match="version mismatch"):
        Payload().unpack(bytes(encoded))


def test_generated_objects_expose_public_encoding_descriptors():
    class Payload(SvObject):
        data = Int()

    expected = encoding_descriptor(Payload)
    sv = Payload.to_sv_obj()
    cpp = Payload.to_cpp_obj()
    assert "static function svtypes_pkg::encoding_descriptor svtypes_encoding_descriptor" in sv
    assert expected.unified_type_name in sv
    assert expected.encoding_fingerprint_hex in sv
    assert "static svtypes::EncodingDescriptor svtypes_encoding_descriptor" in cpp
    assert expected.unified_type_name in cpp
    assert expected.encoding_fingerprint_hex in cpp
