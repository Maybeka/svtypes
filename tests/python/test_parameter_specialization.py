import subprocess
import tempfile
from pathlib import Path

from svtypes import SvObject, Parameter, Int, svobj, Package

@svobj
class ConfigurableData(SvObject):
    ID = Parameter()(0)
    data = Int()

InlineConfigurableData = ConfigurableData.specialize(emit_class=False, ID=5)

@svobj
class ConfigHolder(SvObject):
    child = InlineConfigurableData()

def test_parameter_specialization():
    print("Testing Parameter Specialization...")

    # Check default value
    assert ConfigurableData.ID.value == 0

    # Specialize
    Specialized = ConfigurableData.specialize(ID=42)
    assert Specialized.ID.value == 42
    assert Specialized.__name__ == "ConfigurableData_ID_42"

    # Original should remain unchanged
    assert ConfigurableData.ID.value == 0

    # Instances
    inst = Specialized()
    assert inst.ID.value == 42

    # Immutability check (Direct assignment blocked)
    try:
        Specialized.ID.value = 100
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass

    print("Specialization and Immutability Passed!")

def test_parameter_codegen():
    print("Testing Parameter Codegen...")

    sv_code = ConfigurableData.to_sv_obj()
    print("--- SV Class ---")
    print(sv_code)
    assert "class ConfigurableData #(parameter int ID = 32'd0) extends svtypes_pkg::sv_object;" in sv_code
    assert sv_code.count("parameter int ID = 32'd0") == 1

    cpp_code = ConfigurableData.to_cpp_obj()
    print("\n--- C++ Struct ---")
    print(cpp_code)
    assert "template <int32_t ID = 0>" in cpp_code
    assert "struct ConfigurableData : public svtypes::SvObject {" in cpp_code
    assert "static constexpr int32_t ID" not in cpp_code

    Specialized = ConfigurableData.specialize(ID=42)
    sv_spec_code = Specialized.to_sv_obj()
    assert "class ConfigurableData_ID_42 extends ConfigurableData#(.ID(32'd42));" in sv_spec_code
    assert f'pack_object_header("{Specialized._encoding_type_name()}"' in sv_spec_code

    cpp_spec_code = Specialized.to_cpp_obj()
    assert "struct ConfigurableData_ID_42 : public ConfigurableData<42> {" in cpp_spec_code
    assert f'pack_object_header("{Specialized._encoding_type_name()}"' in cpp_spec_code

    holder_cpp_code = ConfigHolder.to_cpp_obj()
    assert "ConfigurableData<5> child;" in holder_cpp_code
    assert "ConfigurableData_ID_5 child;" not in holder_cpp_code

    holder_sv_code = ConfigHolder.to_sv_obj()
    assert "ConfigurableData#(.ID(32'd5)) child;" in holder_sv_code
    assert "object_packer#(ConfigurableData#(.ID(32'd5)))::pack(child, bytes);" in holder_sv_code
    assert "object_packer#(ConfigurableData#(.ID(32'd5)))::unpack(child, bytes, offset);" in holder_sv_code
    assert "ConfigurableData_ID_5 child;" not in holder_sv_code

    try:
        InlineConfigurableData.to_sv_obj()
        assert False, "Non-emitted specializations should not generate standalone SV classes"
    except NotImplementedError:
        pass

    print("Codegen Passed!")


def test_parameterized_cpp_generated_parity():
    Specialized = ConfigurableData.specialize(ID=42)

    spec = Specialized()
    spec.data.value = 100
    holder = ConfigHolder()
    holder.child.data.value = 200

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "spec_in.bin").write_bytes(spec.to_bytes())
        (tmp_path / "holder_in.bin").write_bytes(holder.to_bytes())
        (tmp_path / "model.hpp").write_text(
            "#pragma once\n"
            '#include "svtypes.hpp"\n\n'
            f"{ConfigurableData.to_cpp_obj()}\n\n"
            f"{Specialized.to_cpp_obj()}\n\n"
            f"{ConfigHolder.to_cpp_obj()}\n",
            encoding="utf-8",
        )
        (tmp_path / "main.cpp").write_text(
            r'''
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include "model.hpp"

static std::vector<uint8_t> read_file(const char* path) {
    std::ifstream is(path, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());
}

static void write_file(const char* path, const std::vector<uint8_t>& bytes) {
    std::ofstream os(path, std::ios::binary);
    os.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}

static void require(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << msg << std::endl;
        std::exit(2);
    }
}

int main() {
    ConfigurableData_ID_42 spec;
    std::vector<uint8_t> spec_bytes = read_file("spec_in.bin");
    size_t offset = 0;
    spec.unpack(spec_bytes, offset);
    require(offset == spec_bytes.size(), "spec offset mismatch");
    require(spec.data == 100, "spec data mismatch");
    spec.data = 101;
    std::vector<uint8_t> spec_out;
    spec.pack(spec_out);
    write_file("spec_out.bin", spec_out);

    ConfigHolder holder;
    std::vector<uint8_t> holder_bytes = read_file("holder_in.bin");
    offset = 0;
    holder.unpack(holder_bytes, offset);
    require(offset == holder_bytes.size(), "holder offset mismatch");
    require(holder.child.data == 200, "holder child mismatch");
    holder.child.data = 201;
    std::vector<uint8_t> holder_out;
    holder.pack(holder_out);
    write_file("holder_out.bin", holder_out);
    return 0;
}
''',
            encoding="utf-8",
        )

        compile_result = subprocess.run(
            ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "param_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            ["./param_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stderr + run_result.stdout

        out_spec = Specialized()
        out_spec.from_bytes((tmp_path / "spec_out.bin").read_bytes())
        out_holder = ConfigHolder()
        out_holder.from_bytes((tmp_path / "holder_out.bin").read_bytes())

    assert out_spec.data.value == 101
    assert out_holder.child.data.value == 201

def test_package_parameter_immutability():
    print("Testing Package Parameter Immutability...")
    pkg = Package("const_pkg")
    pkg.VERSION = Parameter()(1)

    assert pkg.VERSION.value == 1

    try:
        pkg.VERSION = 2
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass
    print("Package Parameter Immutability Passed!")

if __name__ == "__main__":
    test_parameter_specialization()
    test_parameter_codegen()
    test_package_parameter_immutability()
