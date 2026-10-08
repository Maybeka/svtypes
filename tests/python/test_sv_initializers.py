import pytest

from svtypes import Array, Bit, Enum, Int, Logic, Real, ShortReal, String, SvStruct, sv_declaration


class InitializerMode(Enum[Bit[8]]):
    IDLE = 0
    ACTIVE = 7


@pytest.mark.parametrize("codec, changed, expected", [
    (String('quote " slash\\ newline\n'), "changed", r'"quote \" slash\\ newline\n"'),
    (Real(2.5), 9.0, "2.5"),
    (ShortReal(-1.25), 4.0, "-1.25"),
    (InitializerMode(InitializerMode.ACTIVE), InitializerMode.IDLE, "ACTIVE"),
])
def test_sv_declaration_uses_initial_value_not_later_mutable_value(codec, changed, expected):
    codec.value = changed
    assert codec.sv_initializer() == expected
    assert sv_declaration(codec, "field", include_initializer=True).endswith(" = " + expected)
    assert " = " not in sv_declaration(codec, "field")


@pytest.mark.parametrize("codec", [String(), Real(), ShortReal(), InitializerMode()])
def test_unspecified_initializer_retains_native_default(codec):
    assert codec.sv_initializer() is None
    assert " = " not in sv_declaration(codec, "field", include_initializer=True)


def test_string_literal_preserves_utf8_bytes_and_control_characters():
    value = String("\u4e2d\x00\t\r")
    assert value.sv_initializer() == r'"\344\270\255\000\t\r"'
    assert value.to_sv_code(name="field").endswith(" = " + value.sv_initializer() + ";")


class DefaultHeader(SvStruct):
    tag = Bit[8](7)
    flags = Logic[4]()


class DefaultPacket(SvStruct):
    header = DefaultHeader()
    count = Bit[8](9)


def test_struct_type_declaration_does_not_contain_member_initializers():
    declaration = DefaultHeader.to_sv_obj().split("} DefaultHeader_t;", 1)[0]
    assert " = " not in declaration


def test_nested_struct_field_initializer_uses_declared_defaults():
    codec = DefaultPacket()
    codec.header.tag.value = 99
    codec.count.value = 42
    assert codec.sv_initializer() == "'{header: '{tag: 8'h7, flags: 'x}, count: 8'h9}"
    assert sv_declaration(codec, "packet", include_initializer=True).endswith(
        " = " + codec.sv_initializer()
    )


@pytest.mark.parametrize("shape, expected", [
    (2, "'{default: '{tag: 8'h7, flags: 'x}}"),
    ((2, 3), "'{default: '{default: '{tag: 8'h7, flags: 'x}}}"),
])
def test_fixed_array_uses_element_declared_defaults(shape, expected):
    codec = Array[DefaultHeader, shape]()
    element = codec[0] if shape == 2 else codec[0][0]
    element.tag.value = 99
    assert codec.sv_initializer() == expected
    assert sv_declaration(codec, "headers", include_initializer=True).endswith(" = " + expected)
    assert codec.to_sv_code(name="headers").endswith(" = " + expected + ";")


def test_fixed_array_unspecified_packed_element_stays_declaration_only():
    assert Array[Bit[8], 2]().sv_initializer() is None
    assert Array[Logic[8], 2]().sv_initializer() is None
    assert Array[Int, 2]().sv_initializer() == "'{default: 32'd0}"
