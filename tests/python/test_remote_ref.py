import subprocess
from pathlib import Path

import pytest

from svtypes import RemoteRef, RemoteRefValue, SvObject, unified_type_name


def test_remote_ref_has_frozen_nullable_eight_byte_contract():
    codec = RemoteRef("acme.Device")
    null = RemoteRefValue("acme.Device", 0)
    value = RemoteRefValue("acme.Device", 0x0102030405060708)

    assert null.is_null
    assert codec.pack(null) == bytes(8)
    assert codec.pack(value) == bytes.fromhex("0807060504030201")
    assert codec.unpack(codec.pack(value)) == (value, 8)
    assert unified_type_name(codec) == "svtypes.RemoteRef[target=acme.Device]"


def test_remote_ref_rejects_wrong_declared_target_and_range():
    codec = RemoteRef("acme.Device")
    with pytest.raises(TypeError, match="target type mismatch"):
        codec.pack(RemoteRefValue("acme.Other", 1))
    with pytest.raises(ValueError, match="outside uint64"):
        RemoteRefValue("acme.Device", 1 << 64)


def test_generated_cpp_remote_ref_roundtrip(tmp_path: Path):
    class Message(SvObject):
        target = RemoteRef("acme.Device")

    header = tmp_path / "model.hpp"
    header.write_text('#include "svtypes.hpp"\n' + Message.to_cpp_obj())
    source = tmp_path / "main.cpp"
    source.write_text(
        '#include "model.hpp"\n'
        "int main() {\n"
        "  Message in; in.target.object_number = 0x0102030405060708ULL;\n"
        "  std::vector<uint8_t> bytes; in.pack(bytes);\n"
        "  Message out; size_t offset = 0; out.unpack(bytes, offset);\n"
        "  return out.target.target_type_name == \"acme.Device\" && "
        "out.target.object_number == in.target.object_number ? 0 : 1;\n"
        "}\n"
    )
    executable = tmp_path / "remote_ref_test"
    result = subprocess.run(
        [
            "g++",
            "-std=c++20",
            str(source),
            "-I",
            str(Path.cwd() / "svtypes_runtime" / "cpp"),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
