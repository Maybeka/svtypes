import subprocess
from pathlib import Path

from svtypes import Logic, LogicValue, Reg, SvObject, encoding_descriptor, schema_descriptor, unified_type_name


def test_four_state_truth_table_and_three_plane_bytes():
    codec = Logic(4)
    value = LogicValue.from_string("10xz")
    assert str(value) == "10xz"
    assert codec.pack(value) == bytes([0b1000, 0b0010, 0b0001])
    assert codec.unpack(codec.pack(value)) == (value, 3)
    assert codec.state_domain == "4state"


def test_multidimensional_logic_shape_is_preserved_with_flat_bytes():
    shaped = Logic((2, 4))
    flat = Logic(8)
    value = LogicValue.from_string("10xz01zx")
    assert shaped.width == 8
    assert shaped.shape == (2, 4)
    assert shaped.sv_decl("data") == "logic [1:0] [3:0] data"
    assert shaped.pack(value) == flat.pack(value)
    assert unified_type_name(shaped) == "svtypes.Logic[shape=(2,4),signed=false,state=4state]"


def test_cpp_logic_bits_three_plane_roundtrip(tmp_path: Path):
    source = tmp_path / "main.cpp"
    source.write_text(
        '#include "svtypes.hpp"\n'
        "int main() {\n"
        "  svtypes::LogicValue<4> in; in.value[0]=8; in.x[0]=2; in.z[0]=1;\n"
        "  std::vector<uint8_t> bytes; svtypes::pack(in, bytes);\n"
        "  svtypes::LogicValue<4> out; size_t offset=0; svtypes::unpack(out,bytes,offset);\n"
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


def test_logic_bits_signed_changes_identity_and_declarations():
    unsigned = Logic(4)
    signed = Logic(4, signed=True)
    assert unsigned.signed is False
    assert signed.signed is True
    assert unsigned.sv_decl("x") == "logic [3:0] x"
    assert signed.sv_decl("x") == "logic signed [3:0] x"
    assert unsigned.cpp_decl("x") == "svtypes::LogicValue<4, false> x"
    assert signed.cpp_decl("x") == "svtypes::LogicValue<4, true> x"
    assert unified_type_name(unsigned) == "svtypes.Logic[width=4,signed=false,state=4state]"
    assert unified_type_name(signed) == "svtypes.Logic[width=4,signed=true,state=4state]"
    from svtypes import encoding_descriptor

    assert encoding_descriptor(unsigned) != encoding_descriptor(signed)
    assert unsigned.pack(LogicValue.from_string("10xz")) == signed.pack(LogicValue.from_string("10xz"))


def test_reg_is_a_logic_runtime_value_with_legacy_sv_declaration_spelling():
    logic = Logic(4, signed=True)
    reg = Reg(4, signed=True)

    assert type(reg) is type(logic)
    assert isinstance(logic, Reg)
    assert isinstance(reg, Reg)
    assert issubclass(Logic, Reg)
    assert logic.sv_decl("logic_data") == "logic signed [3:0] logic_data"
    assert reg.sv_decl("reg_data") == "reg signed [3:0] reg_data"
    assert logic.pack("10xz") == reg.pack("10xz")
    assert encoding_descriptor(logic) == encoding_descriptor(reg)
    assert schema_descriptor(logic).schema_fingerprint != schema_descriptor(reg).schema_fingerprint


def test_generated_reg_field_preserves_legacy_keyword():
    class LegacyRegister(SvObject):
        data = Reg(8)

    code = LegacyRegister.to_sv_obj()
    assert "rand reg [7:0] data;" in code


def test_generated_signed_logic_field():
    class SignedLogic(SvObject):
        data = Logic(8, signed=True)

    code = SignedLogic.to_sv_obj()
    assert "rand logic signed [7:0] data;" in code


def test_generated_logic_field_supports_rand_plusarg_and_coverage():
    class LogicPayload(SvObject):
        data = Logic((2, 4))

    code = LogicPayload.to_sv_obj()
    assert "rand logic [1:0] [3:0] data;" in code
    assert '"data=%h"' in code
    assert "data_cp: coverpoint item.data;" in code
    assert "logic_packer#(logic [1:0] [3:0])::pack(data, bytes);" in code
