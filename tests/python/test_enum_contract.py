import pytest

from svtypes import DecodeError, DeclarationError, Enum, SvObject, schema_descriptor


class UnsignedState(Enum, width=8, signed=False):
    IDLE = 0
    LAST = 255


class SignedState(Enum, width=16, signed=True):
    NEGATIVE = -2
    POSITIVE = 300


def test_enum_requires_explicit_supported_width_and_signedness():
    with pytest.raises(DeclarationError, match="must declare width"):
        class MissingWidth(Enum):
            VALUE = 0

    with pytest.raises(DeclarationError, match="explicitly declare signed"):
        class MissingSigned(Enum, width=8):
            VALUE = 0

    with pytest.raises(DeclarationError, match="width=8, 16, 32, or 64"):
        class UnsupportedWidth(Enum, width=24, signed=False):
            VALUE = 0


def test_enum_rejects_duplicate_and_out_of_range_members():
    with pytest.raises(DeclarationError, match="duplicate"):
        class Duplicate(Enum, width=8, signed=False):
            FIRST = 1
            SECOND = 1

    with pytest.raises(DeclarationError, match="does not fit"):
        class OutOfRange(Enum, width=8, signed=True):
            VALUE = 128


def test_enum_bytes_and_invalid_value_policy_are_explicit():
    unsigned = UnsignedState()
    assert unsigned.pack(UnsignedState.LAST) == b"\xff"
    assert unsigned.unpack(b"\xff") == (UnsignedState.LAST, 1)

    signed = SignedState()
    assert signed.pack(SignedState.NEGATIVE) == b"\xfe\xff"
    assert signed.unpack(b"\xfe\xff") == (SignedState.NEGATIVE, 2)

    with pytest.raises(DecodeError, match="encoded value 1"):
        unsigned.unpack(b"\x01")


def test_enum_schema_and_backend_declarations_retain_width_and_signedness():
    descriptor = schema_descriptor(SignedState())
    assert descriptor.encoding_schema["width"] == 16
    assert descriptor.encoding_schema["signed"] is True
    assert descriptor.encoding_schema["state_domain"] == "2state"
    assert "typedef enum bit signed [15:0]" in SignedState.to_sv_enum()
    assert "enum class SignedState : int16_t" in SignedState.to_cpp_enum()


def test_enum_plusarg_accepts_member_names_and_validates_numeric_values():
    class Payload(SvObject):
        state = SignedState()

    generated = Payload.to_sv_obj()
    assert '"NEGATIVE": state = NEGATIVE;' in generated
    assert "$sscanf(__svtypes_enum_text_0, \"%d\"" in generated
    assert "Invalid enum plusarg state=%s" in generated
