import pytest

from svtypes import (
    Bits,
    DeclarationError,
    Int,
    RecordField,
    RecordSchema,
    unified_type_name,
    schema_descriptor,
    SvObject,
)


def test_record_schema_builds_an_unregistered_ordered_public_record():
    schema = RecordSchema(
        "svx.generated.bus.drive.request",
        [
            RecordField("address", Bits(32)),
            ("data", Bits(64)),
        ],
        class_name="DriveRequest",
    )
    record_type = schema.build()
    assert record_type is not None
    assert unified_type_name(record_type) == "svx.generated.bus.drive.request"
    assert [name for name, _ in record_type._SvObject__svtypes_members] == ["address", "data"]

    value = record_type()
    value.address.value = 0x10
    value.data.value = 0x20
    decoded, consumed = record_type().unpack(value.to_bytes())
    assert consumed == len(value.to_bytes())
    assert decoded.address.value == 0x10
    assert decoded.data.value == 0x20
    assert schema_descriptor(record_type).unified_type_name == schema.unified_type_name
    assert "class DriveRequest" in record_type.to_sv_obj()
    assert "struct DriveRequest" in record_type.to_cpp_obj()


def test_record_schema_validates_explicit_identity_and_ordered_fields():
    with pytest.raises(DeclarationError, match="unified_type_name"):
        RecordSchema("", [("value", Int())])
    with pytest.raises(DeclarationError, match="field name"):
        RecordSchema("example.Bad", [("not-valid", Int())])
    with pytest.raises(DeclarationError, match="unique"):
        RecordSchema("example.Duplicate", [("value", Int()), ("value", Int())])


def test_void_record_uses_no_generated_class_or_payload():
    schema = RecordSchema("svx.generated.void", [])
    assert schema.is_void
    assert schema.build() is None


def test_record_field_rename_changes_schema_not_encoding_when_layout_is_unchanged():
    before = RecordSchema("svx.generated.rename", [("old_name", Int())]).build()
    after = RecordSchema("svx.generated.rename", [("new_name", Int())]).build()

    assert before is not None and after is not None
    before_descriptor = schema_descriptor(before)
    after_descriptor = schema_descriptor(after)
    assert before_descriptor.schema_fingerprint != after_descriptor.schema_fingerprint
    assert before_descriptor.encoding_fingerprint == after_descriptor.encoding_fingerprint


def test_user_field_named_params_does_not_collide_with_svobject_parameter_metadata():
    class UserValue(SvObject):
        params = Int()

    value = UserValue()
    value.params.value = 7
    decoded, consumed = UserValue().unpack(value.to_bytes())
    assert consumed == len(value.to_bytes())
    assert decoded.params.value == 7
