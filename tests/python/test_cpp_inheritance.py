import subprocess
import tempfile
from pathlib import Path

from examples.milestone_3_svtypes_parity.tests.types import BaseTx, Color, Inner, M3Tx


def test_cpp_inheritance_generated_parity():
    tx = M3Tx()
    tx.id.value = 11
    tx.data.value = [12, 13]
    tx.label.value = "base"
    tx.addr.value = 0x1234
    tx.serial.value = -55
    tx.color.value = Color.GREEN
    tx.ratio.value = 1.5
    tx.temp.value = 2.5
    tx.dyn.value = [1, 2, 3]
    tx.q.value = [4, 5]
    tx.inner.a.value = 6
    tx.inner.b.value = 0x7

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "input.bin").write_bytes(tx.to_bytes())
        (tmp_path / "model.hpp").write_text(
            "#pragma once\n"
            '#include "svtypes.hpp"\n\n'
            f"{Color.to_cpp_enum()}\n\n"
            f"{Inner.to_cpp_obj()}\n\n"
            f"{BaseTx.to_cpp_obj()}\n\n"
            f"{M3Tx.to_cpp_obj()}\n",
            encoding="utf-8",
        )
        (tmp_path / "main.cpp").write_text(
            r'''
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include "model.hpp"

static void require(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << msg << std::endl;
        std::exit(2);
    }
}

int main() {
    std::ifstream is("input.bin", std::ios::binary);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());

    M3Tx tx;
    size_t offset = 0;
    tx.unpack(bytes, offset);
    require(offset == bytes.size(), "offset mismatch");
    require(tx.id == 11, "bad inherited id");
    require(tx.data[0] == 12 && tx.data[1] == 13, "bad inherited data");
    require(tx.label == "base", "bad inherited label");
    require(tx.addr == 0x1234, "bad addr");
    require(tx.serial == -55, "bad serial");
    require(tx.color == Color::GREEN, "bad color");
    require(std::fabs(tx.ratio - 1.5) < 1e-12, "bad ratio");
    require(std::fabs(tx.temp - 2.5f) < 1e-6, "bad temp");
    require(tx.dyn.size() == 3 && tx.dyn[2] == 3, "bad dyn");
    require(tx.q.size() == 2 && tx.q[1] == 5, "bad queue");
    require(tx.inner.a == 6 && tx.inner.b == 7, "bad inner");

    tx.id = 21;
    tx.data = {22, 23};
    tx.label = "derived";
    tx.addr = 0x4567;
    tx.serial = 99;
    tx.color = Color::BLUE;
    tx.ratio = -3.25;
    tx.temp = -4.5f;
    tx.dyn = {8, 9};
    tx.q = {10, 11, 12};
    tx.inner.a = 13;
    tx.inner.b = 14;

    std::vector<uint8_t> out;
    tx.pack(out);
    std::ofstream os("output.bin", std::ios::binary);
    os.write(reinterpret_cast<const char*>(out.data()), out.size());
    return 0;
}
''',
            encoding="utf-8",
        )

        compile_result = subprocess.run(
            ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "inheritance_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            ["./inheritance_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stderr + run_result.stdout

        out = M3Tx()
        out.from_bytes((tmp_path / "output.bin").read_bytes())

    assert out.id.value == 21
    assert out.data.value == [22, 23]
    assert out.label.value == "derived"
    assert out.addr.value == 0x4567
    assert out.serial.value == 99
    assert out.color.value == Color.BLUE
    assert abs(out.ratio.value - (-3.25)) < 1e-12
    assert abs(out.temp.value - (-4.5)) < 1e-6
    assert out.dyn.value == [8, 9]
    assert out.q.value == [10, 11, 12]
    assert out.inner.a.value == 13
    assert out.inner.b.value == 14
