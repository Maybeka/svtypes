import subprocess
import tempfile
from pathlib import Path

import pytest

from svtypes import Bit, DecodeError, DeclarationError, Enum, Signed, SvObject, schema_descriptor


class UnsignedState(Enum[Bit[8]]):
    IDLE = 0
    LAST = 255


class SignedState(Enum[Bit[16, Signed]]):
    NEGATIVE = -2
    POSITIVE = 300


def test_enum_uses_sv_default_int_base_and_rejects_invalid_explicit_shape():
    class DefaultBase(Enum):
        VALUE = 0

    assert DefaultBase().width == 32
    assert DefaultBase().signed is True

    with pytest.raises(DeclarationError, match="base width must be a positive integer"):
        class InvalidWidth(Enum[Bit[0]]):
            VALUE = 0

    with pytest.raises(TypeError, match=r"Enum\[Bit\[width"):
        class MissingSigned(Enum, width=8):
            VALUE = 0

    class ThreeBit(Enum[Bit[3]]):
        VALUE = 7

    assert ThreeBit().pack(ThreeBit.VALUE) == b"\x07"
    assert ThreeBit().unpack(b"\x07") == (ThreeBit.VALUE, 1)


def test_enum_rejects_duplicate_and_out_of_range_members():
    with pytest.raises(DeclarationError, match="duplicate"):
        class Duplicate(Enum[Bit[8]]):
            FIRST = 1
            SECOND = 1

    with pytest.raises(DeclarationError, match="does not fit"):
        class OutOfRange(Enum[Bit[8, Signed]]):
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


def test_arbitrary_width_enum_cpp_wrapper_compiles_and_round_trips():
    class Tiny(Enum[Bit[3]]):
        ZERO = 0
        LAST = 7

    class Packet(SvObject):
        state = Tiny()

    packet = Packet()
    packet.state.value = Tiny.LAST
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "input.bin").write_bytes(packet.to_bytes())
        (root / "model.hpp").write_text(
            "#pragma once\n#include \"svtypes.hpp\"\n\n"
            + Tiny.to_cpp_enum()
            + "\n\n"
            + Packet.to_cpp_obj(),
            encoding="utf-8",
        )
        (root / "main.cpp").write_text(
            """
#include <fstream>
#include <iterator>
#include <vector>
#include \"model.hpp\"
int main() {
  std::ifstream is("input.bin", std::ios::binary);
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(is)), {});
  Packet packet; size_t offset = 0; packet.unpack(bytes, offset);
  if (offset != bytes.size() || packet.state != Tiny::LAST) return 2;
  packet.state = Tiny::ZERO;
  std::vector<uint8_t> output; packet.pack(output);
  std::ofstream os("output.bin", std::ios::binary);
  os.write(reinterpret_cast<const char*>(output.data()), output.size());
  return 0;
}
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "g++", "-std=c++20", "main.cpp", "-I.",
                f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "enum_test",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        result = subprocess.run(["./enum_test"], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        out = Packet()
        out.from_bytes((root / "output.bin").read_bytes())
    assert out.state.value is Tiny.ZERO


def test_enum_plusarg_accepts_member_names_and_validates_numeric_values():
    class Payload(SvObject):
        state = SignedState()

    generated = Payload.to_sv_obj()
    assert '"NEGATIVE": state = NEGATIVE;' in generated
    assert "$sscanf(__svtypes_enum_text_0, \"%d\"" in generated
    assert "Invalid enum plusarg state=%s" in generated
