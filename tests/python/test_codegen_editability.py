"""Compile/run prototypes for generated-code editability mechanisms."""

from pathlib import Path
import shutil
import subprocess
import pytest

from svtypes import Array, Bit, Int, Package, Parameter, SvObject, SvStruct, generate, unified_type_name


def test_generated_cpp_business_edits_compile_and_roundtrip_without_regeneration(tmp_path):
    class EditableBusiness(SvObject):
        code = Bit[8]()

    generated = EditableBusiness.to_cpp_obj()
    assert "  uint8_t code;" in generated
    edited = generated.replace(
        "  uint8_t code;",
        "  uint8_t code = 5;\n  unsigned user_value() const { return code + 1; }",
        1,
    )
    source = tmp_path / "editable_business.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + edited + r'''
int main() {
    EditableBusiness source, restored;
    assert(source.code == 5 && source.user_value() == 6);
    source.code = 7;
    std::vector<uint8_t> bytes;
    source.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.code == 7 && restored.user_value() == 8 && offset == bytes.size());
}
''')
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler, "C++ compiler required for editability qualification"
    executable = tmp_path / "editable_business"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_generated_cpp_renamed_fields_do_not_shadow_codec_locals(tmp_path):
    class EditableNames(SvObject):
        payload = Bit[8]()
        position = Bit[16]()

    generated = EditableNames.to_cpp_obj()
    assert "uint8_t payload;" in generated and "uint16_t position;" in generated
    edited = generated.replace("uint8_t payload;", "uint8_t bytes;", 1)
    edited = edited.replace("uint16_t position;", "uint16_t offset;", 1)
    edited = edited.replace('operation("payload", self.payload,', 'operation("bytes", self.bytes,', 1)
    edited = edited.replace('operation("position", self.position,', 'operation("offset", self.offset,', 1)
    assert "self.payload" not in edited and "self.position" not in edited
    source = tmp_path / "shadow.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + edited + r'''
int main() {
    EditableNames original, restored;
    original.bytes = 0x12; original.offset = 0x3456;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.bytes == 0x12 && restored.offset == 0x3456 && offset == bytes.size());
    const auto display = restored.svtypes_sprint();
    assert(display.find("bytes=") != std::string::npos && display.find("offset=") != std::string::npos);
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "shadow"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_cpp_single_field_list_preserves_const_and_derives_changed_types(tmp_path):
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler, "C++ compiler required for editability qualification"
    source = r'''
#include "svtypes.hpp"
#include <cassert>
#include <array>
struct Record {
    svtypes::BitValue<13, false> opcode;
    std::array<uint16_t, 3> data;
    template<class Self, class Operation>
    static void fields(Self& self, Operation&& operation) {
        operation(self.opcode);
        operation(self.data);
    }
};
int main() {
    Record original{};
    original.opcode = 0x1234;
    original.data.fill(0x1234);
    const Record& input = original;
    std::vector<uint8_t> bytes;
    Record::fields(input, [&](const auto& value) { svtypes::pack(value, bytes); });
    Record decoded{};
    size_t offset = 0;
    Record::fields(decoded, [&](auto& value) { svtypes::unpack(value, bytes, offset); });
    std::vector<uint8_t> again;
    Record::fields(decoded, [&](const auto& value) { svtypes::pack(value, again); });
    assert(bytes == again);
    assert(offset == bytes.size());
    assert(bytes.size() == EXPECTED_SIZE);
    assert(decoded.data == original.data);
}
'''
    # Only declarations change; the shared field list and both operations stay intact.
    for name, text, size in (
        ("original", source, 8),
        ("edited", source.replace("<13, false>", "<21, false>")
                         .replace("uint16_t, 3", "uint16_t, 5"), 13),
    ):
        path = tmp_path / f"{name}.cpp"
        path.write_text(text.replace("EXPECTED_SIZE", str(size)))
        executable = tmp_path / name
        subprocess.run([compiler, "-std=c++20", "-O2", "-I", str(root / "svtypes_runtime/cpp"),
                        str(path), "-o", str(executable)], check=True, capture_output=True, text=True)
        subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_generated_cpp_struct_can_change_width_and_array_declaration_without_codec_edits(tmp_path):
    class EditableRecord(SvStruct):
        opcode = Bit[65]()
        data = Array[Bit[16], 3]()

    generated = EditableRecord.to_cpp_obj()
    changed = generated.replace("BitValue<65, false>", "BitValue<73, false>")
    changed = changed.replace("uint16_t, 3>", "uint16_t, 5>")
    assert changed != generated
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler
    for name, body, size in (("original", generated, 15), ("edited", changed, 20)):
        source = tmp_path / f"{name}.cpp"
        source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + body + f'''
int main() {{
    const EditableRecord original{{}};
    std::vector<uint8_t> bytes;
    pack(original, bytes);
    assert(bytes.size() == {size});
    EditableRecord result{{}};
    size_t offset = 0;
    unpack(result, bytes, offset);
    std::vector<uint8_t> again;
    pack(result, again);
    assert(offset == bytes.size() && again == bytes);
}}
''')
        executable = tmp_path / name
        subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                        str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
        subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_generated_cpp_object_count_follows_edited_wire_list(tmp_path):
    class EditableObject(SvObject):
        first = Bit[8]()
        second = Bit[8]()

    generated = EditableObject.to_cpp_obj()
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler
    for name, text, count in (
        ("original", generated, 2),
        ("edited", "\n".join(line for line in generated.splitlines() if 'operation("second",' not in line), 1),
    ):
        source = tmp_path / f"{name}.cpp"
        source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + text + f'''
int main() {{
    EditableObject value;
    assert(value.__svtypes_wire_field_count() == {count});
    std::vector<uint8_t> bytes;
    value.pack_body(bytes);
    assert(bytes.at(6) == {count} && bytes.at(7) == 0);
}}
''')
        executable = tmp_path / name
        subprocess.run([compiler, "-std=c++20", "-O2", "-I", str(root / "svtypes_runtime/cpp"),
                        str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
        subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_generated_cpp_counts_fields_on_encode_and_strict_decode_only(tmp_path):
    class CountedObject(SvObject):
        number = Bit[8]()

    generated = CountedObject.to_cpp_obj()
    signature = "size_t __svtypes_wire_field_count() const {"
    assert generated.count(signature) == 1
    generated = generated.replace(signature, signature + "\n    ++wire_count_calls;", 1)
    source = tmp_path / "count_fields.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cassert>\n'
                      'static unsigned wire_count_calls = 0;\n' + generated + r'''
int main() {
    CountedObject original, restored;
    original.number = 42;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    assert(wire_count_calls == 1);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.number == 42 && wire_count_calls == 1);
    svtypes::DecodeContext strict{svtypes::DiagnosticPolicy::strict};
    svtypes::DecodeContextGuard scope(strict);
    offset = 0;
    restored.unpack_body(bytes, offset);
    assert(wire_count_calls == 2);
    bytes[6] = 2;
    offset = 0;
    try { restored.unpack_body(bytes, offset); assert(false); }
    catch (const std::runtime_error& error) {
        assert(std::string(error.what()).find("field-count mismatch") != std::string::npos);
    }
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "count_fields"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("struct", [False, True])
def test_multifile_cpp_support_compiles_without_generation_manifest(tmp_path, struct):
    class SplitObject(SvStruct if struct else SvObject):
        number = Bit[32]()

    output = tmp_path / "generated"
    result = generate(SplitObject, output, targets={"cpp"})
    assert not result.failed
    assert any(path.startswith("support/") for path in result.written)
    (output / "svtypes-manifest.json").unlink()
    source = tmp_path / "main.cpp"
    bench = r'''
#include "SplitObject.hpp"
#include <cassert>
int main() {
    SplitObject original, restored;
    original.number = 42;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.number == 42 && offset == bytes.size());
}
'''
    if struct:
        bench = bench.replace("original.pack_body(bytes);", "pack(original, bytes);")
        bench = bench.replace("restored.unpack_body(bytes, offset);", "unpack(restored, bytes, offset);")
    source.write_text(bench)
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler
    executable = tmp_path / "main"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_multifile_cpp_template_remains_editable_without_a_concrete_schema(tmp_path):
    import json

    class EditableTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8]()

    output = tmp_path / "generated"
    result = generate(EditableTemplate, output, targets={"cpp", "schema"})
    assert not result.failed
    schema = json.loads((output / "EditableTemplate.schema.json").read_text())
    manifest = json.loads((output / "svtypes-manifest.json").read_text())
    assert schema["types"] == []
    assert schema["unbound_templates"] == manifest["unbound_templates"] == [unified_type_name(EditableTemplate)]
    header = output / "EditableTemplate.hpp"
    text = header.read_text()
    assert "uint8_t data;" in text
    header.write_text(text.replace("uint8_t data;", "uint16_t data;", 1))
    source = tmp_path / "template.cpp"
    source.write_text(r'''
#include "EditableTemplate.hpp"
#include <cassert>
int main() {
    EditableTemplate<4> source, restored;
    source.data = 0x1234;
    std::vector<uint8_t> bytes;
    source.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.data == 0x1234 && offset == bytes.size());
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "template"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_multifile_cpp_template_and_specialized_member_share_edited_declaration(tmp_path):
    class PacketTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8]()

    specialized = PacketTemplate.specialize(WIDTH=4)

    class PacketHolder(SvObject):
        child = specialized()

    output = tmp_path / "generated"
    result = generate([PacketTemplate, PacketHolder], output, targets={"cpp"})
    assert not result.failed
    header = output / "schema.hpp"
    original = header.read_text()
    assert original.count("uint8_t data;") == 1
    header.write_text(original.replace("uint8_t data;", "uint16_t data;", 1))
    source = tmp_path / "template_dependency.cpp"
    source.write_text(r'''
#include "schema.hpp"
#include <cassert>
int main() {
    PacketHolder original, restored;
    original.child.data = 0x1234;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.child.data == 0x1234 && offset == bytes.size());
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "template_dependency"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_multifile_cpp_type_parameter_field_follows_edited_declaration(tmp_path):
    class TypePacket(SvObject):
        T = Parameter[type]()
        value = T()

    output = tmp_path / "generated"
    result = generate(TypePacket, output, targets={"cpp", "schema"})
    assert not result.failed
    header = output / "TypePacket.hpp"
    original = header.read_text()
    assert "T value;" in original
    header.write_text(original.replace("T value;", "std::array<T, 2> value;", 1))
    source = tmp_path / "type_parameter.cpp"
    source.write_text(r'''
#include "TypePacket.hpp"
#include <cassert>
int main() {
    TypePacket<uint16_t> original, restored;
    original.value = {0x1234, 0x5678};
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.value == original.value && offset == bytes.size());
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "type_parameter"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_multifile_cpp_package_template_edit_keeps_namespace_and_dependency(tmp_path):
    package = Package("editable_pkg")

    class NamespaceTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8]()

    package.register(NamespaceTemplate)
    specialized = NamespaceTemplate.specialize(WIDTH=4)

    class NamespaceHolder(SvObject):
        child = specialized()

    package.register(NamespaceHolder)
    output = tmp_path / "generated"
    result = generate(package, output, targets={"cpp"})
    assert not result.failed
    header = output / "editable_pkg.hpp"
    original = header.read_text()
    assert "namespace editable_pkg" in original and original.count("uint8_t data;") == 1
    header.write_text(original.replace("uint8_t data;", "uint16_t data;", 1))
    source = tmp_path / "namespace.cpp"
    source.write_text(r'''
#include "editable_pkg.hpp"
#include <cassert>
int main() {
    editable_pkg::NamespaceHolder original, restored;
    original.child.data = 0x1234;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.child.data == 0x1234 && offset == bytes.size());
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "namespace"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_multifile_cpp_inherits_registered_specialized_template_after_edit(tmp_path):
    package = Package("inherit_pkg")

    class ZBase(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8]()

    package.register(ZBase)

    class AChild(ZBase.specialize(WIDTH=4)):
        tail = Bit[8]()

    package.register(AChild)
    output = tmp_path / "generated"
    result = generate(package, output, targets={"cpp"})
    assert not result.failed
    header = output / "inherit_pkg.hpp"
    original = header.read_text()
    assert original.index("struct ZBase :") < original.index("struct AChild :")
    assert original.count("uint8_t data;") == 1
    header.write_text(original.replace("uint8_t data;", "uint16_t data;", 1))
    source = tmp_path / "inherit.cpp"
    source.write_text(r'''
#include "inherit_pkg.hpp"
#include <cassert>
int main() {
    inherit_pkg::AChild original, restored;
    original.data = 0x1234;
    original.tail = 0x56;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.data == 0x1234 && restored.tail == 0x56 && offset == bytes.size());
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "inherit"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    "-I", str(output), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_cpp_struct_physical_field_deletion_only_changes_declaration_and_shared_list(tmp_path):
    class EditableDeletion(SvStruct):
        first = Bit[8]()
        second = Bit[16]()

    generated = EditableDeletion.to_cpp_obj()
    assert "uint16_t second;" in generated
    edited = generated.replace("uint16_t second;", "")
    edited = "\n".join(line for line in edited.splitlines() if 'operation("second",' not in line)
    assert "self.second" not in edited
    source = tmp_path / "deleted.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + edited + r'''
int main() {
    EditableDeletion value{}, restored{};
    value.first = 7;
    std::vector<uint8_t> bytes;
    pack(value, bytes);
    assert(bytes == std::vector<uint8_t>{7});
    size_t offset = 0;
    unpack(restored, bytes, offset);
    assert(restored.first == 7 && offset == 1);
    assert(dump_value(restored) == "EditableDeletion{first=7}");
}
''')
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler
    executable = tmp_path / "deleted"
    subprocess.run([compiler, "-std=c++20", "-O2", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_cpp_object_physical_field_deletion_updates_codec_count_and_dump(tmp_path):
    class EditableDeletion(SvObject):
        first = Bit[8]()
        second = Bit[16]()

    generated = EditableDeletion.to_cpp_obj()
    assert "uint16_t second;" in generated
    edited = generated.replace("uint16_t second;", "")
    edited = "\n".join(line for line in edited.splitlines() if 'operation("second",' not in line)
    assert "self.second" not in edited
    source = tmp_path / "deleted_object.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cassert>\n' + edited + r'''
int main() {
    EditableDeletion value, restored;
    value.first = 7;
    std::vector<uint8_t> bytes;
    value.pack_body(bytes);
    assert(value.__svtypes_wire_field_count() == 1 && bytes.back() == 7);
    size_t offset = 0;
    restored.unpack_body(bytes, offset);
    assert(restored.first == 7 && offset == bytes.size());
    auto display = restored.svtypes_sprint();
    assert(display.find("first=7") != std::string::npos);
    assert(display.find("second=") == std::string::npos);
}
''')
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler
    executable = tmp_path / "deleted_object"
    subprocess.run([compiler, "-std=c++20", "-O2", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
