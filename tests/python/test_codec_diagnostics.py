"""Operation-local optional diagnostics, independent of required wire parsing."""

import pytest
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from svtypes import (Bit, CodecSession, CovPoint, Object, ObjectRegistry, SvObject, SvStruct, UnpackContext,
                     bins, checked_unpack, constraint, covergroup, encoding_descriptor)


class DiagnosticValue(SvObject):
    number = Bit[8]()


class SamplingWithoutCodec(SvObject):
    number = Bit[4]()

    def __init__(self):
        super().__init__()
        self.coverage.instantiate()

    @constraint
    def legal(self):
        self.number < 8

    @covergroup
    def coverage(self):
        class values(CovPoint, source=self.number):
            all_values = bins[0:15].split(16)


def test_randomize_and_coverage_sampling_do_not_request_codec_diagnostics(monkeypatch):
    value = SamplingWithoutCodec()

    def unexpected(cls):
        pytest.fail("randomization or sampling requested codec metadata")

    monkeypatch.setattr(SamplingWithoutCodec, "_prepare_encoding_metadata", classmethod(unexpected))
    monkeypatch.setattr(SamplingWithoutCodec, "_encoding_fingerprint_hex", classmethod(unexpected))
    for _ in range(5):
        assert value.randomize()
        value.coverage.sample()
    assert value.coverage.instance.snapshot_document()["sample_count"] == 5


def test_python_off_skips_expected_digest_and_strict_rejects_mismatch(monkeypatch):
    value = DiagnosticValue()
    value.number.value = 7
    data = bytearray(value.to_bytes())
    descriptor = encoding_descriptor(DiagnosticValue)
    fingerprint_offset = 21 + len(DiagnosticValue._encoding_type_name().encode())
    data[fingerprint_offset] ^= 1
    codec = DiagnosticValue.codec_template()
    restored, used = codec.unpack(bytes(data))
    assert used == len(data) and restored.number.value == 7
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        codec.unpack(bytes(data), UnpackContext(diagnostics="strict"))
    context = UnpackContext()
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        checked_unpack(codec, bytes(data), descriptor, context=context)
    assert context.diagnostics == "off"
    monkeypatch.setattr(DiagnosticValue, "_encoding_fingerprint_hex",
                        classmethod(lambda cls: pytest.fail("off computed expected digest")))
    assert codec.unpack(bytes(data))[0].number.value == 7
    with pytest.raises(ValueError, match="Not enough bytes"):
        codec.unpack(bytes(data[:fingerprint_offset + 1]))


def test_invalid_diagnostic_policy_rejects_at_context_creation():
    with pytest.raises(ValueError, match="diagnostics"):
        UnpackContext(diagnostics="unknown")


class DiagnosticRecord(SvStruct):
    child = DiagnosticValue()


class DiagnosticParent(SvObject):
    record = DiagnosticRecord()


def test_generated_cpp_nested_graph_strict_detects_child_digest_mismatch(tmp_path):
    registry = ObjectRegistry()

    class GraphChild(SvObject):
        number = Bit[8]()

    registry.register(GraphChild)

    class GraphParent(SvObject):
        child = Object["GraphChild"](registry=registry)

    source = tmp_path / "nested_diagnostics.cpp"
    source.write_text('#include "svtypes.hpp"\n#include <algorithm>\n#include <cassert>\n'
                      + GraphChild.to_cpp_obj() + "\n" + GraphParent.to_cpp_obj() + r'''
int main() {
    GraphChild child;
    child.number = 73;
    GraphParent original;
    original.child = &child;
    std::vector<uint8_t> bytes;
    original.pack_body(bytes);
    const std::array<uint8_t, 4> magic{'S', 'V', 'X', 'O'};
    auto nested = std::search(bytes.begin() + 4, bytes.end(), magic.begin(), magic.end());
    assert(nested != bytes.end());
    size_t header = static_cast<size_t>(nested - bytes.begin());
    uint32_t name_length = 0;
    for (unsigned i = 0; i < 4; ++i) name_length |= uint32_t(bytes[header + 16 + i]) << (8 * i);
    bytes.at(header + 20 + name_length) ^= 1;
    svtypes::clear_object_registry();
    GraphParent normal;
    size_t offset = 0;
    normal.unpack_body(bytes, offset);
    assert(offset == bytes.size() && normal.child && normal.child->number == 73);
    svtypes::clear_object_registry();
    GraphParent checked;
    svtypes::DecodeContext strict{svtypes::DiagnosticPolicy::strict};
    try {
        svtypes::DecodeContextGuard scope(strict);
        offset = 0;
        checked.unpack_body(bytes, offset);
        assert(false);
    } catch (const std::runtime_error& error) {
        assert(std::string(error.what()).find("fingerprint mismatch") != std::string::npos);
    }
    assert(!svtypes::strict_decode_diagnostics());
    delete normal.child;
    delete checked.child;
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "nested_diagnostics"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_checked_unpack_propagates_strict_through_struct_to_child_and_restores_context():
    source = DiagnosticParent()
    source.record.child.number.value = 19
    data = bytearray(source.to_bytes())
    child_header = data.index(b"SVXO", 5)
    name_length = int.from_bytes(data[child_header + 16:child_header + 20], "little")
    data[child_header + 20 + name_length] ^= 1
    codec = DiagnosticParent.codec_template()
    context = UnpackContext(CodecSession())
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        checked_unpack(codec, bytes(data), encoding_descriptor(DiagnosticParent), context=context)
    assert context.diagnostics == "off"
    assert context._depth == 0
    assert codec.unpack(bytes(data), context)[0].record.child.number.value == 19

    def decode(policy):
        local_context = UnpackContext(CodecSession(), diagnostics=policy)
        try:
            restored, _ = codec.unpack(bytes(data), local_context)
            return restored.record.child.number.value
        except ValueError as exc:
            assert "fingerprint mismatch" in str(exc)
            return "strict rejection"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(decode, ["off", "strict"] * 8)) == [19, "strict rejection"] * 8


def test_optional_count_comparison_does_not_disable_version_or_identity_checks():
    data = bytearray(DiagnosticValue().to_bytes())
    data[7:9] = (42).to_bytes(2, "little")
    codec = DiagnosticValue.codec_template()
    assert codec.unpack(bytes(data))[1] == len(data)
    with pytest.raises(ValueError, match="field-count mismatch"):
        codec.unpack(bytes(data), UnpackContext(diagnostics="strict"))
    data[5:7] = (999).to_bytes(2, "little")
    with pytest.raises(ValueError, match="version mismatch"):
        codec.unpack(bytes(data))


def test_cpp_decode_context_is_nested_exception_safe_and_thread_local(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "diagnostics.cpp"
    source.write_text(r'''
#include "svtypes.hpp"
#include <cassert>
#include <thread>
int main() {
    constexpr auto literal = svtypes::fingerprint_literal(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
    static_assert(literal[0] == 0x01 && literal[31] == 0xef);
    std::vector<uint8_t> literal_header, string_header;
    svtypes::pack_object_header("Record", literal, 1, 1, literal_header);
    svtypes::pack_object_header("Record", std::string(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"),
        1, 1, string_header);
    assert(literal_header == string_header);
    {
        svtypes::DecodeContext strict{svtypes::DiagnosticPolicy::strict};
        svtypes::DecodeContextGuard scope(strict);
        uint64_t id = 0;
        size_t offset = 0;
        svtypes::unpack_object_header("Record", literal, 1, id, literal_header, offset);
        assert(id == 1 && offset == literal_header.size());
        literal_header.back() ^= 1;
        offset = 0;
        try {
            svtypes::unpack_object_header("Record", literal, 1, id, literal_header, offset);
            assert(false);
        } catch (const std::runtime_error& error) {
            assert(std::string(error.what()).find("fingerprint mismatch") != std::string::npos);
        }
    }
    std::vector<uint8_t> bytes;
    svtypes::pack_object_header("Record", std::string(64, '0'), 1, 1, bytes);
    bytes.back() ^= 1;
    uint64_t id;
    size_t offset = 0;
    // off does not even parse an invalid expected fingerprint.
    svtypes::unpack_object_header("Record", "not a digest", 999, id, bytes, offset);
    assert(offset == bytes.size() && id == 1);
    svtypes::DecodeContext strict{svtypes::DiagnosticPolicy::strict}, off;
    try {
        svtypes::DecodeContextGuard scope(strict);
        assert(svtypes::strict_decode_diagnostics());
        {
            svtypes::DecodeContextGuard inner(off);
            assert(!svtypes::strict_decode_diagnostics());
        }
        assert(svtypes::strict_decode_diagnostics());
        std::thread independent([] { assert(!svtypes::strict_decode_diagnostics()); });
        independent.join();
        offset = 0;
        svtypes::unpack_object_header("Record", std::string(64, '0'), 1, id, bytes, offset);
        assert(false);
    } catch (const std::runtime_error& error) {
        assert(std::string(error.what()).find("fingerprint mismatch") != std::string::npos);
    }
    assert(!svtypes::strict_decode_diagnostics());
    bytes.resize(3);
    offset = 0;
    try {
        svtypes::unpack_object_header("Record", "", 0, id, bytes, offset);
        assert(false);
    } catch (const std::runtime_error&) {}
}
''')
    compiler = shutil.which("c++")
    assert compiler
    executable = tmp_path / "diagnostics"
    subprocess.run([compiler, "-std=c++20", "-pthread", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_first_and_repeated_python_pack_do_not_compute_diagnostic_hashes(monkeypatch):
    import svtypes.schema as schema

    class PreparedValue(SvObject):
        number = Bit[32]()

    value = PreparedValue()
    monkeypatch.setattr(schema, "_fingerprint", lambda _: pytest.fail("pack computed a hash"))
    first = value.to_bytes()
    assert value.to_bytes() == first


def test_encoding_metadata_refreshes_after_handle_registration_and_declaration_refresh():
    from svtypes import Object, ObjectRegistry

    registry = ObjectRegistry()

    class Owner(SvObject):
        child = Object["Child"](registry=registry)

    class Child(SvObject):
        number = Bit[8]()

    registry.register(Child)
    expected = encoding_descriptor(Owner).encoding_fingerprint
    value = Owner()
    data = value.to_bytes()
    offset = 21 + len(Owner._encoding_type_name().encode())
    assert data[offset:offset + 32] == expected
    Owner.extra = Bit[16]()
    Owner.extra.__set_name__(Owner, "extra")
    Owner.refresh_declarations()
    expected = encoding_descriptor(Owner).encoding_fingerprint
    assert Owner().to_bytes()[offset:offset + 32] == expected


def test_nested_value_declaration_refresh_updates_owner_prepared_fingerprint():
    class Inner(SvStruct):
        first = Bit[8]()

    class Outer(SvObject):
        inner = Inner()

    before = encoding_descriptor(Outer).encoding_fingerprint
    Inner.second = Bit[16]()
    Inner.second.__set_name__(Inner, "second")
    Inner.refresh_declarations()
    expected = encoding_descriptor(Outer).encoding_fingerprint
    assert expected != before
    data = Outer().to_bytes()
    offset = 21 + len(Outer._encoding_type_name().encode())
    assert data[offset:offset + 32] == expected


def test_metadata_refresh_tracks_nested_handles_and_ignores_unrelated_types(monkeypatch):
    from svtypes import Array, Object, ObjectRegistry

    registry = ObjectRegistry()

    class Record(SvStruct):
        handles = Array[Object["Child"](registry=registry), 2]()

    class Owner(SvObject):
        record = Record()

    class Unrelated(SvObject):
        value = Bit[8]()

    class Child(SvObject):
        value = Bit[8]()

    monkeypatch.setattr(Unrelated, "_prepare_encoding_metadata",
                        classmethod(lambda cls: pytest.fail("unrelated metadata was refreshed")))

    def stored_fingerprint():
        return Owner._SvObject__svtypes_prepared_encoding_fingerprint

    before = stored_fingerprint()
    registry.register(Child)
    registered = encoding_descriptor(Owner).encoding_fingerprint
    assert registered != before and stored_fingerprint() == registered
    data = Owner().to_bytes()
    offset = 21 + len(Owner._encoding_type_name().encode())
    assert data[offset:offset + 32] == registered
    registry.clear()
    assert stored_fingerprint() == encoding_descriptor(Owner).encoding_fingerprint == before
    Record.extra = Bit[16]()
    Record.extra.__set_name__(Record, "extra")
    Record.refresh_declarations()
    assert stored_fingerprint() == encoding_descriptor(Owner).encoding_fingerprint != before


def test_metadata_dependency_index_does_not_keep_temporary_classes_alive():
    import gc
    import weakref

    def make_type():
        class Temporary(SvObject):
            value = Bit[8]()
        return weakref.ref(Temporary)

    reference = make_type()
    gc.collect()
    assert reference() is None


def test_registration_elsewhere_refreshes_handles_to_the_renamed_type():
    from svtypes import Object, ObjectRegistry

    first, second = ObjectRegistry(), ObjectRegistry()
    first.path, second.path = "first", "second"

    class Child(SvObject):
        value = Bit[8]()

    first.register(Child)

    class Owner(SvObject):
        child = Object["Child"](registry=first)

    before = Owner._SvObject__svtypes_prepared_encoding_fingerprint
    second.register(Child)
    expected = encoding_descriptor(Owner).encoding_fingerprint
    assert expected != before
    assert Owner._SvObject__svtypes_prepared_encoding_fingerprint == expected
