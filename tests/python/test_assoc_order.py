import subprocess
from pathlib import Path

from svtypes import AssocArray, Int


def test_assoc_array_orders_entries_by_encoded_key_bytes():
    codec = AssocArray(Int(), Int())
    payload = codec.pack({-1: 1, 256: 2, 0: 3})
    offset = 4
    keys = []
    for _ in range(3):
        key, count = Int().unpack(payload[offset:])
        offset += count
        _, count = Int().unpack(payload[offset:])
        offset += count
        keys.append(key)
    assert keys == [0, 256, -1]


def test_cpp_assoc_array_uses_the_same_encoded_key_order(tmp_path: Path):
    source = tmp_path / "main.cpp"
    source.write_text(
        '#include "svtypes.hpp"\n'
        "int main() {\n"
        "  std::map<int32_t,int32_t> value{{-1,1},{256,2},{0,3}};\n"
        "  std::vector<uint8_t> bytes; svtypes::pack(value, bytes);\n"
        "  const int expected[] = {0, 256, -1}; size_t offset = 4;\n"
        "  for (int key : expected) { int32_t actual, item; "
        "svtypes::unpack(actual, bytes, offset); svtypes::unpack(item, bytes, offset); "
        "if (actual != key) return 1; }\n"
        "  return 0;\n"
        "}\n"
    )
    executable = tmp_path / "assoc_order"
    result = subprocess.run(
        [
            "g++", "-std=c++20", str(source), "-I",
            str(Path.cwd() / "svtypes_runtime" / "cpp"), "-o", str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
