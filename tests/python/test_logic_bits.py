import subprocess
from pathlib import Path

from svtypes import LogicBits, LogicValue, SvObject, unified_type_name


def test_four_state_truth_table_and_three_plane_bytes():
    codec = LogicBits(4)
    value = LogicValue.from_string("10xz")
    assert str(value) == "10xz"
    assert codec.pack(value) == bytes([0b1000, 0b0010, 0b0001])
    assert codec.unpack(codec.pack(value)) == (value, 3)
    assert codec.state_domain == "4state"


def test_multidimensional_logic_shape_is_preserved_with_flat_bytes():
    shaped = LogicBits((2, 4))
    flat = LogicBits(8)
    value = LogicValue.from_string("10xz01zx")
    assert shaped.width == 8
    assert shaped.shape == (2, 4)
    assert shaped.sv_decl("data") == "logic [1:0] [3:0] data"
    assert shaped.pack(value) == flat.pack(value)
    assert unified_type_name(shaped) == "svtypes.LogicBits[shape=(2,4),state=4state]"


def test_cpp_logic_bits_three_plane_roundtrip(tmp_path: Path):
    source = tmp_path / "main.cpp"
    source.write_text(
        '#include "svtypes.hpp"\n'
        "int main() {\n"
        "  svtypes::LogicBitsValue<4> in; in.value[0]=8; in.x[0]=2; in.z[0]=1;\n"
        "  std::vector<uint8_t> bytes; svtypes::pack(in, bytes);\n"
        "  svtypes::LogicBitsValue<4> out; size_t offset=0; svtypes::unpack(out,bytes,offset);\n"
        "  return out == in && offset == 3 ? 0 : 1;\n"
        "}\n"
    )
    executable = tmp_path / "logic_bits"
    result = subprocess.run(
        ["g++", "-std=c++20", str(source), "-I", str(Path.cwd() / "svtypes_runtime" / "cpp"), "-o", str(executable)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert subprocess.run([str(executable)]).returncode == 0


def test_generated_logic_field_supports_rand_plusarg_and_coverage():
    class LogicPayload(SvObject):
        data = LogicBits((2, 4))

    code = LogicPayload.to_sv_obj()
    assert "rand logic [1:0] [3:0] data;" in code
    assert '"data=%h"' in code
    assert "data_cp: coverpoint item.data;" in code
    assert "logic_bits_packer#(logic [1:0] [3:0])::pack(data, bytes);" in code
