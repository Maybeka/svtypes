import subprocess
import tempfile
from pathlib import Path

import pytest

from svtypes import (
    Bit,
    DeclarationError,
    Int,
    LongInt,
    Package,
    ParamRef,
    Parameter,
    Real,
    ShortReal,
    String,
    SvObject,
    schema_descriptor,
    svobj,
)

@svobj
class ConfigurableData(SvObject):
    ID = Parameter()(0)
    data = Int()

InlineConfigurableData = ConfigurableData.specialize(ID=5)

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
    # A specialization is a Python-side binding; it never emits a generated
    # SV/C++ class (the target-language type is ConfigurableData#(.ID(42))).
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Specialized.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Specialized.to_cpp_obj()

    holder_cpp_code = ConfigHolder.to_cpp_obj()
    assert "ConfigurableData<5> child;" in holder_cpp_code
    assert "ConfigurableData_ID_5 child;" not in holder_cpp_code

    holder_sv_code = ConfigHolder.to_sv_obj()
    assert "ConfigurableData#(.ID(32'd5)) child;" in holder_sv_code
    assert "object_packer#(ConfigurableData#(.ID(32'd5)))::pack(child, bytes);" in holder_sv_code
    assert "object_packer#(ConfigurableData#(.ID(32'd5)))::unpack(child, bytes, offset);" in holder_sv_code
    assert "ConfigurableData_ID_5 child;" not in holder_sv_code

    with pytest.raises(DeclarationError, match="Python-side binding"):
        InlineConfigurableData.to_sv_obj()

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
    ConfigurableData<42> spec;
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


def test_unbound_template_definition_and_specialization_codegen():
    """An unbound parameter class is a template: its parameterized
    definition is generated and specializations are just parameter
    references resolved by the target language."""
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8)

    assert Templated._SvObject__svtypes_is_template is True
    sv = Templated.to_sv_obj()
    assert "class Templated #(parameter int WIDTH) extends svtypes_pkg::sv_object;" in sv
    cpp = Templated.to_cpp_obj()
    assert "template <int32_t WIDTH>" in cpp
    assert "struct Templated : public svtypes::SvObject {" in cpp

    Spec = Templated.specialize(WIDTH=4)
    assert Spec.__name__ == "Templated_WIDTH_4"
    # A specialization is a Python-side binding; no generated class exists.
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_cpp_obj()

    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    assert Kind._SvObject__svtypes_is_template is True
    assert "class Kind #(parameter type T) extends svtypes_pkg::sv_object;" in Kind.to_sv_obj()
    assert "template <typename T>" in Kind.to_cpp_obj()

    class Payload(SvObject):
        x = Bit(4)

    KindSpec = Kind.specialize(T=Payload)
    assert KindSpec.__name__ == "Kind_T_Payload"
    with pytest.raises(DeclarationError, match="Python-side binding"):
        KindSpec.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        KindSpec.to_cpp_obj()
    assert schema_descriptor(KindSpec).unified_type_name.endswith(
        ".Kind[T:type=Payload]"
    )


def test_member_only_specialization_references_template():
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8)

    MemberOnly = Templated.specialize(WIDTH=8)

    class Holder(SvObject):
        child = MemberOnly()

    sv = Holder.to_sv_obj()
    assert "Templated#(.WIDTH(32'd8)) child;" in sv
    assert "Templated_WIDTH_8 child;" not in sv
    cpp = Holder.to_cpp_obj()
    assert "Templated<8> child;" in cpp
    assert "Templated_WIDTH_8 child;" not in cpp


def test_unbound_template_requires_type_declaration_for_codegen():
    class Anonymous(SvObject):
        W = Parameter()
        data = Bit(8)

    try:
        Anonymous.to_sv_obj()
        assert False, "untyped unbound parameter should be rejected at codegen"
    except Exception as exc:
        assert "declare a type" in str(exc)


def test_template_and_specialization_cpp_compile_parity():
    """Generated template definition + specialization + member-only field
    compile and roundtrip through the C++ runtime."""
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8)

    Spec = Templated.specialize(WIDTH=4)
    MemberOnly = Templated.specialize(WIDTH=8)

    class Holder(SvObject):
        child = MemberOnly()

    spec = Spec()
    spec.data.value = 0xAB
    holder = Holder()
    holder.child.data.value = 0xCD

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "spec_in.bin").write_bytes(spec.to_bytes())
        (tmp_path / "holder_in.bin").write_bytes(holder.to_bytes())
        (tmp_path / "model.hpp").write_text(
            "#pragma once\n"
            '#include "svtypes.hpp"\n\n'
            f"{Templated.to_cpp_obj()}\n\n"
            f"{Holder.to_cpp_obj()}\n",
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

int main() {
    Templated<4> spec;
    std::vector<uint8_t> spec_bytes = read_file("spec_in.bin");
    size_t offset = 0;
    spec.unpack(spec_bytes, offset);
    if (spec.data != 0xAB) return 1;
    spec.data = 0x11;
    std::vector<uint8_t> spec_out;
    spec.pack(spec_out);
    write_file("spec_out.bin", spec_out);

    Holder holder;
    std::vector<uint8_t> holder_bytes = read_file("holder_in.bin");
    offset = 0;
    holder.unpack(holder_bytes, offset);
    if (holder.child.data != 0xCD) return 2;
    holder.child.data = 0x22;
    std::vector<uint8_t> holder_out;
    holder.pack(holder_out);
    write_file("holder_out.bin", holder_out);
    return 0;
}
''',
            encoding="utf-8",
        )

        compile_result = subprocess.run(
            ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "templ_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr
        run_result = subprocess.run(
            ["./templ_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stderr + run_result.stdout

        out_spec = Spec()
        out_spec.from_bytes((tmp_path / "spec_out.bin").read_bytes())
        out_holder = Holder()
        out_holder.from_bytes((tmp_path / "holder_out.bin").read_bytes())

    assert out_spec.data.value == 0x11
    assert out_holder.child.data.value == 0x22


def test_dtype_template_definition_matrix():
    """Each declared dtype renders the matching SV/C++ parameter declaration."""
    cases = [
        (Int, "parameter int W", "template <int32_t W>"),
        (LongInt, "parameter longint W", "template <int64_t W>"),
        (String, "parameter string W", None),
        (Real, "parameter real W", None),
        (ShortReal, "parameter shortreal W", None),
        (type, "parameter type W", "template <typename W>"),
    ]
    for dtype, expected_sv_param, expected_cpp in cases:
        name = f"Tpl{dtype.__name__}"
        namespace = {"SvObject": SvObject, "Bit": Bit, "Parameter": Parameter, "dtype": dtype}
        exec(
            f"class {name}(SvObject):\n    W = Parameter(dtype)\n    data = Bit(8)",
            namespace,
        )
        cls = namespace[name]
        assert cls._SvObject__svtypes_is_template is True
        sv = cls.to_sv_obj()
        assert f"class {name} #({expected_sv_param}) extends svtypes_pkg::sv_object;" in sv.splitlines()[0], (
            dtype,
            sv.splitlines()[0],
        )
        if expected_cpp is None:
            with pytest.raises(DeclarationError, match="C\\+\\+ template parameter"):
                cls.to_cpp_obj()
        else:
            assert expected_cpp in cls.to_cpp_obj(), (dtype, cls.to_cpp_obj().splitlines()[0])


def test_mixed_value_and_type_parameters():
    class Mixed(SvObject):
        N = Parameter(Int)
        T = Parameter(type)
        data = Bit(8)

    assert "class Mixed #(parameter int N, parameter type T) extends svtypes_pkg::sv_object;" in Mixed.to_sv_obj()
    assert "template <int32_t N, typename T>" in Mixed.to_cpp_obj()

    class Payload(SvObject):
        x = Bit(4)

    Spec = Mixed.specialize(N=4, T=Payload)
    assert Spec.__name__ == "Mixed_N_4_T_Payload"
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_cpp_obj()


def test_template_and_specializations_share_encoding_fingerprint():
    """Encoding fingerprints are parameter-independent layout digests."""
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8)

    fp = Templated._encoding_fingerprint_hex()
    assert len(fp) == 64
    assert fp == Templated.specialize(WIDTH=4)._encoding_fingerprint_hex()
    assert fp == Templated.specialize(WIDTH=8)._encoding_fingerprint_hex()
    assert fp == Templated.specialize(WIDTH=16)._encoding_fingerprint_hex()
    assert fp != "0" * 64


def test_str_and_float_specializations_reject_cpp_generation():
    class StrTpl(SvObject):
        S = Parameter(String)
        data = Bit(8)

    class FloatTpl(SvObject):
        V = Parameter(Real)
        data = Bit(8)

    str_spec = StrTpl.specialize(S="abc")
    # The specialization is a Python-side binding; only the template generates
    # SV, and C++ generation is rejected for str/float parameters.
    with pytest.raises(DeclarationError, match="Python-side binding"):
        str_spec.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        str_spec.to_cpp_obj()
    with pytest.raises(DeclarationError, match="str"):
        StrTpl.to_cpp_obj()

    float_spec = FloatTpl.specialize(V=1.5)
    with pytest.raises(DeclarationError, match="Python-side binding"):
        float_spec.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        float_spec.to_cpp_obj()
    with pytest.raises(DeclarationError, match="float"):
        FloatTpl.to_cpp_obj()


def test_specialize_rejects_mismatched_bindings():
    class WVal(SvObject):
        W = Parameter(Int)
        data = Bit(8)

    with pytest.raises(TypeError, match="value parameter"):
        WVal.specialize(W=int)

    class TVar(SvObject):
        T = Parameter(type)
        data = Bit(8)

    with pytest.raises(TypeError, match="type parameter"):
        TVar.specialize(T=5)
    with pytest.raises(TypeError, match="type parameter"):
        TVar.specialize(T="abc")

    with pytest.raises(AttributeError, match="no parameter named"):
        WVal.specialize(W=1, BOGUS=1)


def test_type_parameter_schema_entries():
    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    class Payload(SvObject):
        x = Bit(4)

    Spec = Kind.specialize(T=Payload)
    params = [dict(item) for item in schema_descriptor(Spec).schema["parameters"]]
    assert params == [{"kind": "type", "name": "T", "value": "Payload"}]
    assert schema_descriptor(Spec).unified_type_name.endswith(".Kind[T:type=Payload]")


def test_package_generates_unbound_template_and_type_parameter():
    class Tpl(SvObject):
        W = Parameter(Int)
        data = Bit(8)

    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    pkg = Package("param_pkg")
    pkg._types["Tpl"] = Tpl
    pkg._types["Kind"] = Kind
    cpp = pkg.to_cpp_pkg()
    assert "template <int32_t W> struct Tpl;" in cpp
    assert "template <int32_t W>\n  struct Tpl" in cpp
    assert "template <typename T> struct Kind;" in cpp
    sv = pkg.to_sv_pkg()
    assert "class Tpl #(parameter int W) extends svtypes_pkg::sv_object;" in sv
    assert "class Kind #(parameter type T) extends svtypes_pkg::sv_object;" in sv

    class Payload(SvObject):
        x = Bit(4)

    pkg2 = Package("type_pkg")
    pkg2._types["Kind"] = Kind
    pkg2._types["Payload"] = Payload
    out = pkg2.to_cpp_pkg()
    # A bound type parameter inside a package is a nested typename alias.
    assert "template <typename T> struct Kind;" in out


def test_type_parameter_cpp_compile_parity():
    """Type parameters compile and roundtrip through C++."""
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8)

    Spec = Templated.specialize(WIDTH=4)

    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    class Payload(SvObject):
        x = Bit(4)

    KindSpec = Kind.specialize(T=Payload)

    spec = Spec()
    spec.data.value = 0xAB

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "spec_in.bin").write_bytes(spec.to_bytes())
        (tmp_path / "model.hpp").write_text(
            "#pragma once\n"
            '#include "svtypes.hpp"\n\n'
            f"{Templated.to_cpp_obj()}\n\n"
            f"{Kind.to_cpp_obj()}\n\n"
            f"{Payload.to_cpp_obj()}\n",
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

int main() {
    Templated<4> spec;
    std::vector<uint8_t> spec_bytes = read_file("spec_in.bin");
    size_t offset = 0;
    spec.unpack(spec_bytes, offset);
    if (spec.data != 0xAB) return 1;
    spec.data = 0x11;
    std::vector<uint8_t> spec_out;
    spec.pack(spec_out);
    write_file("spec_out.bin", spec_out);

    Kind<Payload> kind;
    kind.data = 0x5A;
    std::vector<uint8_t> kind_out;
    kind.pack(kind_out);
    // A bare type-parameter instance has no stable cross-language type name
    // (C++ typeid is mangled), so this roundtrip is same-language only.
    size_t ko = 0;
    Kind<Payload> kind2;
    kind2.unpack(kind_out, ko);
    if (kind2.data != 0x5A) return 2;
    return 0;
}
''',
            encoding="utf-8",
        )
        compile_result = subprocess.run(
            ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "typed_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr
        run_result = subprocess.run(
            ["./typed_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stderr + run_result.stdout

        out_spec = Spec()
        out_spec.from_bytes((tmp_path / "spec_out.bin").read_bytes())

    assert out_spec.data.value == 0x11

if __name__ == "__main__":
    test_parameter_specialization()
    test_parameter_codegen()
    test_package_parameter_immutability()


def test_specialization_cannot_be_re_specialized():
    """A bound specialization is a Python-side binding; re-specializing it
    would produce Tpl_W_4_W_16, which is not a template. Re-specialize the
    original template instead."""
    class Tpl(SvObject):
        W = Parameter(Int)
        data = Bit(8)

    S = Tpl.specialize(W=4)
    assert S.W.value == 4
    assert dict(S._SvObject__svtypes_params)["W"].value == 4
    with pytest.raises(DeclarationError, match="already a specialization"):
        S.specialize(W=16)
    # Default-valued parameter classes (not specializations) stay re-specializable.
    class Defaulted(SvObject):
        W = Parameter(Int)(4)
        data = Bit(8)

    assert Defaulted.specialize(W=16).W.value == 16


def test_specialize_preserves_declared_dtype():
    class Wide(SvObject):
        W = Parameter(LongInt)
        data = Bit(8)

    assert Wide.W.dtype == "longint"
    Spec = Wide.specialize(W=5)
    assert Spec.W.dtype == "longint"
    # The template itself renders the longint declaration; the specialization
    # is a Python-side binding and generates nothing.
    assert "class Wide #(parameter longint W) extends svtypes_pkg::sv_object;" in Wide.to_sv_obj().splitlines()[0]
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_sv_obj()


def test_bound_str_default_template_rejects_cpp_generation():
    class StrDef(SvObject):
        S = Parameter("hello")
        data = Bit(8)

    assert 'class StrDef #(parameter string S = "hello") extends svtypes_pkg::sv_object;' in StrDef.to_sv_obj()
    with pytest.raises(DeclarationError, match="C\\+\\+ template parameter"):
        StrDef.to_cpp_obj()


def test_inheriting_unbound_template_is_rejected():
    class Base(SvObject):
        W = Parameter(Int)
        data = Bit(8)

    with pytest.raises(DeclarationError, match="cannot inherit unbound parameter template"):
        class Child(Base):
            x = Bit(4)

    # Inheriting a bound specialization is fine; the subclass flattens to the
    # original template rather than referencing the specialization class.
    class Child2(Base.specialize(W=8)):
        x = Bit(4)
    assert Child2._SvObject__svtypes_params == []
    assert "class Child2 extends Base#(.W(32'd8));" in Child2.to_sv_obj()
    assert "struct Child2 : public Base<8> {" in Child2.to_cpp_obj()


def test_paramref_forwarding_flattens_to_original_template():
    class Base(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

    # Same-name forward.
    class Same(Base.specialize(WIDTH=ParamRef())):
        WIDTH = Parameter(Int)
        n = Parameter(Int)

    assert Same._SvObject__svtypes_param_refs == {"WIDTH": "WIDTH"}
    sv = Same.to_sv_obj()
    assert "class Same #(parameter int WIDTH, parameter int n) extends Base#(.WIDTH(WIDTH));" in sv.splitlines()[0]
    assert "extends Base#(.WIDTH(WIDTH));" in sv
    cpp = Same.to_cpp_obj()
    assert "template <int32_t WIDTH, int32_t n>" in cpp
    assert "struct Same : public Base<WIDTH> {" in cpp

    # Renamed forward.
    class Renamed(Base.specialize(WIDTH=ParamRef("W"))):
        W = Parameter(Int)
        n = Parameter(Int)

    assert Renamed._SvObject__svtypes_param_refs == {"WIDTH": "W"}
    assert "class Renamed #(parameter int W, parameter int n) extends Base#(.WIDTH(W));" in Renamed.to_sv_obj().splitlines()[0]
    assert "struct Renamed : public Base<W> {" in Renamed.to_cpp_obj()

    # The forwarding subclass is a template itself and can be specialized.
    Spec = Same.specialize(WIDTH=4, n=2)
    assert Spec.WIDTH.value == 4


def test_paramref_requires_declared_subclass_parameter():
    class Base(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

    with pytest.raises(DeclarationError, match="undeclared parameter"):
        class Bad(Base.specialize(WIDTH=ParamRef())):
            data = Bit(8)


def test_paramref_dtype_mismatch_is_rejected():
    class Base(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

    with pytest.raises(DeclarationError, match="dtype mismatch"):
        class Bad(Base.specialize(WIDTH=ParamRef("W"))):
            W = Parameter(String)
            data = Bit(8)


def test_type_parameter_forwarding():
    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    class Sub(Kind.specialize(T=ParamRef())):
        T = Parameter(type)
        payload = Bit(8)

    assert Sub._SvObject__svtypes_param_refs == {"T": "T"}
    assert "class Sub #(parameter type T) extends Kind#(.T(T));" in Sub.to_sv_obj().splitlines()[0]
    assert "struct Sub : public Kind<T> {" in Sub.to_cpp_obj()


def test_user_subclass_keeps_own_parameters():
    """class Child(Base.specialize(A=8)) with its own parameter still renders
    the parameter list and can be specialized."""
    class Base(SvObject):
        A = Parameter(Int)
        data = Bit(8)

    class Child(Base.specialize(A=8)):
        B = Parameter(Int)
        x = Bit(4)

    assert "class Child #(parameter int B) extends Base#(.A(32'd8));" in Child.to_sv_obj().splitlines()[0]
    assert "template <int32_t B>\nstruct Child : public Base<8> {" in Child.to_cpp_obj()
    S = Child.specialize(B=3)
    assert S.B.value == 3
    assert S.__name__ == "Child_B_3"


def test_paramref_intermediate_is_not_instantiable():
    class Base(SvObject):
        A = Parameter(Int)
        data = Bit(8)

    Mid = Base.specialize(A=ParamRef())
    assert Mid._SvObject__svtypes_is_template is True
    with pytest.raises(DeclarationError, match="parameterized template"):
        Mid()
    with pytest.raises(DeclarationError, match="parameterized template"):
        schema_descriptor(Mid)
    with pytest.raises(DeclarationError, match="parameterized template"):
        class Holder(SvObject):
            child = Mid()
