import pytest

from svtypes import (
    Bit,
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
            RecordField("address", Bit[32]()),
            ("data", Bit[64]()),
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


def test_explicit_record_underscore_field_is_preserved_and_instance_local():
    record = RecordSchema('example.ExplicitFields', [('_native', Int()), ('plain', Int())]).build()
    first, second = record(), record()
    first._native.value = 19
    first.plain.value = 23
    assert second._native.value == 0
    assert [field['name'] for field in schema_descriptor(record).schema['fields']] == ['_native', 'plain']
    payload = first.to_bytes()
    decoded, consumed = record().unpack(payload)
    assert consumed == len(payload)
    assert decoded._native.value == 19
    assert decoded.plain.value == 23
    assert 'int _native;' in record.to_sv_obj()
    assert '_native' in record.to_cpp_obj()
    with pytest.raises(AttributeError, match='Direct assignment'):
        first._native = Int()


def test_record_sv_field_references_do_not_bind_pack_unpack_locals():
    record = RecordSchema('example.CollisionFields',
                          [(name, Int()) for name in ('bytes', 'offset', 'result', 'present')]).build()
    source = record.to_sv_obj()
    for name in ('bytes', 'offset', 'result', 'present'):
        assert f'::pack(this.{name}, bytes);' in source
        assert f'::unpack(this.{name}, bytes, offset);' in source
def test_generated_record_dump_qualifies_shadowed_collection_member():
    from svtypes import AssocArray, Queue, RemoteRef, String

    record = RecordSchema('test.ShadowedResult', (
        ('result', AssocArray[String, Queue[RemoteRef['sv://test/Item']]](cov=False)),
    ), class_name='ShadowedResult').build()
    text = record.to_sv_obj()
    assert 'this.result.first(' in text
    assert 'foreach (this.result[' in text
    assert 'this.result[__svtypes_key_0][__svtypes_index_1].object_number' in text
