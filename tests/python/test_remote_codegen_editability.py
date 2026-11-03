"""Native target qualification of field-list language mechanisms."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest
from svtypes import Array, AssocArray, Bit, BitSigned, CodecSession, CovPoint, DynArray, Enum, Int, Logic, LongInt, Object, ObjectRegistry, Package, Parameter, Queue, Real, RemoteRef, ShortReal, String, SvObject, SvPackedStruct, SvStruct, UnpackContext, bins, constraint, covergroup, generate


def _run_target(name, source, marker, expected_error=None, run_args=()):
    root = Path(__file__).resolve().parents[2]
    output = root / ".tmp" / name
    output.mkdir(parents=True, exist_ok=True)
    (output / "tb.sv").write_text(source)
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    remote = os.environ["SVTYPES_REMOTE_SV_ROOT"].rstrip("/") + "/.tmp/" + name
    runner = shlex.join(shlex.split(os.environ["SVTYPES_REMOTE_SV_RUNNER"]))
    for operation in ("compile tb.sv", "run"):
        arguments = " " + shlex.join(run_args) if operation == "run" and run_args else ""
        command = f"cd {shlex.quote(remote)} && {runner} {operation}{arguments}"
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", host,
             "bash -ilc " + shlex.quote(command)], capture_output=True, text=True,
        )
        log = output / ("target-" + operation.split()[0] + ".log")
        log.write_text(result.stdout + result.stderr)
        if operation == "run" and expected_error is not None:
            assert expected_error in result.stdout + result.stderr, f"expected diagnostic missing; private diagnostic: {log}"
            assert "UNEXPECTED_ACCEPTANCE" not in result.stdout + result.stderr, (
                f"invalid input continued after diagnostic; private diagnostic: {log}"
            )
            return
        assert result.returncode == 0, f"target operation failed; private diagnostic: {log}"
    assert marker in result.stdout, (
        f"field visitor success marker missing; private diagnostic: {log}"
    )


@pytest.mark.remote_sv
def test_remote_generated_business_edits_work_without_regeneration():
    class EditableBehavior(SvObject):
        code = Bit[8](3)

        @constraint
        def legal(self):
            self.code == 3

    source = EditableBehavior.to_sv_obj(use_type_reference=True)
    assert "code = 8'h3;" in source
    assert "code == 8'd3" in source
    source = source.replace("code = 8'h3;", "code = 8'h5;", 1)
    source = source.replace("code == 8'd3", "code == 8'd7", 1)
    # Add a user method in the class body; neither change touches its support code.
    source = source.replace("  rand bit [7:0] code",
                            "  function int user_value(); return code + 1; endfunction\n"
                            "  rand bit [7:0] code", 1)
    bench = r'''
module tb;
  initial begin
    EditableBehavior source, restored;
    byte unsigned bytes[$];
    int offset;
    source = new(); restored = new();
    if (source.code != 5 || source.user_value() != 6) $fatal(1, "edited default or method");
    if (!source.randomize() || source.code != 7 || source.user_value() != 8)
      $fatal(1, "edited constraint or method");
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (restored.code != 7 || offset != bytes.size()) $fatal(1, "edited business roundtrip");
    $display("SVTYPES_EDITED_BUSINESS_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-business", source + bench, "SVTYPES_EDITED_BUSINESS_PASS")


@pytest.mark.remote_sv
def test_remote_renamed_fields_do_not_shadow_codec_locals():
    class EditableNames(SvObject):
        payload = Bit[8](rand=False, cov=False)
        position = Bit[16](rand=False, cov=False)

    source = EditableNames.to_sv_obj(use_type_reference=True)
    assert "bit [7:0] payload;" in source and "bit [15:0] position;" in source
    source = source.replace("bit [7:0] payload;", "bit [7:0] bytes;", 1)
    source = source.replace("bit [15:0] position;", "bit [15:0] offset;", 1)
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if "::apply(this.payload," in line:
            lines[index] = line.replace("this.payload", "this.bytes").replace('"payload"', '"bytes"')
        elif "::apply(this.position," in line:
            lines[index] = line.replace("this.position", "this.offset").replace('"position"', '"offset"')
    source = "\n".join(lines)
    assert "this.payload" not in source and "this.position" not in source
    bench = r'''
module tb;
  initial begin
    EditableNames original, restored;
    byte unsigned bytes[$];
    int offset;
    string expected;
    original = new(); restored = new();
    original.bytes = 'h12; original.offset = 'h3456;
    original.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (restored.bytes !== 'h12 || restored.offset !== 'h3456 || offset != bytes.size())
      $fatal(1, "shadowed field roundtrip");
    expected = $sformatf("EditableNames#%0d{bytes=12, offset=3456}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected) $fatal(1, "shadowed field display");
    $display("SVTYPES_SHADOWED_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-shadowed-fields", source + "\n" + bench,
                "SVTYPES_SHADOWED_FIELDS_PASS")


@pytest.mark.remote_sv
def test_remote_cpp_compiler_handles_edited_shared_cyclic_graph():
    registry = ObjectRegistry()

    class NativeChild(SvObject):
        number = Bit[8](rand=False, cov=False)
        next_node = Object["NativeChild"](registry=registry)

    registry.register(NativeChild)

    class NativeGraph(SvObject):
        left = Object["NativeChild"](registry=registry)
        right = Object["NativeChild"](registry=registry)

    child = NativeChild.to_cpp_obj()
    assert "uint8_t number;" in child
    child = child.replace("uint8_t number;", "uint16_t number;", 1)
    source = '#include "svtypes.hpp"\n#include <cassert>\n' + child + "\n" + NativeGraph.to_cpp_obj() + r'''
int main() {
    NativeChild child;
    NativeGraph original, restored;
    child.number = 0x1234; child.next_node = &child;
    original.left = &child; original.right = &child;
    std::vector<uint8_t> bytes;
    original.pack(bytes);
    svtypes::clear_object_registry();
    size_t offset = 0;
    restored.unpack(bytes, offset);
    assert(offset == bytes.size() && restored.left && restored.left == restored.right);
    assert(restored.left->number == 0x1234 && restored.left->next_node == restored.left);
    delete restored.left;
}
'''
    root = Path(__file__).resolve().parents[2]
    output = root / ".tmp" / "codegen-editability-native-compiler"
    output.mkdir(parents=True, exist_ok=True)
    (output / "main.cpp").write_text(source)
    remote_root = os.environ["SVTYPES_REMOTE_SV_ROOT"].rstrip("/")
    remote_dir = remote_root + "/.tmp/codegen-editability-native-compiler"
    compiler = shlex.join(["g++", "-std=c++20", "-O2", "-I", remote_root + "/svtypes_runtime/cpp",
                           "main.cpp", "-o", "native-test"])
    command = f"cd {shlex.quote(remote_dir)} && {compiler} && ./native-test"
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                             os.environ["SVTYPES_REMOTE_SV_HOST"], "bash -ilc " + shlex.quote(command)],
                            capture_output=True, text=True)
    log = output / "private-native.log"
    log.write_text(result.stdout + result.stderr)
    assert result.returncode == 0, f"native target compilation or execution failed; private diagnostic: {log}"


@pytest.mark.remote_sv
def test_remote_generated_coverage_edit_changes_samples_without_regeneration():
    class EditableCoverage(SvObject):
        code = Bit[2](cov=False)

        @covergroup
        def cg(self):
            class values(CovPoint, source=self.code):
                zero = bins[0]
                one = bins[1]

    source = EditableCoverage.to_sv_obj(use_type_reference=True)
    assert "bins one = {1};" in source
    source = source.replace("bins one = {1};", "bins one = {2};", 1)
    bench = r'''
module tb;
  initial begin
    EditableCoverage item;
    EditableCoverage::EditableCoverage__cg__coverage cov;
    item = new(); cov = new();
    item.code = 0; cov.sample(item);
    item.code = 1; cov.sample(item);
    if (cov.get_coverage() != 50.0) $fatal(1, "unedited bin unexpectedly sampled");
    item.code = 2; cov.sample(item);
    if (cov.get_coverage() != 100.0) $fatal(1, "edited bin was not sampled");
    $display("SVTYPES_EDITED_COVERAGE_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-coverage-business", source + bench, "SVTYPES_EDITED_COVERAGE_PASS")


@pytest.mark.remote_sv
def test_remote_generated_counts_fields_on_encode_and_strict_decode_only():
    class CountedObject(SvObject):
        number = Bit[8](rand=False, cov=False)

    source = CountedObject.to_sv_obj(use_type_reference=True)
    signature = "  function int __svtypes_wire_field_count();"
    declaration = "    int unused_offset = 0;"
    assert source.count(signature) == 1 and source.count(declaration) == 1
    source = source.replace("class CountedObject extends svtypes_pkg::sv_object;",
                            "class CountedObject extends svtypes_pkg::sv_object;\n  int count_calls;", 1)
    source = source.replace(declaration, declaration + "\n    count_calls++;", 1)
    bench = r'''
module tb;
  initial begin
    CountedObject original, restored;
    svtypes_pkg::decode_context strict_context, previous;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new(); original.number = 42;
    original.pack_body(bytes);
    if (original.count_calls != 0 || bytes[6] != 1 || bytes[7] != 0)
      $fatal(1, "encode field count or extra traversal");
    restored.unpack_body(bytes, offset);
    if (restored.number != 42 || restored.count_calls != 0) $fatal(1, "normal decode counted");
    strict_context = new(svtypes_pkg::DIAGNOSTICS_STRICT);
    previous = svtypes_pkg::begin_decode_context(strict_context);
    offset = 0; restored.unpack_body(bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    if (restored.count_calls != 1) $fatal(1, "strict decode count");
    $display("SVTYPES_DECODE_FIELD_COUNT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-decode-count", source + bench, "SVTYPES_DECODE_FIELD_COUNT_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("old_runtime", [False, True])
def test_remote_rejects_runtime_without_generated_wire_helpers(old_runtime):
    bench = r'''
module tb;
  initial begin
    svtypes_pkg::runtime_capabilities received;
    string required[$];
    received = svtypes_pkg::get_runtime_capabilities();
    required.push_back("svtypes.generated-wire-helpers.v1");
    if (OLD_RUNTIME) begin
      foreach (received.provided[i]) begin
        if (received.provided[i] == "svtypes.generated-wire-helpers.v1") begin
          received.provided.delete(i); break;
        end
      end
    end
    svtypes_pkg::require_runtime_compatible(required, received);
    $display("MARKER");
    $finish;
  end
endmodule
'''.replace("OLD_RUNTIME", "1" if old_runtime else "0")
    bench = bench.replace("MARKER", "UNEXPECTED_ACCEPTANCE" if old_runtime else "SVTYPES_WIRE_CAPABILITY_PASS")
    _run_target("codegen-editability-wire-capability-" + ("old" if old_runtime else "current"),
                bench, "SVTYPES_WIRE_CAPABILITY_PASS",
                expected_error="missing required capability" if old_runtime else None)


@pytest.mark.remote_sv
def test_remote_field_count_backfill_respects_buffer_prefix_and_disabled_fields():
    class PrefixRecord(SvObject):
        keep = Bit[8](rand=False, cov=False)
        hidden = Bit[8](rand=False, cov=False, pack_bytes=False)
        tail = Bit[16](rand=False, cov=False)

    class EmptyRecord(SvObject):
        pass

    source = PrefixRecord.to_sv_obj(use_type_reference=True) + "\n" + EmptyRecord.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    PrefixRecord original, first, second;
    EmptyRecord empty, empty_copy;
    svtypes_pkg::decode_context state, previous;
    byte unsigned bytes[$];
    int offset, second_start, empty_start;
    original = new(); first = new(); second = new(); empty = new(); empty_copy = new();
    original.keep = 'h12; original.hidden = 'h34; original.tail = 'h5678;
    bytes.push_back('ha5); bytes.push_back('h5a);
    original.pack_body(bytes);
    second_start = bytes.size(); original.pack_body(bytes);
    empty_start = bytes.size(); empty.pack_body(bytes);
    if (bytes[0] != 'ha5 || bytes[1] != 'h5a || bytes[8] != 2 || bytes[9] != 0 ||
        bytes[second_start+6] != 2 || bytes[second_start+7] != 0 ||
        bytes[empty_start+6] != 0 || bytes[empty_start+7] != 0)
      $fatal(1, "field count backfill or buffer prefix");
    state = new(svtypes_pkg::DIAGNOSTICS_STRICT);
    previous = svtypes_pkg::begin_decode_context(state);
    offset = 2; first.unpack_body(bytes, offset);
    if (offset != second_start || first.keep !== 'h12 || first.tail !== 'h5678)
      $fatal(1, "first record roundtrip");
    second.unpack_body(bytes, offset);
    if (offset != empty_start || second.keep !== 'h12 || second.tail !== 'h5678)
      $fatal(1, "second record roundtrip");
    empty_copy.unpack_body(bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    if (offset != bytes.size()) $fatal(1, "empty record roundtrip");
    $display("SVTYPES_PREFIX_FIELD_COUNT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-prefix-count", source + bench, "SVTYPES_PREFIX_FIELD_COUNT_PASS")


@pytest.mark.remote_sv
def test_remote_field_count_backfill_crosses_byte_boundary():
    fields = {f"field_{index:03d}": Bit[8](rand=False, cov=False, plusarg=False, dump=False)
              for index in range(260)}
    many = type("ManyFields", (SvObject,), fields)
    bench = r'''
module tb;
  initial begin
    ManyFields original, restored;
    svtypes_pkg::decode_context state, previous;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new();
    original.field_000 = 'h12; original.field_259 = 'h34;
    original.pack_body(bytes);
    if (bytes[6] != 4 || bytes[7] != 1) $fatal(1, "260-field header count");
    state = new(svtypes_pkg::DIAGNOSTICS_STRICT);
    previous = svtypes_pkg::begin_decode_context(state);
    restored.unpack_body(bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    if (restored.field_000 != 'h12 || restored.field_259 != 'h34 || offset != bytes.size())
      $fatal(1, "260-field roundtrip");
    $display("SVTYPES_WIDE_FIELD_COUNT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-wide-count", many.to_sv_obj(use_type_reference=True) + "\n" + bench,
                "SVTYPES_WIDE_FIELD_COUNT_PASS")


@pytest.mark.remote_sv
def test_remote_multifile_template_declaration_edit_without_regeneration():
    class EditableTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8](rand=False, cov=False)

    name = "codegen-editability-multifile-template"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate(EditableTemplate, output, targets={"sv", "schema"}, use_type_reference=True)
    assert not result.failed
    source = (output / "EditableTemplate.sv").read_text()
    assert "bit [7:0] data;" in source
    source = source.replace("bit [7:0] data;", "bit [11:0] data;", 1)
    bench = r'''
module tb;
  initial begin
    EditableTemplate#(4) original, restored;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new(); original.data = 12'habc;
    original.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (restored.data !== 12'habc || offset != bytes.size()) $fatal(1, "template edit roundtrip");
    $display("SVTYPES_EDITED_MULTIFILE_TEMPLATE_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, source + bench, "SVTYPES_EDITED_MULTIFILE_TEMPLATE_PASS")


@pytest.mark.remote_sv
def test_remote_multifile_template_and_specialized_member_follow_edit():
    class PacketTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8](rand=False, cov=False)

    specialized = PacketTemplate.specialize(WIDTH=4)

    class PacketHolder(SvObject):
        child = specialized()

    name = "codegen-editability-template-member"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate([PacketTemplate, PacketHolder], output, targets={"sv"}, use_type_reference=True)
    assert not result.failed
    source = (output / "schema.sv").read_text()
    assert source.count("bit [7:0] data;") == 1
    source = source.replace("bit [7:0] data;", "bit [11:0] data;", 1)
    bench = r'''
module tb;
  initial begin
    PacketHolder original, restored;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new(); original.child = new();
    original.child.data = 12'habc;
    original.pack_body(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack_body(bytes, offset);
    if (restored.child == null || restored.child.data !== 12'habc || offset != bytes.size())
      $fatal(1, "specialized member edit roundtrip");
    $display("SVTYPES_EDITED_TEMPLATE_MEMBER_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, source + bench, "SVTYPES_EDITED_TEMPLATE_MEMBER_PASS")


@pytest.mark.remote_sv
def test_remote_multifile_package_template_and_specialized_member_follow_edit():
    package = Package("editable_pkg")

    class NamespaceTemplate(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8](rand=False, cov=False)

    package.register(NamespaceTemplate)
    specialized = NamespaceTemplate.specialize(WIDTH=4)

    class NamespaceHolder(SvObject):
        child = specialized()

    package.register(NamespaceHolder)
    name = "codegen-editability-package-template"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate(package, output, targets={"sv"}, use_type_reference=True)
    assert not result.failed
    source = (output / "editable_pkg.sv").read_text()
    assert source.count("bit [7:0] data;") == 1
    source = source.replace("bit [7:0] data;", "bit [11:0] data;", 1)
    bench = r'''
module tb;
  initial begin
    editable_pkg::NamespaceHolder original, restored;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new(); original.child = new();
    original.child.data = 12'habc;
    original.pack_body(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack_body(bytes, offset);
    if (restored.child == null || restored.child.data !== 12'habc || offset != bytes.size())
      $fatal(1, "package template member edit roundtrip");
    $display("SVTYPES_EDITED_PACKAGE_TEMPLATE_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, source + bench, "SVTYPES_EDITED_PACKAGE_TEMPLATE_PASS")


@pytest.mark.remote_sv
def test_remote_multifile_inherits_registered_specialized_template_after_edit():
    package = Package("inherit_pkg")

    class ZBase(SvObject):
        WIDTH = Parameter[Int]()
        data = Bit[8](rand=False, cov=False)

    package.register(ZBase)

    class AChild(ZBase.specialize(WIDTH=4)):
        tail = Bit[8](rand=False, cov=False)

    package.register(AChild)
    name = "codegen-editability-template-base"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate(package, output, targets={"sv"}, use_type_reference=True)
    assert not result.failed
    source = (output / "inherit_pkg.sv").read_text()
    assert source.index("class ZBase #(") < source.index("class AChild extends")
    assert source.count("bit [7:0] data;") == 1
    source = source.replace("bit [7:0] data;", "bit [11:0] data;", 1)
    bench = r'''
module tb;
  initial begin
    inherit_pkg::AChild original, restored;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new();
    original.data = 12'habc; original.tail = 8'h56;
    original.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (restored.data !== 12'habc || restored.tail !== 8'h56 || offset != bytes.size())
      $fatal(1, "inherited template edit roundtrip");
    $display("SVTYPES_EDITED_TEMPLATE_BASE_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, source + bench, "SVTYPES_EDITED_TEMPLATE_BASE_PASS")


@pytest.mark.remote_sv
def test_remote_multifile_type_parameter_field_follows_edited_declaration():
    class TypePacket(SvObject):
        T = Parameter[type]()
        value = T()

    name = "codegen-editability-multifile-type-parameter"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate(TypePacket, output, targets={"sv", "schema"}, use_type_reference=True)
    assert not result.failed
    source = (output / "TypePacket.sv").read_text()
    assert "T value;" in source
    source = source.replace("T value;", "bit [20:0] value;", 1)
    bench = r'''
module tb;
  initial begin
    TypePacket#(bit [7:0]) original, restored;
    byte unsigned bytes[$];
    int offset;
    original = new(); restored = new(); original.value = 21'h123456;
    original.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (restored.value !== original.value || offset != bytes.size())
      $fatal(1, "type parameter declaration edit roundtrip");
    $display("SVTYPES_MULTIFILE_TYPE_PARAMETER_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, source + bench, "SVTYPES_MULTIFILE_TYPE_PARAMETER_PASS")


@pytest.mark.remote_sv
def test_remote_field_visitor_derives_types_dimensions_and_inherits():
    fixture = Path(__file__).parent / "data/codegen_editability/field_visitor.sv"
    _run_target("codegen-editability-containers", fixture.read_text(),
                "SVTYPES_EDITABILITY_CONTAINERS_PASS bytes=31")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_field", [False, True])
def test_remote_typed_helpers_share_field_list_for_codec_display_and_plusargs(remove_field):
    source = r'''
class scalar_field_ops #(type T = int, type CODEC = svtypes_pkg::bit_packer#(T));
  static function void apply(ref T value, input string name, input int action,
                             ref byte unsigned bytes[$], ref int offset,
                             ref int count, ref string result, input string prefix);
    string key;
    case (action)
      0: count++;
      1: CODEC::pack(value, bytes);
      2: CODEC::unpack(value, bytes, offset);
      3: begin
        if (result != "") result = {result, ", "};
        result = {result, name, "=", $sformatf("%0h", value)};
      end
      4: begin
        key = (prefix == "") ? {name, "=%h"} : {prefix, ".", name, "=%h"};
        if ($test$plusargs((prefix == "") ? name : {prefix, ".", name}) &&
            !$value$plusargs(key, value)) $fatal(1, "bad plusarg");
      end
    endcase
  endfunction
endclass
class array_field_ops #(type T = int, type CODEC = int);
  static function void apply(ref T value, input string name, input int action,
                             ref byte unsigned bytes[$], ref int offset,
                             ref int count, ref string result, input string prefix);
    bit first;
    case (action)
      0: count++;
      1: CODEC::pack(value, bytes);
      2: CODEC::unpack(value, bytes, offset);
      3: begin
        if (result != "") result = {result, ", "};
        result = {result, name, "=["};
        first = 1;
        foreach (value[i]) begin
          if (!first) result = {result, ", "};
          first = 0;
          result = {result, $sformatf("%0h", value[i])};
        end
        result = {result, "]"};
      end
    endcase
  endfunction
endclass
class Editable;
  bit [20:0] first;
  bit [7:0] second;
  bit [11:0] data[3];
  function void fields(input int action, ref byte unsigned bytes[$], ref int offset,
                       ref int count, ref string result, input string prefix = "");
    scalar_field_ops#(type(this.first))::apply(this.first, "first", action, bytes, offset, count, result, prefix);
    scalar_field_ops#(type(this.second))::apply(this.second, "second", action, bytes, offset, count, result, prefix);
    array_field_ops#(type(this.data), svtypes_pkg::fixed_array_packer#(type(this.data[0]), $size(this.data), svtypes_pkg::bit_packer#(type(this.data[0]))))::apply(this.data, "data", action, bytes, offset, count, result, prefix);
  endfunction
endclass
module tb;
  initial begin
    Editable a, b;
    byte unsigned bytes[$];
    int offset = 0, count = 0;
    string display = "";
    a = new(); b = new(); a.first = 21'h123456;
    a.data[0] = 'h123; a.data[1] = 'h456; a.data[2] = 'h789;
    a.fields(0, bytes, offset, count, display);
    if (count != EXPECTED_COUNT) $fatal(1, "field count");
    a.fields(1, bytes, offset, count, display);
    b.fields(2, bytes, offset, count, display);
    b.fields(3, bytes, offset, count, display);
    b.fields(4, bytes, offset, count, display);
    if (b.first !== 21'h1abcde || b.data !== a.data || offset != bytes.size()) $fatal(1, "roundtrip/plusarg");
    if (display != EXPECTED_DISPLAY) $fatal(1, "display");
    $display("SVTYPES_TYPED_FIELD_OPERATIONS_PASS");
    $finish;
  end
endmodule
'''
    if remove_field:
        source = source.replace("  bit [7:0] second;\n", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.second," not in line)
    source = source.replace("EXPECTED_COUNT", "2" if remove_field else "3")
    source = source.replace("EXPECTED_DISPLAY", '"first=123456, data=[123, 456, 789]"' if remove_field else '"first=123456, second=0, data=[123, 456, 789]"')
    _run_target("codegen-editability-typed-field-ops-" + ("removed" if remove_field else "original"),
                source, "SVTYPES_TYPED_FIELD_OPERATIONS_PASS", run_args=("+first=1abcde",))


@pytest.mark.remote_sv
@pytest.mark.parametrize("multifile", [False, True])
def test_remote_generated_bit_field_deletion_updates_all_helpers(multifile):
    class EditableDeletion(SvObject):
        first = Bit[21](rand=False, cov=False)
        second = Bit[8](rand=False, cov=False)

    target_name = "codegen-editability-deleted-field" + ("-multifile" if multifile else "-inline")
    output = Path(__file__).resolve().parents[2] / ".tmp" / target_name
    if multifile:
        result = generate(EditableDeletion, output, targets={"sv"}, use_type_reference=True, overwrite_modified=True)
        assert not result.failed
        source = (output / "EditableDeletion.sv").read_text()
        support_files = [output / name for name in result.written if name.startswith("support/")]
        fields_file = next(path for path in support_files if "__svtypes_apply_fields" in path.read_text())
        support = fields_file.read_text()
        assert support.count("::apply(this.second,") == 1
        fields_file.write_text("\n".join(line for line in support.splitlines() if "::apply(this.second," not in line) + "\n")
    else:
        source = EditableDeletion.to_sv_obj(use_type_reference=True)
        assert source.count("::apply(this.second,") == 1
        source = "\n".join(line for line in source.splitlines() if "::apply(this.second," not in line)
    assert "  bit [7:0] second;" in source
    source = source.replace("  bit [7:0] second;", "")
    bench = r'''
module tb;
  initial begin
    EditableDeletion source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string display;
    source = new(); restored = new();
    source.first = 21'h123456;
    source.pack_body(bytes);
    if (bytes[6] != 1 || bytes[7] != 0) $fatal(1, "edited field count mismatch");
    restored.unpack_body(bytes, offset);
    if (restored.first !== source.first || offset != bytes.size()) $fatal(1, "edited roundtrip mismatch");
    display = restored.svtypes_sprint();
    if (display != $sformatf("EditableDeletion#%0d{first=123456}", restored.__svtypes_object_number))
      $fatal(1, "edited display mismatch: %s", display);
    restored.apply_plusargs();
    if (restored.first !== 21'h1abcde) $fatal(1, "edited plusarg mismatch");
    $display("SVTYPES_GENERATED_FIELD_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target(target_name, source + bench, "SVTYPES_GENERATED_FIELD_DELETION_PASS", run_args=("+first=1abcde",))


@pytest.mark.remote_sv
def test_remote_shared_bit_field_operations_preserve_independent_policies():
    class EditablePolicies(SvObject):
        signed_value = BitSigned[8](rand=False, cov=False)
        display_only = Bit[8](rand=False, cov=False, pack_bytes=False, plusarg=False)
        wire_only = Bit[8](rand=False, cov=False, dump=False, plusarg=False)

    source = EditablePolicies.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditablePolicies source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string display;
    source = new(); restored = new();
    source.signed_value = -3; source.display_only = 'h55; source.wire_only = 'h66;
    source.pack_body(bytes);
    if (bytes[6] != 2 || bytes[7] != 0) $fatal(1, "field count policy");
    restored.display_only = 'h55;
    restored.unpack_body(bytes, offset);
    if (restored.signed_value !== -3 || restored.display_only !== 'h55 ||
        restored.wire_only !== 'h66 || offset != bytes.size()) $fatal(1, "wire policy");
    display = restored.svtypes_sprint();
    if (display != $sformatf("EditablePolicies#%0d{signed_value=-3, display_only=55}", restored.__svtypes_object_number))
      $fatal(1, "display policy: %s", display);
    restored.apply_plusargs("scope");
    if (restored.signed_value !== -7 || restored.display_only !== 'h55 || restored.wire_only !== 'h66)
      $fatal(1, "plusarg policy");
    $display("SVTYPES_SHARED_BIT_POLICIES_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-shared-bit-policies", source + bench,
                "SVTYPES_SHARED_BIT_POLICIES_PASS",
                run_args=("+scope.signed_value=-7", "+scope.display_only=aa", "+scope.wire_only=bb"))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_logic", [False, True])
def test_remote_shared_numeric_fields_keep_logic_and_integer_semantics(remove_logic):
    class EditableNumeric(SvObject):
        control = Bit[8](rand=False, cov=False)
        status = Logic[8](rand=False, cov=False)
        counter = Int(rand=False, cov=False)
        total = LongInt(rand=False, cov=False)

    source = EditableNumeric.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.status,") == 1
    if remove_logic:
        assert "  logic [7:0] status;" in source
        source = source.replace("  logic [7:0] status;", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.status," not in line)
    bench = r'''
module tb;
  initial begin
    EditableNumeric source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42; source.counter = -19; source.total = -123456789;
    SET_LOGIC
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "field count");
    restored.unpack_body(bytes, offset);
    if (restored.control !== source.control || restored.counter !== source.counter ||
        restored.total !== source.total || offset != bytes.size()) $fatal(1, "numeric roundtrip");
    CHECK_LOGIC
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "numeric display");
    restored.apply_plusargs();
    if (restored.counter !== -77 || restored.total !== -88) $fatal(1, "integer plusarg");
    $display("SVTYPES_SHARED_NUMERIC_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    if remove_logic:
        replacements = {
            "SET_LOGIC": "", "CHECK_LOGIC": "", "EXPECTED_COUNT": "3",
            "EXPECTED_DISPLAY": 'expected_display = $sformatf("EditableNumeric#%0d{control=42, counter=-19, total=-123456789}", restored.__svtypes_object_number);',
        }
    else:
        replacements = {
            "SET_LOGIC": "source.status = 8'b10xz0110;",
            "CHECK_LOGIC": 'if (restored.status !== source.status) $fatal(1, "4-state roundtrip");',
            "EXPECTED_COUNT": "4",
            "EXPECTED_DISPLAY": 'expected_display = $sformatf("EditableNumeric#%0d{control=42, status=%0h, counter=-19, total=-123456789}", restored.__svtypes_object_number, source.status);',
        }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-numeric-" + ("removed" if remove_logic else "original"),
                source + bench, "SVTYPES_SHARED_NUMERIC_FIELDS_PASS",
                run_args=("+counter=-77", "+total=-88"))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_dynamic", [False, True])
def test_remote_shared_numeric_collection_fields_and_deletion(remove_dynamic):
    class EditableCollections(SvObject):
        control = Bit[8](rand=False, cov=False)
        fixed = Array[BitSigned[8], 2](rand=False, cov=False)
        dynamic = DynArray[Logic[4]](rand=False, cov=False)
        queue_data = Queue[Int](rand=False, cov=False)

    source = EditableCollections.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.dynamic,") == 1
    if remove_dynamic:
        assert "  logic [3:0] dynamic [];" in source
        source = source.replace("  logic [3:0] dynamic [];", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.dynamic," not in line)
    bench = r'''
module tb;
  initial begin
    EditableCollections source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42;
    source.fixed[0] = -2; source.fixed[1] = 5;
    source.queue_data.push_back(-3); source.queue_data.push_back(7);
    SET_DYNAMIC
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "field count");
    restored.unpack_body(bytes, offset);
    if (restored.control !== source.control || restored.fixed !== source.fixed ||
        restored.queue_data !== source.queue_data || offset != bytes.size()) $fatal(1, "collection roundtrip");
    CHECK_DYNAMIC
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "collection display: %s", restored.svtypes_sprint());
    $display("SVTYPES_SHARED_COLLECTION_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_DYNAMIC": "" if remove_dynamic else "source.dynamic = new[2]; source.dynamic[0] = 4'b1xz0; source.dynamic[1] = 4'h7;",
        "CHECK_DYNAMIC": "" if remove_dynamic else 'if (restored.dynamic !== source.dynamic) $fatal(1, "dynamic roundtrip");',
        "EXPECTED_COUNT": "3" if remove_dynamic else "4",
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableCollections#%0d{control=42, fixed=[-2, 5], queue_data=[-3, 7]}", restored.__svtypes_object_number);'
            if remove_dynamic else
            'expected_display = $sformatf("EditableCollections#%0d{control=42, fixed=[-2, 5], dynamic=[%0h, 7], queue_data=[-3, 7]}", restored.__svtypes_object_number, source.dynamic[0]);'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-numeric-collections-" + ("removed" if remove_dynamic else "original"),
                source + bench, "SVTYPES_SHARED_COLLECTION_FIELDS_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_text", [False, True])
def test_remote_shared_string_and_real_fields_and_deletion(remove_text):
    class EditableScalars(SvObject):
        control = Bit[8](rand=False, cov=False)
        text = String(rand=False, cov=False)
        measurement = Real(rand=False, cov=False)
        short_value = ShortReal(rand=False, cov=False)

    source = EditableScalars.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.text,") == 1
    if remove_text:
        assert "  string text;" in source
        source = source.replace("  string text;", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.text," not in line)
    bench = r'''
module tb;
  initial begin
    EditableScalars source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42; source.measurement = 1.25; source.short_value = 2.5;
    SET_TEXT
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "field count");
    restored.unpack_body(bytes, offset);
    if (restored.control !== source.control || restored.measurement != source.measurement ||
        restored.short_value != source.short_value || offset != bytes.size()) $fatal(1, "scalar roundtrip");
    CHECK_TEXT
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "scalar display: %s", restored.svtypes_sprint());
    restored.apply_plusargs();
    if (restored.measurement != 4.5) $fatal(1, "real plusarg");
    CHECK_TEXT_ARG
    $display("SVTYPES_SHARED_TEXT_REAL_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_TEXT": "" if remove_text else 'source.text = "hello";',
        "CHECK_TEXT": "" if remove_text else 'if (restored.text != source.text) $fatal(1, "string roundtrip");',
        "CHECK_TEXT_ARG": "" if remove_text else 'if (restored.text != "changed") $fatal(1, "string plusarg");',
        "EXPECTED_COUNT": "3" if remove_text else "4",
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableScalars#%0d{control=42, measurement=%0g, short_value=%0g}", restored.__svtypes_object_number, source.measurement, source.short_value);'
            if remove_text else
            'expected_display = $sformatf("EditableScalars#%0d{control=42, text=%s, measurement=%0g, short_value=%0g}", restored.__svtypes_object_number, svtypes_pkg::escape_dump_string(source.text), source.measurement, source.short_value);'
        ),
    }
    for key, value in sorted(replacements.items(), key=lambda item: -len(item[0])):
        bench = bench.replace(key, value)
    _run_target("codegen-editability-text-real-" + ("removed" if remove_text else "original"),
                source + bench, "SVTYPES_SHARED_TEXT_REAL_FIELDS_PASS",
                run_args=("+measurement=4.5", "+text=changed"))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_labels", [False, True])
def test_remote_shared_string_and_real_collections_and_deletion(remove_labels):
    class EditableTextArrays(SvObject):
        control = Bit[8](rand=False, cov=False)
        labels = Queue[String](rand=False, cov=False)
        samples = DynArray[Real](rand=False, cov=False)

    source = EditableTextArrays.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.labels,") == 1
    if remove_labels:
        assert "  string labels [$];" in source
        source = source.replace("  string labels [$];", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.labels," not in line)
    bench = r'''
module tb;
  initial begin
    EditableTextArrays source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42;
    source.samples = new[2]; source.samples[0] = 1.25; source.samples[1] = 2.5;
    SET_LABELS
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "field count");
    restored.unpack_body(bytes, offset);
    if (restored.control !== source.control || restored.samples.size() != 2 ||
        restored.samples[0] != 1.25 || restored.samples[1] != 2.5 || offset != bytes.size())
      $fatal(1, "real array roundtrip");
    CHECK_LABELS
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "array display: %s", restored.svtypes_sprint());
    $display("SVTYPES_SHARED_TEXT_REAL_ARRAYS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_LABELS": "" if remove_labels else 'source.labels.push_back("one"); source.labels.push_back("two");',
        "CHECK_LABELS": "" if remove_labels else 'if (restored.labels != source.labels) $fatal(1, "string array roundtrip");',
        "EXPECTED_COUNT": "2" if remove_labels else "3",
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableTextArrays#%0d{control=42, samples=[1.25, 2.5]}", restored.__svtypes_object_number);'
            if remove_labels else
            'expected_display = $sformatf("EditableTextArrays#%0d{control=42, labels=[%s, %s], samples=[1.25, 2.5]}", restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("one"), svtypes_pkg::escape_dump_string("two"));'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-text-real-arrays-" + ("removed" if remove_labels else "original"),
                source + bench, "SVTYPES_SHARED_TEXT_REAL_ARRAYS_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_null_handle", [False, True])
def test_remote_shared_object_handles_preserve_identity_and_deletion(remove_null_handle):
    registry = ObjectRegistry()

    class SharedChild(SvObject):
        number = Bit[8](rand=False, cov=False)

    registry.register(SharedChild)

    class EditableGraph(SvObject):
        control = Bit[8](rand=False, cov=False)
        left = Object["SharedChild"](registry=registry)
        right = Object["SharedChild"](registry=registry)
        empty = Object["SharedChild"](registry=registry)

    source = SharedChild.to_sv_obj(use_type_reference=True) + "\n" + EditableGraph.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.empty,") == 1
    if remove_null_handle:
        assert "  SharedChild empty;" in source
        source = source.replace("  SharedChild empty;", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.empty," not in line)
    bench = r'''
module tb;
  initial begin
    EditableGraph source, restored;
    SharedChild child;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); child = new();
    source.control = 'h42; child.number = 'h2a;
    source.left = child; source.right = child;
    source.pack(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42 || restored.left == null ||
        restored.left != restored.right || restored.left.number !== 'h2a)
      $fatal(1, "shared graph roundtrip");
    CHECK_NULL
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display)
      $fatal(1, "shared graph display: %s", restored.svtypes_sprint());
    restored.apply_plusargs();
    if (restored.left.number !== 'h77 || restored.right.number !== 'h77)
      $fatal(1, "shared graph plusarg");
    $display("SVTYPES_SHARED_OBJECT_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "CHECK_NULL": "" if remove_null_handle else 'if (restored.empty != null) $fatal(1, "null handle");',
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableGraph#%0d{control=42, left=SharedChild#%0d{number=2a}, right=<ref#%0d>}", restored.__svtypes_object_number, restored.left.__svtypes_object_number, restored.left.__svtypes_object_number);'
            if remove_null_handle else
            'expected_display = $sformatf("EditableGraph#%0d{control=42, left=SharedChild#%0d{number=2a}, right=<ref#%0d>, empty=null}", restored.__svtypes_object_number, restored.left.__svtypes_object_number, restored.left.__svtypes_object_number);'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-object-graph-" + ("removed" if remove_null_handle else "original"),
                source + bench, "SVTYPES_SHARED_OBJECT_FIELDS_PASS", run_args=("+left.number=77",))


@pytest.mark.remote_sv
def test_remote_shared_object_fields_keep_self_cycles():
    registry = ObjectRegistry()

    class EditableCycle(SvObject):
        number = Bit[8](rand=False, cov=False)
        next_node = Object["EditableCycle"](registry=registry)

    registry.register(EditableCycle)
    source = EditableCycle.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditableCycle source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.number = 'h2a; source.next_node = source;
    source.pack(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack(bytes, offset);
    if (offset != bytes.size() || restored.number !== 'h2a || restored.next_node != restored)
      $fatal(1, "cycle roundtrip");
    expected_display = $sformatf("EditableCycle#%0d{number=2a, next_node=<ref#%0d>}",
                                 restored.__svtypes_object_number, restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "cycle display");
    restored.apply_plusargs();
    if (restored.number !== 'h77 || restored.next_node != restored) $fatal(1, "cycle plusarg");
    $display("SVTYPES_SHARED_CYCLE_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-object-cycle", source + bench,
                "SVTYPES_SHARED_CYCLE_FIELDS_PASS", run_args=("+number=77",))


@pytest.mark.remote_sv
def test_remote_direct_object_field_preserves_existing_policies():
    class NestedValue(SvObject):
        number = Bit[8](rand=False, cov=False)

    class ObjectPolicies(SvObject):
        control = Bit[8](rand=False, cov=False)
        child = NestedValue(pack_bytes=False, dump=False, plusarg=False, cov=False)

    source = NestedValue.to_sv_obj(use_type_reference=True) + "\n" + ObjectPolicies.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    ObjectPolicies source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.child = new(); restored.child = new();
    source.control = 'h42; source.child.number = 'h12; restored.child.number = 'h34;
    source.pack_body(bytes);
    if (bytes[6] != 1 || bytes[7] != 0) $fatal(1, "handle wire policy");
    restored.unpack_body(bytes, offset);
    if (restored.control !== 'h42 || restored.child.number !== 'h34 || offset != bytes.size())
      $fatal(1, "handle excluded from wire");
    expected_display = $sformatf("ObjectPolicies#%0d{control=42}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "handle dump policy");
    // Existing object-handle semantics recursively apply child plusargs.
    restored.apply_plusargs();
    if (restored.child.number !== 'h77) $fatal(1, "handle plusarg policy");
    $display("SVTYPES_DIRECT_OBJECT_POLICIES_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-direct-object-policies", source + bench,
                "SVTYPES_DIRECT_OBJECT_POLICIES_PASS", run_args=("+child.number=77",))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_queue", [False, True])
@pytest.mark.parametrize("kind", ["fixed", "dynamic", "queue"])
def test_remote_shared_object_collection_fields_keep_identity(remove_queue, kind):
    registry = ObjectRegistry()

    class ListChild(SvObject):
        number = Bit[8](rand=False, cov=False)

    registry.register(ListChild)
    handle = Object["ListChild"](registry=registry)
    collection = {"fixed": Array[handle, 3], "dynamic": DynArray[handle], "queue": Queue[handle]}[kind]

    class EditableObjectCollections(SvObject):
        control = Bit[8](rand=False, cov=False)
        primary = Object["ListChild"](registry=registry)
        items = collection(rand=False, cov=False)

    source = ListChild.to_sv_obj(use_type_reference=True) + "\n" + EditableObjectCollections.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.items,") == 1
    if remove_queue:
        declaration = {"fixed": "  ListChild items [3];", "dynamic": "  ListChild items [];", "queue": "  ListChild items [$];"}[kind]
        assert declaration in source
        source = source.replace(declaration, "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.items," not in line)
    bench = r'''
module tb;
  initial begin
    EditableObjectCollections source, restored;
    ListChild child;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); child = new();
    child.number = 'h2a; source.control = 'h42; source.primary = child;
    SET_QUEUE
    source.pack(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack(bytes, offset);
    if (offset != bytes.size() || restored.primary == null || restored.primary.number !== 'h2a)
      $fatal(1, "object collection roundtrip");
    CHECK_QUEUE
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display)
      $fatal(1, "object collection display: %s", restored.svtypes_sprint());
    $display("SVTYPES_SHARED_OBJECT_COLLECTIONS_PASS");
    $finish;
  end
endmodule
'''
    assignments = {
        "fixed": "source.items[0] = child; source.items[1] = null; source.items[2] = child;",
        "dynamic": "source.items = new[3]; source.items[0] = child; source.items[1] = null; source.items[2] = child;",
        "queue": "source.items.push_back(child); source.items.push_back(null); source.items.push_back(child);",
    }
    replacements = {
        "SET_QUEUE": "" if remove_queue else assignments[kind],
        "CHECK_QUEUE": "" if remove_queue else 'if ($size(restored.items) != 3 || restored.items[0] != restored.primary || restored.items[1] != null || restored.items[2] != restored.primary) $fatal(1, "object collection identity");',
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableObjectCollections#%0d{control=42, primary=ListChild#%0d{number=2a}}", restored.__svtypes_object_number, restored.primary.__svtypes_object_number);'
            if remove_queue else
            'expected_display = $sformatf("EditableObjectCollections#%0d{control=42, primary=ListChild#%0d{number=2a}, items=[<ref#%0d>, null, <ref#%0d>]}", restored.__svtypes_object_number, restored.primary.__svtypes_object_number, restored.primary.__svtypes_object_number, restored.primary.__svtypes_object_number);'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-object-collection-" + kind + "-" + ("removed" if remove_queue else "original"),
                source + bench, "SVTYPES_SHARED_OBJECT_COLLECTIONS_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_map", [False, True])
def test_remote_shared_associative_fields_keep_codecs_and_display(remove_map):
    class EditableMaps(SvObject):
        control = Bit[8](rand=False, cov=False)
        mapping = AssocArray[Bit[8], String](cov=False)
        counts = AssocArray[String, Int](cov=False)

    source = EditableMaps.to_sv_obj(use_type_reference=True)
    assert source.count("::apply(this.mapping,") == 1
    if remove_map:
        assert "  string mapping [bit [7:0]];" in source
        source = source.replace("  string mapping [bit [7:0]];", "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.mapping," not in line)
    bench = r'''
module tb;
  initial begin
    EditableMaps source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42; source.counts["a"] = -3; source.counts["b"] = 7;
    SET_MAP
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "field count");
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42 ||
        restored.counts["a"] != -3 || restored.counts["b"] != 7) $fatal(1, "map roundtrip");
    CHECK_MAP
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display)
      $fatal(1, "map display: %s", restored.svtypes_sprint());
    $display("SVTYPES_SHARED_ASSOC_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_MAP": "" if remove_map else 'source.mapping[1] = "one"; source.mapping[2] = "two";',
        "CHECK_MAP": "" if remove_map else 'if (restored.mapping[1] != "one" || restored.mapping[2] != "two") $fatal(1, "string map");',
        "EXPECTED_COUNT": "2" if remove_map else "3",
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("EditableMaps#%0d{control=42, counts={%s: -3, %s: 7}}", restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("a"), svtypes_pkg::escape_dump_string("b"));'
            if remove_map else
            'expected_display = $sformatf("EditableMaps#%0d{control=42, mapping={1: %s, 2: %s}, counts={%s: -3, %s: 7}}", restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("one"), svtypes_pkg::escape_dump_string("two"), svtypes_pkg::escape_dump_string("a"), svtypes_pkg::escape_dump_string("b"));'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-assoc-" + ("removed" if remove_map else "original"),
                source + bench, "SVTYPES_SHARED_ASSOC_FIELDS_PASS")


@pytest.mark.remote_sv
def test_remote_shared_associative_object_values_keep_identity():
    registry = ObjectRegistry()

    class MapChild(SvObject):
        number = Bit[8](rand=False, cov=False)

    registry.register(MapChild)

    class EditableObjectMap(SvObject):
        primary = Object["MapChild"](registry=registry)
        mapping = AssocArray[Bit[8], Object["MapChild"](registry=registry)](cov=False)

    source = MapChild.to_sv_obj(use_type_reference=True) + "\n" + EditableObjectMap.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditableObjectMap source, restored;
    MapChild child;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); child = new();
    child.number = 'h2a; source.primary = child;
    source.mapping[1] = child; source.mapping[2] = null;
    source.pack(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack(bytes, offset);
    if (offset != bytes.size() || restored.primary == null || restored.primary.number !== 'h2a ||
        restored.mapping[1] != restored.primary || restored.mapping[2] != null)
      $fatal(1, "map object identity");
    expected_display = $sformatf("EditableObjectMap#%0d{primary=MapChild#%0d{number=2a}, mapping={1: <ref#%0d>, 2: null}}",
                                 restored.__svtypes_object_number, restored.primary.__svtypes_object_number,
                                 restored.primary.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "map object display");
    $display("SVTYPES_SHARED_ASSOC_OBJECT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-assoc-object", source + bench, "SVTYPES_SHARED_ASSOC_OBJECT_PASS")


@pytest.mark.remote_sv
def test_remote_edited_associative_value_width_follows_declaration():
    class EditableMapWidths(SvObject):
        numeric_keys = AssocArray[Bit[8], Bit[8]](cov=False)
        string_keys = AssocArray[String, Bit[8]](cov=False)

    source = EditableMapWidths.to_sv_obj(use_type_reference=True)
    for declaration in ("bit [7:0] numeric_keys [bit [7:0]];", "bit [7:0] string_keys [string];"):
        assert declaration in source
        source = source.replace(declaration, declaration.replace("bit [7:0]", "bit [20:0]", 1))
    bench = r'''
module tb;
  initial begin
    EditableMapWidths source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.numeric_keys[1] = 21'h123456;
    source.string_keys["a"] = 21'h1abcde;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.numeric_keys[1] !== 21'h123456 ||
        restored.string_keys["a"] !== 21'h1abcde) $fatal(1, "edited associative value width");
    expected_display = $sformatf("EditableMapWidths#%0d{numeric_keys={1: 123456}, string_keys={%s: 1abcde}}",
                                 restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("a"));
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "edited associative display");
    $display("SVTYPES_EDITED_ASSOC_VALUE_WIDTH_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-assoc-value-width", source + bench,
                "SVTYPES_EDITED_ASSOC_VALUE_WIDTH_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("argument, expected", [("BLUE", "BLUE"), ("2", "BLUE"), ("255", "MAX"), ("-1", None), ("3", None), ("INVALID", None)])
def test_remote_shared_enum_field_preserves_names_numeric_plusargs_and_validation(argument, expected):
    class Color(Enum[Bit[8]]):
        RED = 1
        BLUE = 2
        MAX = 255

    class Tone(Enum[BitSigned[8]]):
        NEG = -1
        POS = 1

    class EditableEnums(SvObject):
        control = Bit[8](rand=False, cov=False)
        color = Color(rand=False, cov=False)
        tone = Tone(rand=False, cov=False)

    source = Color.to_sv_enum() + "\n" + Tone.to_sv_enum() + "\n" + EditableEnums.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditableEnums source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42; source.color = RED; source.tone = NEG;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42 || restored.color !== RED || restored.tone !== NEG)
      $fatal(1, "enum roundtrip");
    expected_display = $sformatf("EditableEnums#%0d{control=42, color=Color.RED, tone=Tone.NEG}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "enum display");
    restored.apply_plusargs();
    if (restored.color !== EXPECTED_COLOR || restored.tone !== NEG) $fatal(1, "enum plusarg");
    $display("SVTYPES_SHARED_ENUM_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    bench = bench.replace("EXPECTED_COLOR", expected or "BLUE")
    diagnostic = None if expected else ("Malformed enum plusarg" if argument == "INVALID" else "Invalid enum plusarg")
    _run_target("codegen-editability-enum-" + argument.lower(), source + bench,
                "SVTYPES_SHARED_ENUM_FIELDS_PASS", expected_error=diagnostic,
                run_args=("+color=" + argument, "+tone=-1"))


@pytest.mark.remote_sv
def test_remote_generated_enum_deletion_changes_only_declaration_and_field_list():
    class Color(Enum[Bit[8]]):
        RED = 1
        BLUE = 2

    class EditableEnumDeletion(SvObject):
        control = Bit[8](rand=False, cov=False)
        color = Color(rand=False, cov=False)

    source = EditableEnumDeletion.to_sv_obj(use_type_reference=True)
    assert "  Color color;" in source and source.count("::apply(this.color,") == 1
    source = source.replace("  Color color;", "")
    source = "\n".join(line for line in source.splitlines() if "::apply(this.color," not in line)
    bench = r'''
module tb;
  initial begin
    EditableEnumDeletion source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); source.control = 'h42;
    source.pack_body(bytes);
    if (bytes[6] != 1 || bytes[7] != 0) $fatal(1, "enum deletion count");
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42) $fatal(1, "enum deletion roundtrip");
    expected_display = $sformatf("EditableEnumDeletion#%0d{control=42}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "enum deletion display");
    restored.apply_plusargs();
    if (restored.control !== 'h77) $fatal(1, "enum deletion plusarg");
    $display("SVTYPES_ENUM_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-enum-deletion", Color.to_sv_enum() + "\n" + source + bench,
                "SVTYPES_ENUM_DELETION_PASS", run_args=("+control=77",))


@pytest.mark.remote_sv
def test_remote_shared_enum_collection_roundtrip_and_display():
    class Color(Enum[Bit[8]]):
        RED = 1
        BLUE = 2

    class EditableEnumArrays(SvObject):
        control = Bit[8](rand=False, cov=False)
        fixed = Array[Color, 2](rand=False, cov=False)
        values = Queue[Color](rand=False, cov=False)

    source = Color.to_sv_enum() + "\n" + EditableEnumArrays.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditableEnumArrays source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); source.control = 'h42;
    source.fixed[0] = RED; source.fixed[1] = BLUE;
    source.values.push_back(BLUE); source.values.push_back(RED);
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.fixed !== source.fixed || restored.values !== source.values)
      $fatal(1, "enum array roundtrip");
    expected_display = $sformatf("EditableEnumArrays#%0d{control=42, fixed=[Color.RED, Color.BLUE], values=[Color.BLUE, Color.RED]}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "enum array display");
    $display("SVTYPES_SHARED_ENUM_ARRAY_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-enum-array", source + bench, "SVTYPES_SHARED_ENUM_ARRAY_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("packed", [False, True])
def test_remote_struct_member_deletion_updates_containing_object_helpers(packed):
    class EditableRecord(SvPackedStruct if packed else SvStruct):
        keep = Bit[8](rand=False, cov=False)
        drop = Int(rand=False, cov=False)

    class EditableRecordOwner(SvObject):
        control = Bit[8](rand=False, cov=False)
        data = EditableRecord(plusarg=True, rand=False, cov=False)

    record = EditableRecord.to_sv_obj(use_type_reference=True)
    declaration = "  int drop;" if packed else "  int drop = 32'd0;"
    assert declaration in record
    record = record.replace(declaration, "")
    if packed:
        block = next(part for part in record.split("    begin\n") if "type(value.drop) member;" in part)
        record = record.replace("    begin\n" + block.split("    end\n", 1)[0] + "    end\n", "")
    else:
        record = "\n".join(line for line in record.splitlines() if "::apply(value.drop," not in line)
    assert "value.drop" not in record
    source = record + "\n" + EditableRecordOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EditableRecordOwner source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.control = 'h42; source.data.keep = 'h23;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42 || restored.data.keep !== 'h23)
      $fatal(1, "edited struct roundtrip");
    expected_display = $sformatf("EditableRecordOwner#%0d{control=42, data=EditableRecord{keep=23}}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "edited struct display: %s", restored.svtypes_sprint());
    restored.apply_plusargs();
    if (restored.data.keep !== 'h77) $fatal(1, "edited struct plusarg");
    $display("SVTYPES_STRUCT_FIELD_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-struct-delete-" + ("packed" if packed else "unpacked"),
                source + bench, "SVTYPES_STRUCT_FIELD_DELETION_PASS", run_args=("+data.keep=77",))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_member", [False, True])
def test_remote_nonuniform_packed_defaults_follow_field_deletion(remove_member):
    class DefaultRecord(SvPackedStruct):
        keep = Bit[8](0x23, rand=False, cov=False)
        removed = Bit[8](0x45, rand=False, cov=False)
        unknown = Logic[4](rand=False, cov=False)
        count = Int(-3, rand=False, cov=False)

    class DefaultOwner(SvObject):
        data = DefaultRecord(rand=False, cov=False)

    record = DefaultRecord.to_sv_obj(use_type_reference=True)
    owner = DefaultOwner.to_sv_obj(use_type_reference=True)
    assert "DefaultRecord_packer::svtypes_default()" in owner
    if remove_member:
        assert "  bit [7:0] removed;" in record
        record = record.replace("  bit [7:0] removed;", "")
        block = next(part for part in record.split("    begin\n") if "type(value.removed) member;" in part)
        record = record.replace("    begin\n" + block.split("    end\n", 1)[0] + "    end\n", "")
        assert "value.removed" not in record
    bench = r'''
module tb;
  initial begin
    DefaultOwner source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    if (source.data.keep !== 'h23 || source.data.unknown !== 4'bxxxx || source.data.count !== -3)
      $fatal(1, "nonuniform initial defaults");
    CHECK_REMOVED
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.data.keep !== 'h23 ||
        restored.data.unknown !== 4'bxxxx || restored.data.count !== -3)
      $fatal(1, "nonuniform default roundtrip");
    $display("SVTYPES_NONUNIFORM_PACKED_DEFAULT_PASS");
    $finish;
  end
endmodule
'''.replace("CHECK_REMOVED", "" if remove_member else 'if (source.data.removed !== \'h45) $fatal(1, "removed default");')
    _run_target("codegen-editability-packed-default-" + ("removed" if remove_member else "original"),
                record + "\n" + owner + bench, "SVTYPES_NONUNIFORM_PACKED_DEFAULT_PASS")


@pytest.mark.remote_sv
def test_remote_nested_packed_defaults_follow_inner_field_deletion():
    class DefaultInner(SvPackedStruct):
        keep = Bit[8](0x23, rand=False, cov=False)
        removed = Bit[8](0x45, rand=False, cov=False)
        unknown = Logic[4](rand=False, cov=False)

    class DefaultOuter(SvPackedStruct):
        nested = DefaultInner(rand=False, cov=False)
        count = Int(-3, rand=False, cov=False)

    class NestedDefaultOwner(SvObject):
        data = DefaultOuter(rand=False, cov=False)

    inner = DefaultInner.to_sv_obj(use_type_reference=True)
    assert "  bit [7:0] removed;" in inner
    inner = inner.replace("  bit [7:0] removed;", "")
    block = next(part for part in inner.split("    begin\n") if "type(value.removed) member;" in part)
    inner = inner.replace("    begin\n" + block.split("    end\n", 1)[0] + "    end\n", "")
    assert "value.removed" not in inner
    source = inner + "\n" + DefaultOuter.to_sv_obj(use_type_reference=True) + "\n" + NestedDefaultOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    NestedDefaultOwner source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    if (source.data.nested.keep !== 'h23 || source.data.nested.unknown !== 4'bxxxx || source.data.count !== -3)
      $fatal(1, "nested packed defaults");
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.data.nested.keep !== 'h23 ||
        restored.data.nested.unknown !== 4'bxxxx || restored.data.count !== -3)
      $fatal(1, "nested packed default roundtrip");
    $display("SVTYPES_NESTED_PACKED_DEFAULT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-nested-packed-default", source + bench,
                "SVTYPES_NESTED_PACKED_DEFAULT_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("packed", [False, True])
def test_remote_nested_struct_member_deletion_preserves_parent_behavior(packed):
    base = SvPackedStruct if packed else SvStruct

    class InnerRecord(base):
        keep = Bit[8](rand=False, cov=False)
        drop = Bit[8](rand=False, cov=False)

    class OuterRecord(base):
        mode = Bit[8](rand=False, cov=False)
        nested = InnerRecord(rand=False, cov=False)

    class NestedRecordOwner(SvObject):
        data = OuterRecord(plusarg=True, rand=False, cov=False)

    inner = InnerRecord.to_sv_obj(use_type_reference=True)
    assert "  bit [7:0] drop;" in inner
    inner = inner.replace("  bit [7:0] drop;", "")
    if packed:
        block = next(part for part in inner.split("    begin\n") if "type(value.drop) member;" in part)
        inner = inner.replace("    begin\n" + block.split("    end\n", 1)[0] + "    end\n", "")
    else:
        inner = "\n".join(line for line in inner.splitlines() if "::apply(value.drop," not in line)
    assert "value.drop" not in inner
    source = inner + "\n" + OuterRecord.to_sv_obj(use_type_reference=True) + "\n" + NestedRecordOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    NestedRecordOwner source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.data.mode = 'h12; source.data.nested.keep = 'h23;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.data.mode !== 'h12 || restored.data.nested.keep !== 'h23)
      $fatal(1, "nested edited struct roundtrip");
    expected_display = $sformatf("NestedRecordOwner#%0d{data=OuterRecord{mode=12, nested=InnerRecord{keep=23}}}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display)
      $fatal(1, "nested edited struct display: %s", restored.svtypes_sprint());
    restored.apply_plusargs();
    if (restored.data.mode !== 'h77) $fatal(1, "nested edited struct plusarg");
    $display("SVTYPES_NESTED_STRUCT_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-nested-struct-" + ("packed" if packed else "unpacked"),
                source + bench, "SVTYPES_NESTED_STRUCT_DELETION_PASS", run_args=("+data.mode=77",))


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_handle", [False, True])
def test_remote_unpacked_struct_container_and_handle_deletion(remove_handle):
    registry = ObjectRegistry()

    class StructChild(SvObject):
        number = Bit[8](rand=False, cov=False)

    registry.register(StructChild)

    class ComplexRecord(SvStruct):
        kind = Bit[8](rand=False, cov=False)
        samples = DynArray[Bit[8]](rand=False, cov=False)
        child = Object["StructChild"](registry=registry)

    class ComplexRecordOwner(SvObject):
        data = ComplexRecord(rand=False, cov=False)

    record = ComplexRecord.to_sv_obj(use_type_reference=True)
    assert "  StructChild child;" in record and record.count("::apply(value.child,") == 1
    if remove_handle:
        record = record.replace("  StructChild child;", "")
        record = "\n".join(line for line in record.splitlines() if "::apply(value.child," not in line)
    source = StructChild.to_sv_obj(use_type_reference=True) + "\n" + record + "\n" + ComplexRecordOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    ComplexRecordOwner source, restored;
    StructChild child;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); child = new();
    source.data.kind = 'h12;
    source.data.samples = new[2]; source.data.samples[0] = 'h23; source.data.samples[1] = 'h34;
    SET_CHILD
    source.pack(bytes);
    svtypes_pkg::clear_object_registry();
    restored.unpack(bytes, offset);
    if (offset != bytes.size() || restored.data.kind !== 'h12 || restored.data.samples.size() != 2 ||
        restored.data.samples[0] !== 'h23 || restored.data.samples[1] !== 'h34)
      $fatal(1, "struct container roundtrip");
    CHECK_CHILD
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display)
      $fatal(1, "struct container display: %s", restored.svtypes_sprint());
    $display("SVTYPES_STRUCT_CONTAINER_HANDLE_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_CHILD": "" if remove_handle else "child.number = 'h45; source.data.child = child;",
        "CHECK_CHILD": "" if remove_handle else 'if (restored.data.child == null || restored.data.child.number !== \'h45) $fatal(1, "struct handle roundtrip");',
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("ComplexRecordOwner#%0d{data=ComplexRecord{kind=12, samples=[23, 34]}}", restored.__svtypes_object_number);'
            if remove_handle else
            'expected_display = $sformatf("ComplexRecordOwner#%0d{data=ComplexRecord{kind=12, samples=[23, 34], child=StructChild#%0d{number=45}}}", restored.__svtypes_object_number, restored.data.child.__svtypes_object_number);'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-struct-container-" + ("removed" if remove_handle else "original"),
                source + bench, "SVTYPES_STRUCT_CONTAINER_HANDLE_PASS")


@pytest.mark.remote_sv
def test_remote_struct_arrays_follow_deleted_element_member():
    class ItemRecord(SvStruct):
        item_id = Bit[8](rand=False, cov=False)
        removed = Bit[8](rand=False, cov=False)

    class ItemGroup(SvStruct):
        items = Array[ItemRecord, 2](rand=False, cov=False)

    class ItemOwner(SvObject):
        group = ItemGroup(rand=False, cov=False)
        pending = Queue[ItemRecord](rand=False, cov=False)

    item = ItemRecord.to_sv_obj(use_type_reference=True)
    assert "  bit [7:0] removed;" in item and item.count("::apply(value.removed,") == 1
    item = item.replace("  bit [7:0] removed;", "")
    item = "\n".join(line for line in item.splitlines() if "::apply(value.removed," not in line)
    source = item + "\n" + ItemGroup.to_sv_obj(use_type_reference=True) + "\n" + ItemOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    ItemOwner source, restored;
    ItemRecord_t item;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.group.items[0].item_id = 'h12; source.group.items[1].item_id = 'h23;
    item.item_id = 'h34; source.pending.push_back(item);
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.group.items[0].item_id !== 'h12 ||
        restored.group.items[1].item_id !== 'h23 || restored.pending.size() != 1 ||
        restored.pending[0].item_id !== 'h34) $fatal(1, "struct array roundtrip");
    expected_display = $sformatf("ItemOwner#%0d{group=ItemGroup{items=[ItemRecord{item_id=12}, ItemRecord{item_id=23}]}, pending=[ItemRecord{item_id=34}]}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "struct array display: %s", restored.svtypes_sprint());
    $display("SVTYPES_STRUCT_ARRAY_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-struct-arrays", source + bench, "SVTYPES_STRUCT_ARRAY_DELETION_PASS")


@pytest.mark.remote_sv
def test_remote_associative_enum_and_struct_values_follow_deleted_member():
    class MapColor(Enum[Bit[8]]):
        RED = 1
        BLUE = 2

    class MapItem(SvStruct):
        item_id = Bit[8](rand=False, cov=False)
        removed = Bit[8](rand=False, cov=False)

    class EnumStructMap(SvObject):
        by_color = AssocArray[MapColor, MapItem](cov=False)
        colors = AssocArray[Bit[8], MapColor](cov=False)

    item = MapItem.to_sv_obj(use_type_reference=True)
    assert "  bit [7:0] removed;" in item and item.count("::apply(value.removed,") == 1
    item = item.replace("  bit [7:0] removed;", "")
    item = "\n".join(line for line in item.splitlines() if "::apply(value.removed," not in line)
    source = MapColor.to_sv_enum() + "\n" + item + "\n" + EnumStructMap.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    EnumStructMap source, restored;
    MapItem_t item;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    item.item_id = 'h12; source.by_color[RED] = item;
    item.item_id = 'h23; source.by_color[BLUE] = item;
    source.colors[1] = BLUE; source.colors[2] = RED;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.by_color[RED].item_id !== 'h12 ||
        restored.by_color[BLUE].item_id !== 'h23 || restored.colors[1] !== BLUE || restored.colors[2] !== RED)
      $fatal(1, "enum struct map roundtrip");
    expected_display = $sformatf("EnumStructMap#%0d{by_color={MapColor.RED: MapItem{item_id=12}, MapColor.BLUE: MapItem{item_id=23}}, colors={1: MapColor.BLUE, 2: MapColor.RED}}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "enum struct map display: %s", restored.svtypes_sprint());
    $display("SVTYPES_ASSOC_ENUM_STRUCT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-assoc-enum-struct", source + bench, "SVTYPES_ASSOC_ENUM_STRUCT_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_entries", [False, True])
def test_remote_associative_member_of_struct_uses_shared_list(remove_entries):
    class MapRecord(SvStruct):
        marker = Bit[8](rand=False, cov=False)
        entries = AssocArray[String, Bit[8]](cov=False)

    class MapRecordOwner(SvObject):
        record = MapRecord(rand=False, cov=False)

    record = MapRecord.to_sv_obj(use_type_reference=True)
    assert record.count("::apply(value.entries,") == 1
    if remove_entries:
        assert "  bit [7:0] entries [string];" in record
        record = record.replace("  bit [7:0] entries [string];", "")
        record = "\n".join(line for line in record.splitlines() if "::apply(value.entries," not in line)
    source = record + "\n" + MapRecordOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    MapRecordOwner source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    source.record.marker = 'h12;
    SET_ENTRIES
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.record.marker !== 'h12)
      $fatal(1, "struct map roundtrip");
    CHECK_ENTRIES
    EXPECTED_DISPLAY
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "struct map display: %s", restored.svtypes_sprint());
    $display("SVTYPES_STRUCT_ASSOC_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_ENTRIES": "" if remove_entries else 'source.record.entries["a"] = \'h23; source.record.entries["b"] = \'h34;',
        "CHECK_ENTRIES": "" if remove_entries else 'if (restored.record.entries["a"] !== \'h23 || restored.record.entries["b"] !== \'h34) $fatal(1, "struct map entries");',
        "EXPECTED_DISPLAY": (
            'expected_display = $sformatf("MapRecordOwner#%0d{record=MapRecord{marker=12}}", restored.__svtypes_object_number);'
            if remove_entries else
            'expected_display = $sformatf("MapRecordOwner#%0d{record=MapRecord{marker=12, entries={%s: 23, %s: 34}}}", restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("a"), svtypes_pkg::escape_dump_string("b"));'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-struct-assoc-" + ("removed" if remove_entries else "original"),
                source + bench, "SVTYPES_STRUCT_ASSOC_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("outer", ["fixed", "dynamic", "queue", "assoc"])
@pytest.mark.parametrize("inner", ["fixed", "dynamic", "queue", "assoc"])
@pytest.mark.parametrize("use_type_reference", [False, True])
def test_remote_two_level_containers_share_fields_and_preserve_wire(outer, inner, use_type_reference):
    def collection(kind, element):
        if kind == "fixed":
            return Array[element, 2](rand=False, cov=False)
        if kind == "dynamic":
            return DynArray[element](rand=False, cov=False)
        if kind == "queue":
            return Queue[element](rand=False, cov=False)
        return AssocArray[String, element](cov=False)

    class NestedMatrix(SvObject):
        values = collection(outer, collection(inner, Int(rand=False, cov=False)))

    source = NestedMatrix.to_sv_obj(use_type_reference=use_type_reference)
    assert source.count('::apply(this.values, "values",') == 1
    inner_init = {
        "fixed": "element[0] = 7; element[1] = 8;",
        "dynamic": "element = new[2]; element[0] = 7; element[1] = 8;",
        "queue": "element.push_back(7); element.push_back(8);",
        "assoc": 'element["a"] = 7; element["b"] = 8;',
    }[inner]
    outer_init = {
        "fixed": "source.values[0] = element;",
        "dynamic": "source.values = new[1]; source.values[0] = element;",
        "queue": "source.values.push_back(element);",
        "assoc": 'source.values["first"] = element;',
    }[outer]
    outer_index = '["first"]' if outer == "assoc" else "[0]"
    inner_index = '["a"]' if inner == "assoc" else "[0]"
    bench = r'''
module tb;
  initial begin
    NestedMatrix source, restored;
    NestedMatrix::__svtypes_values_element_t element;
    byte unsigned bytes[$], again[$];
    int offset = 0;
    source = new(); restored = new();
    INNER_INIT
    OUTER_INIT
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.values OUTER_INDEX INNER_INDEX != 7)
      $fatal(1, "nested container roundtrip");
    restored.pack_body(again);
    if (bytes != again || source.svtypes_sprint() != restored.svtypes_sprint())
      $fatal(1, "nested container wire/display");
    $display("SVTYPES_NESTED_CONTAINER_PASS");
    $finish;
  end
endmodule
'''
    for key, value in {"INNER_INIT": inner_init, "OUTER_INIT": outer_init,
                       "OUTER_INDEX": outer_index, "INNER_INDEX": inner_index}.items():
        bench = bench.replace(key, value)
    _run_target(f"codegen-editability-nested-{outer}-{inner}", source + bench,
                "SVTYPES_NESTED_CONTAINER_PASS")


@pytest.mark.remote_sv
def test_remote_nested_reference_container_preserves_target_and_shadowed_name():
    class NestedReferences(SvObject):
        result = AssocArray[String, Queue[RemoteRef["item.Type"]]](cov=False)

    source = NestedReferences.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    NestedReferences source, restored;
    NestedReferences::__svtypes_result_element_t values;
    svtypes_pkg::remote_ref entry;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    entry = new("item.Type", 17); values.push_back(entry);
    entry = new("item.Type", 0); values.push_back(entry);
    source.result["a"] = values;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.result["a"].size() != 2 ||
        restored.result["a"][0].object_number != 17 || restored.result["a"][0].target_type_name != "item.Type" ||
        restored.result["a"][1].object_number != 0 || restored.result["a"][1].target_type_name != "item.Type")
      $fatal(1, "nested reference identity");
    expected_display = $sformatf("NestedReferences#%0d{result={%s: [RemoteRef(item.Type,17), null]}}",
                                 restored.__svtypes_object_number, svtypes_pkg::escape_dump_string("a"));
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "nested reference display");
    $display("SVTYPES_NESTED_REFERENCE_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-nested-reference", source + bench, "SVTYPES_NESTED_REFERENCE_PASS")


@pytest.mark.remote_sv
def test_remote_nested_container_inside_struct_roundtrips():
    class MatrixRecord(SvStruct):
        values = Queue[Queue[Int]](rand=False, cov=False)

    class MatrixOwner(SvObject):
        record = MatrixRecord(rand=False, cov=False)

    source = MatrixRecord.to_sv_obj(use_type_reference=True) + "\n" + MatrixOwner.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    MatrixOwner source, restored;
    int row[$];
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new();
    row.push_back(7); row.push_back(-3); source.record.values.push_back(row);
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.record.values.size() != 1 ||
        restored.record.values[0].size() != 2 || restored.record.values[0][0] != 7 || restored.record.values[0][1] != -3)
      $fatal(1, "nested struct container roundtrip");
    expected_display = $sformatf("MatrixOwner#%0d{record=MatrixRecord{values=[[7, -3]]}}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "nested struct container display");
    $display("SVTYPES_NESTED_STRUCT_CONTAINER_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-nested-struct-container", source + bench,
                "SVTYPES_NESTED_STRUCT_CONTAINER_PASS")


@pytest.mark.remote_sv
def test_remote_nested_container_deletion_needs_only_declaration_and_field_list():
    class EditableNestedContainer(SvObject):
        marker = Bit[8](rand=False, cov=False)
        matrix = Queue[Queue[Int]](rand=False, cov=False)

    source = EditableNestedContainer.to_sv_obj(use_type_reference=True)
    assert "  int matrix [$] [$];" in source
    assert source.count('::apply(this.matrix, "matrix",') == 1
    source = source.replace("  int matrix [$] [$];", "")
    source = "\n".join(line for line in source.splitlines() if '::apply(this.matrix, "matrix",' not in line)
    assert "this.matrix" not in source
    bench = r'''
module tb;
  initial begin
    EditableNestedContainer source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    string expected_display;
    source = new(); restored = new(); source.marker = 'h42;
    source.pack_body(bytes);
    if (bytes[6] != 1 || bytes[7] != 0) $fatal(1, "nested deletion count");
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.marker !== 'h42) $fatal(1, "nested deletion roundtrip");
    expected_display = $sformatf("EditableNestedContainer#%0d{marker=42}", restored.__svtypes_object_number);
    if (restored.svtypes_sprint() != expected_display) $fatal(1, "nested deletion display");
    $display("SVTYPES_NESTED_CONTAINER_DELETION_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-nested-container-deletion", source + bench,
                "SVTYPES_NESTED_CONTAINER_DELETION_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_values", [False, True])
def test_remote_symbolic_fields_share_list_and_follow_actual_declarations(remove_values):
    class SymbolicBuffer(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int]()
        tag = Bit[width](rand=False, cov=False)
        values = Array[Bit[width], depth](rand=False, cov=False)

    source = SymbolicBuffer.to_sv_obj(use_type_reference=True)
    assert source.count('::apply(this.values, "values",') == 1
    if remove_values:
        declaration = "  bit [(width) - 1:0] values [(depth)];"
        assert declaration in source
        source = source.replace(declaration, "")
        source = "\n".join(line for line in source.splitlines() if '::apply(this.values, "values",' not in line)
    bench = r'''
module tb;
  initial begin
    SymbolicBuffer#(.width(9), .depth(3)) source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new(); source.tag = 9'h123;
    SET_VALUES
    source.pack_body(bytes);
    if (bytes[6] != EXPECTED_COUNT || bytes[7] != 0) $fatal(1, "symbolic field count");
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.tag !== source.tag) $fatal(1, "symbolic roundtrip");
    CHECK_VALUES
    $display("SVTYPES_SYMBOLIC_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_VALUES": "" if remove_values else "foreach (source.values[i]) source.values[i] = 9'h100 + i;",
        "CHECK_VALUES": "" if remove_values else 'if (restored.values !== source.values) $fatal(1, "symbolic array");',
        "EXPECTED_COUNT": "1" if remove_values else "2",
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-symbolic-" + ("removed" if remove_values else "original"),
                source + bench, "SVTYPES_SYMBOLIC_FIELDS_PASS")


@pytest.mark.remote_sv
def test_remote_type_parameter_field_follows_edited_declaration():
    class SymbolicTypeHolder(SvObject):
        T = Parameter[type]()
        value = T()

    source = SymbolicTypeHolder.to_sv_obj(use_type_reference=True)
    assert "  T value;" in source
    assert "bit_packer#(type(this.value))" in source
    source = source.replace("  T value;", "  bit [20:0] value;")
    bench = r'''
module tb;
  initial begin
    SymbolicTypeHolder#(.T(bit [8:0])) source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new(); source.value = 21'h123456;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.value !== source.value)
      $fatal(1, "edited type parameter field roundtrip");
    $display("SVTYPES_EDITED_TYPE_PARAMETER_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-type-parameter", source + bench,
                "SVTYPES_EDITED_TYPE_PARAMETER_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize('use_type_reference', [False, True])
def test_remote_multidimensional_symbolic_array_uses_actual_shape(use_type_reference):
    class SymbolicMatrix(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int]()
        values = Array[Bit[width], (2, depth)](rand=False, cov=False)

    source = SymbolicMatrix.to_sv_obj(use_type_reference=use_type_reference)
    assert ("fixed_sequence_packer#(type(this.values)" in source) == use_type_reference
    bench = r'''
module tb;
  initial begin
    SymbolicMatrix#(.width(9), .depth(3)) source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    foreach (source.values[i,j]) source.values[i][j] = 9'h100 + i * 3 + j;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.values !== source.values)
      $fatal(1, "symbolic multidimensional roundtrip");
    $display("SVTYPES_SYMBOLIC_MULTIDIMENSIONAL_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-symbolic-matrix", source + bench,
                "SVTYPES_SYMBOLIC_MULTIDIMENSIONAL_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize('use_type_reference', [False, True])
def test_remote_symbolic_queue_uses_actual_element_type(use_type_reference):
    class SymbolicQueue(SvObject):
        width = Parameter[Int]()
        values = Queue[Bit[width]](rand=False, cov=False)

    source = SymbolicQueue.to_sv_obj(use_type_reference=use_type_reference)
    assert source.count('::apply(this.values, "values",') == 1
    bench = r'''
module tb;
  initial begin
    SymbolicQueue#(.width(9)) source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    source.values.push_back(9'h123); source.values.push_back(9'h1ab);
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.values !== source.values)
      $fatal(1, "symbolic queue roundtrip");
    $display("SVTYPES_SYMBOLIC_QUEUE_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-symbolic-queue", source + bench,
                "SVTYPES_SYMBOLIC_QUEUE_PASS")


@pytest.mark.remote_sv
def test_remote_edited_symbolic_array_width_and_length_follow_declaration():
    class EditableSymbolicArray(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int]()
        values = Array[Bit[width], depth](rand=False, cov=False)

    source = EditableSymbolicArray.to_sv_obj(use_type_reference=True)
    declaration = "  bit [(width) - 1:0] values [(depth)];"
    assert declaration in source
    source = source.replace(declaration, "  bit [20:0] values [5];")
    bench = r'''
module tb;
  initial begin
    EditableSymbolicArray#(.width(9), .depth(3)) source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    foreach (source.values[i]) source.values[i] = 21'h123450 + i;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || $size(restored.values) != 5 || restored.values !== source.values)
      $fatal(1, "edited symbolic array roundtrip");
    $display("SVTYPES_EDITED_SYMBOLIC_ARRAY_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-edited-symbolic-array", source + bench,
                "SVTYPES_EDITED_SYMBOLIC_ARRAY_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("remove_ref", [False, True])
def test_remote_reference_field_and_collections_share_operations(remove_ref):
    class EditableRemoteRefs(SvObject):
        control = Bit[8](rand=False, cov=False)
        ref_value = RemoteRef["item.Type"](cov=False)
        refs = Queue[RemoteRef["item.Type"]](cov=False)
        lookup = AssocArray[Bit[8], RemoteRef["item.Type"]](cov=False)

    source = EditableRemoteRefs.to_sv_obj(use_type_reference=True)
    assert '  svtypes_pkg::remote_ref ref_value = new("item.Type");' in source
    assert source.count("::apply(this.ref_value,") == 1
    if remove_ref:
        source = source.replace('  svtypes_pkg::remote_ref ref_value = new("item.Type");', "")
        source = "\n".join(line for line in source.splitlines() if "::apply(this.ref_value," not in line)
    bench = r'''
module tb;
  initial begin
    EditableRemoteRefs source, restored;
    svtypes_pkg::remote_ref entry;
    byte unsigned bytes[$];
    int offset = 0;
    string original_display;
    source = new(); restored = new(); source.control = 'h42;
    entry = new("item.Type", 17); source.refs.push_back(entry);
    entry = new("item.Type", 0); source.refs.push_back(entry);
    entry = new("item.Type", 34); source.lookup[1] = entry;
    SET_REFERENCE
    original_display = source.svtypes_sprint();
    CHECK_DISPLAY
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.control !== 'h42 || restored.refs.size() != 2 ||
        restored.refs[0].object_number != 17 || restored.refs[1].object_number != 0 ||
        restored.lookup[1].object_number != 34 || restored.refs[0].target_type_name != "item.Type" ||
        restored.refs[1].target_type_name != "item.Type" || restored.lookup[1].target_type_name != "item.Type")
      $fatal(1, "remote reference roundtrip");
    CHECK_REFERENCE
    $display("SVTYPES_REMOTE_REFERENCE_FIELDS_PASS");
    $finish;
  end
endmodule
'''
    replacements = {
        "SET_REFERENCE": "" if remove_ref else "source.ref_value.object_number = 51;",
        "CHECK_REFERENCE": "" if remove_ref else 'if (restored.ref_value.object_number != 51 || restored.ref_value.target_type_name != "item.Type") $fatal(1, "remote reference identity");',
        "CHECK_DISPLAY": (
            'if (original_display != $sformatf("EditableRemoteRefs#%0d{control=42, refs=[RemoteRef(item.Type,17), null], lookup={1: RemoteRef(item.Type,34)}}", source.__svtypes_object_number)) $fatal(1, "remote reference display");'
            if remove_ref else
            'if (original_display != $sformatf("EditableRemoteRefs#%0d{control=42, ref_value=RemoteRef(item.Type,51), refs=[RemoteRef(item.Type,17), null], lookup={1: RemoteRef(item.Type,34)}}", source.__svtypes_object_number)) $fatal(1, "remote reference display");'
        ),
    }
    for key, value in replacements.items():
        bench = bench.replace(key, value)
    _run_target("codegen-editability-remote-reference-" + ("removed" if remove_ref else "original"),
                source + bench, "SVTYPES_REMOTE_REFERENCE_FIELDS_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("multifile", [False, True])
def test_remote_generated_object_declaration_edits_follow_actual_types(multifile):
    class EditableOwner(SvObject):
        opcode = Bit[13](rand=False, cov=False)
        data = Array[Bit[16], 3](rand=False, cov=False)

    generated = EditableOwner.to_sv_obj(use_type_reference=True)
    target_name = "codegen-editability-edited-object" + ("-multifile" if multifile else "")
    if multifile:
        output = Path(__file__).resolve().parents[2] / ".tmp" / target_name
        result = generate(EditableOwner, output, targets={"sv"}, use_type_reference=True)
        assert not result.failed
        generated = (output / "EditableOwner.sv").read_text()
    assert "bit [12:0] opcode;" in generated and "data [3];" in generated
    edited = generated.replace("bit [12:0] opcode;", "bit [20:0] opcode;")
    edited = edited.replace("data [3];", "data [5];")
    bench = r'''
module tb;
  initial begin
    EditableOwner source, restored;
    byte unsigned bytes[$];
    int offset;
    source = new(); restored = new();
    source.opcode = 21'h123456;
    foreach (source.data[i]) source.data[i] = 16'h1200 + i;
    source.pack_body(bytes);
    if (bytes[6] != 2 || bytes[7] != 0) $fatal(1,"field count mismatch");
    restored.unpack_body(bytes, offset);
    if (restored.opcode !== source.opcode || restored.data !== source.data || offset != bytes.size())
      $fatal(1,"declaration edit roundtrip mismatch");
    $display("SVTYPES_EDITED_DECLARATIONS_PASS");
    $finish;
  end
endmodule
'''
    _run_target(target_name, edited + bench,
                "SVTYPES_EDITED_DECLARATIONS_PASS")


@pytest.mark.remote_sv
def test_remote_manually_added_and_reordered_fields_match_cpp(tmp_path):
    class EditableOrder(SvObject):
        first = Bit[8](rand=False, cov=False)
        second = Bit[8](rand=False, cov=False)

    # Edit only the native declaration and the ordered field list on each side.
    cpp = EditableOrder.to_cpp_obj()
    assert cpp.count("  uint8_t second;") == 1
    cpp = cpp.replace("  uint8_t second;", "  uint8_t second;\n  uint8_t third;")
    cpp_lines = cpp.splitlines()
    first = next(line for line in cpp_lines if 'operation("first",' in line)
    second = next(line for line in cpp_lines if 'operation("second",' in line)
    third = first.replace('"first", self.first', '"third", self.third')
    assert cpp_lines.index(second) == cpp_lines.index(first) + 1
    cpp = cpp.replace(first + "\n" + second, second + "\n" + third + "\n" + first)
    source = tmp_path / "edited.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <cstdio>\n#include <fstream>\n' + cpp + r'''
int main(int argc, char** argv) {
    if (argc > 1) {
        std::ifstream input(argv[1], std::ios::binary);
        std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(input), {}};
        EditableOrder restored;
        size_t offset = 0;
        restored.unpack_body(bytes, offset);
        return offset == bytes.size() && restored.first == 0x11 &&
               restored.second == 0x22 && restored.third == 0x33 ? 0 : 1;
    }
    EditableOrder value;
    value.__svtypes_object_number = 1234;
    value.first = 0x11; value.second = 0x22; value.third = 0x33;
    std::vector<uint8_t> bytes;
    value.pack_body(bytes);
    for (auto byte : bytes) std::printf("%02x", unsigned(byte));
}
''')
    root = Path(__file__).resolve().parents[2]
    compiler = shutil.which("c++")
    assert compiler, "C++ compiler required for editability qualification"
    executable = tmp_path / "edited"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    expected = bytes.fromhex(subprocess.check_output([str(executable)], text=True))
    assert expected[-3:] == bytes([0x22, 0x33, 0x11])

    # A matching Python declaration has a new fingerprint: normal decoding
    # must accept the native snapshot while strict decoding detects the change.
    matching_type = type("EditableOrder", (SvObject,), {
        "__module__": EditableOrder.__module__,
        "__qualname__": EditableOrder.__qualname__,
        "second": Bit[8](rand=False, cov=False),
        "third": Bit[8](rand=False, cov=False),
        "first": Bit[8](rand=False, cov=False),
    })
    native_value = b"\x01" + expected
    decoded, used = matching_type.codec_template().unpack(
        native_value, UnpackContext(CodecSession())
    )
    assert used == len(native_value)
    assert (decoded.first.value, decoded.second.value, decoded.third.value) == (0x11, 0x22, 0x33)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        matching_type.codec_template().unpack(
            native_value, UnpackContext(CodecSession(), diagnostics="strict")
        )
    python_value = matching_type(session=CodecSession())
    python_value.svtypes_object_number = 1234
    python_value.first.value = 0x11
    python_value.second.value = 0x22
    python_value.third.value = 0x33
    python_bytes = python_value.to_bytes()[1:]
    assert python_bytes[-3:] == expected[-3:]
    python_binary = tmp_path / "python.bin"
    python_binary.write_bytes(python_bytes)
    subprocess.run([str(executable), str(python_binary)], check=True, capture_output=True)

    sv = EditableOrder.to_sv_obj(use_type_reference=True)
    assert sv.count("  bit [7:0] second;") == 1
    sv = sv.replace("  bit [7:0] second;", "  bit [7:0] second;\n  bit [7:0] third;")
    sv_lines = sv.splitlines()
    first = next(line for line in sv_lines if "::apply(this.first," in line)
    second = next(line for line in sv_lines if "::apply(this.second," in line)
    third = first.replace("this.first", "this.third").replace('"first"', '"third"')
    assert sv_lines.index(second) == sv_lines.index(first) + 1
    sv = sv.replace(first + "\n" + second, second + "\n" + third + "\n" + first)
    expected_sv = ", ".join(f"8'h{byte:02x}" for byte in expected)
    python_sv = ", ".join(f"8'h{byte:02x}" for byte in python_bytes)
    bench = r'''
module tb;
  initial begin
    EditableOrder source, restored;
    byte unsigned bytes[$];
    byte unsigned expected[$] = '{EXPECTED_BYTES};
    byte unsigned python_bytes[$] = '{PYTHON_BYTES};
    int offset = 0;
    source = new(); restored = new();
    source.__svtypes_object_number = 1234;
    source.first = 'h11; source.second = 'h22; source.third = 'h33;
    source.pack_body(bytes);
    if (bytes.size() != expected.size()) $fatal(1, "edited size mismatch");
    foreach (bytes[i]) if (bytes[i] !== expected[i]) $fatal(1, "edited byte mismatch at %0d", i);
    restored.unpack_body(expected, offset);
    if (offset != expected.size() || restored.first !== 'h11 ||
        restored.second !== 'h22 || restored.third !== 'h33)
      $fatal(1, "edited cross-language decode mismatch");
    offset = 0;
    restored.unpack_body(python_bytes, offset);
    if (offset != python_bytes.size() || restored.first !== 'h11 ||
        restored.second !== 'h22 || restored.third !== 'h33)
      $fatal(1, "edited Python decode mismatch");
    $display("SVTYPES_EDITED_FIELDS_CROSS_LANGUAGE_PASS");
    $finish;
  end
endmodule
'''.replace("EXPECTED_BYTES", expected_sv).replace("PYTHON_BYTES", python_sv)
    _run_target("codegen-editability-edited-cross-language", sv + bench,
                "SVTYPES_EDITED_FIELDS_CROSS_LANGUAGE_PASS")


@pytest.mark.remote_sv
def test_remote_decode_diagnostics_are_process_local_and_nested():
    source = r'''
module tb;
  initial begin
    svtypes_pkg::decode_context strict_context, off_context, previous, nested;
    byte unsigned bytes[$];
    longint unsigned id;
    int offset;
    strict_context = new(svtypes_pkg::DIAGNOSTICS_STRICT);
    off_context = new();
    svtypes_pkg::pack_object_header("Record", {64{"0"}}, 1, 1, bytes);
    svtypes_pkg::unpack_object_header("Record", "invalid expected digest", 999, id, bytes, offset);
    if (offset != bytes.size() || id != 1) $fatal(1,"off header parsing mismatch");
    previous = svtypes_pkg::begin_decode_context(strict_context);
    if (!svtypes_pkg::strict_decode_diagnostics()) $fatal(1,"strict context missing");
    nested = svtypes_pkg::begin_decode_context(off_context);
    if (svtypes_pkg::strict_decode_diagnostics()) $fatal(1,"nested override failed");
    svtypes_pkg::end_decode_context(nested);
    fork
      begin
        if (svtypes_pkg::strict_decode_diagnostics()) $fatal(1,"process context leaked");
      end
    join
    if (!svtypes_pkg::strict_decode_diagnostics()) $fatal(1,"parent context lost");
    offset = 0;
    svtypes_pkg::unpack_object_header("Record", {64{"0"}}, 1, id, bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    if (svtypes_pkg::strict_decode_diagnostics()) $fatal(1,"context not restored");
    $display("SVTYPES_DIAGNOSTICS_CONTEXT_PASS");
    $finish;
  end
endmodule
'''
    _run_target("codegen-editability-diagnostics", source, "SVTYPES_DIAGNOSTICS_CONTEXT_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("strict", [False, True])
def test_remote_generated_graph_propagates_strict_to_child(strict):
    registry = ObjectRegistry()

    class DiagnosticChild(SvObject):
        number = Bit[8](rand=False, cov=False)

    registry.register(DiagnosticChild)

    class DiagnosticGraph(SvObject):
        child = Object["DiagnosticChild"](registry=registry)

    source = DiagnosticChild.to_sv_obj(use_type_reference=True) + "\n" + DiagnosticGraph.to_sv_obj(use_type_reference=True)
    bench = r'''
module tb;
  initial begin
    DiagnosticChild child;
    DiagnosticGraph original, restored;
    svtypes_pkg::decode_context state, previous;
    byte unsigned bytes[$];
    int offset, header, name_length;
    child = new(); child.number = 73;
    original = new(); restored = new(); original.child = child;
    original.pack(bytes);
    header = -1;
    for (int i = 2; i + 20 < bytes.size(); i++) begin
      if (bytes[i] == "S" && bytes[i+1] == "V" && bytes[i+2] == "X" && bytes[i+3] == "O") begin
        header = i; break;
      end
    end
    if (header < 0) $fatal(1, "child header not found");
    name_length = int'(bytes[header+16]) | (int'(bytes[header+17]) << 8) |
                  (int'(bytes[header+18]) << 16) | (int'(bytes[header+19]) << 24);
    bytes[header + 20 + name_length] ^= 1;
    svtypes_pkg::clear_object_registry();
    state = new(POLICY);
    previous = svtypes_pkg::begin_decode_context(state);
    restored.unpack(bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    if (restored.child == null || restored.child.number != 73 || offset != bytes.size())
      $fatal(1, "normal nested decode mismatch");
    $display("MARKER");
    $finish;
  end
endmodule
'''.replace("POLICY", "svtypes_pkg::DIAGNOSTICS_STRICT" if strict else "svtypes_pkg::DIAGNOSTICS_OFF")
    bench = bench.replace("MARKER", "UNEXPECTED_ACCEPTANCE" if strict else "SVTYPES_NESTED_DIAGNOSTICS_PASS")
    _run_target("codegen-editability-nested-diagnostics-" + ("strict" if strict else "off"),
                source + bench, "SVTYPES_NESTED_DIAGNOSTICS_PASS",
                expected_error="object encoding fingerprint mismatch" if strict else None)


@pytest.mark.remote_sv
def test_remote_multifile_struct_codec_compiles_in_declaring_scope():
    class SplitRecord(SvStruct):
        number = Bit[13]()
        data = Array[Bit[16], 3]()

    name = "codegen-editability-split-struct"
    output = Path(__file__).resolve().parents[2] / ".tmp" / name
    result = generate(SplitRecord, output, targets={"sv"}, use_type_reference=True)
    assert not result.failed
    main = (output / "SplitRecord.sv").read_text()
    assert '`include "support/' in main
    bench = r'''
module tb;
  initial begin
    SplitRecord_t original, restored;
    byte unsigned bytes[$];
    int offset;
    original.number = 13'h1234;
    foreach (original.data[i]) original.data[i] = 100 + i;
    SplitRecord_packer::pack(original, bytes);
    SplitRecord_packer::unpack(restored, bytes, offset);
    if (original !== restored || offset != bytes.size()) $fatal(1,"split struct mismatch");
    $display("SVTYPES_SPLIT_STRUCT_PASS");
    $finish;
  end
endmodule
'''
    _run_target(name, main + bench, "SVTYPES_SPLIT_STRUCT_PASS")


@pytest.mark.remote_sv
@pytest.mark.parametrize("case,strict,mutation,expected", [
    ("fingerprint", True, "bytes[bytes.size()-1] ^= 1;", "fingerprint mismatch"),
    ("count", True, "bytes[6] = 2;", "field-count mismatch"),
    ("version", False, "bytes[4] = 99;", "version mismatch"),
    ("truncated", False, "bytes.delete(bytes.size()-1);", "object encoding fingerprint"),
])
def test_remote_decode_diagnostics_reject_invalid_input(case, strict, mutation, expected):
    source = r'''
module tb;
  initial begin
    svtypes_pkg::decode_context decode_state, previous;
    byte unsigned bytes[$];
    longint unsigned id;
    int offset;
    decode_state = new(POLICY);
    previous = svtypes_pkg::begin_decode_context(decode_state);
    svtypes_pkg::pack_object_header("Record", {64{"0"}}, 1, 1, bytes);
    MUTATION
    svtypes_pkg::unpack_object_header("Record", {64{"0"}}, 1, id, bytes, offset);
    svtypes_pkg::end_decode_context(previous);
    $display("UNEXPECTED_ACCEPTANCE");
    $finish;
  end
endmodule
'''.replace("POLICY", "svtypes_pkg::DIAGNOSTICS_STRICT" if strict else "svtypes_pkg::DIAGNOSTICS_OFF")
    source = source.replace("MUTATION", mutation)
    _run_target("codegen-editability-invalid-" + case, source, "", expected_error=expected)


@pytest.mark.remote_sv
@pytest.mark.parametrize('use_type_reference', [False, True])
def test_remote_type_reference_modes_cover_packed_and_inherited_fields(use_type_reference):
    class TypeModeRecord(SvPackedStruct):
        code = Bit[8]()

    class TypeModeBase(SvObject):
        values = Queue[Bit[8]](cov=False)

    class TypeModeChild(TypeModeBase):
        record = TypeModeRecord()
        more = Queue[Bit[4]](cov=False)

    source = '\n'.join(cls.to_sv_obj(use_type_reference=use_type_reference)
                       for cls in (TypeModeRecord, TypeModeBase, TypeModeChild))
    if not use_type_reference:
        assert 'type(this.' not in source and 'type(value.' not in source
    bench = r'''
module tb;
  initial begin
    TypeModeChild source, restored;
    byte unsigned bytes[$];
    int offset = 0;
    source = new(); restored = new();
    source.values.push_back(8'h53);
    source.more.push_back(4'h9);
    source.record.code = 8'h27;
    source.pack_body(bytes);
    restored.unpack_body(bytes, offset);
    if (offset != bytes.size() || restored.values[0] != 8'h53 ||
        restored.more[0] != 4'h9 || restored.record.code != 8'h27)
      $fatal(1, "type mode roundtrip failed");
    $display("SVTYPES_TYPE_MODE_PASS");
    $finish;
  end
endmodule
'''
    _run_target('type-mode-' + ('inferred' if use_type_reference else 'explicit'),
                source + bench, 'SVTYPES_TYPE_MODE_PASS')
