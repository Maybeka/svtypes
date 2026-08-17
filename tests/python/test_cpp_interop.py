import subprocess
from pathlib import Path

from svtypes import SvObject, Int, Array, Queue, svobj

@svobj
class Config(SvObject):
    version = Int()
    scores = Array(Int(), 3)
    tags = Queue(Int())

def test_cpp_interop(tmp_path: Path):
    # 1. Setup data in Python
    cfg = Config()
    cfg.version.value = 1
    cfg.scores.value = [10, 20, 30]
    cfg.tags.value = [100, 200]

    # 2. Pack to input.bin
    (tmp_path / "input.bin").write_bytes(cfg.to_bytes())
    with open(tmp_path / "input.bin", "rb") as f:
        assert f.read() == cfg.to_bytes()
    print("Python: Packed Config to input.bin")

    # 3. Generate C++ header
    cpp_model = Config.to_cpp_obj()
    with open(tmp_path / "model.hpp", "w") as f:
        f.write("#pragma once\n")
        f.write('#include "svtypes.hpp"\n\n')
        f.write(cpp_model)
    print("Python: Generated model.hpp")

    # 4. Write C++ Main Driver
    cpp_main = """
#include <iostream>
#include <fstream>
#include <vector>
#include "model.hpp"

int main() {
    // Read input.bin
    std::ifstream is("input.bin", std::ios::binary);
    std::vector<uint8_t> buffer((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());
    is.close();

    Config cfg;
    size_t offset = 0;
    cfg.unpack(buffer, offset);

    std::cout << "C++: Unpacked version=" << cfg.version << std::endl;

    // Modify data
    cfg.version = 2;
    cfg.scores[1] = 99;
    cfg.tags.push_back(300);

    // Pack to output.bin
    std::vector<uint8_t> out_buf;
    cfg.pack(out_buf);

    std::ofstream os("output.bin", std::ios::binary);
    os.write((char*)out_buf.data(), out_buf.size());
    os.close();

    std::cout << "C++: Modified and packed to output.bin" << std::endl;
    return 0;
}
"""
    with open(tmp_path / "main.cpp", "w") as f:
        f.write(cpp_main)

    # 5. Compile C++ code
    print("Compiling C++...")
    compile_cmd = ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "interop_test"]
    result = subprocess.run(compile_cmd, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    # 6. Run C++ code
    print("Running C++...")
    result = subprocess.run(["./interop_test"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    print(result.stdout)

    # 7. Verify in Python
    packed_from_cpp = (tmp_path / "output.bin").read_bytes()

    new_cfg = Config()
    new_cfg.from_bytes(packed_from_cpp)

    print(f"Python: Unpacked version={new_cfg.version.value}")
    assert new_cfg.version.value == 2
    assert new_cfg.scores.value == [10, 99, 30]
    assert new_cfg.tags.value == [100, 200, 300]

    print("\nSUCCESS: Data passed from Python -> C++ -> Python correctly!")

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_cpp_interop(Path(tmp))
