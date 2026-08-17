import math
import subprocess
import tempfile
from pathlib import Path

from examples.milestone_3_svtypes_parity.tests.types import Color, Inner, ManyTypesTx


def _make_inner(a: int, b: int) -> Inner:
    inner = Inner()
    inner.a.value = a
    inner.b.value = b
    return inner


def _set_input(tx: ManyTypesTx) -> None:
    tx.u1.value = 1
    tx.u7.value = 0x7E
    tx.u9.value = 0x1AB
    tx.u33.value = 0x1_2345_6789
    tx.u65.value = 0x1_0123_4567_89AB_CDEF
    tx.s5.value = -7
    tx.s12.value = -1023
    tx.i32.value = -0x1234567
    tx.i64.value = -0x0102030405060708
    tx.color.value = Color.GREEN
    tx.text.value = "many-types"
    tx.fp64.value = -math.pi
    tx.fp32.value = 12.5
    tx.fixed_bits.value = [0, 1, 6, 7]
    tx.fixed_signed.value = [-1, -17, 15]
    tx.matrix.value = [[1, -2], [3, -4]]
    tx.dyn_bits.value = [0, 0x155, 0x3FF]
    tx.dyn_signed.value = [-1, -100, 127]
    tx.q_colors.value = [Color.RED, Color.BLUE, Color.GREEN]
    tx.assoc.value = {"apple": 11, "banana": -22}
    tx.q_inner.value = [_make_inner(10, 0x11), _make_inner(-20, 0x22)]
    tx.inner_arr.value = [_make_inner(30, 0x33), _make_inner(-40, 0x44)]
    tx.nested.a.value = 50
    tx.nested.b.value = 0x55


def test_cpp_manytypes_generated_parity():
    tx = ManyTypesTx()
    _set_input(tx)
    input_bytes = tx.to_bytes()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "input.bin").write_bytes(input_bytes)
        (tmp_path / "model.hpp").write_text(
            "#pragma once\n"
            '#include "svtypes.hpp"\n\n'
            f"{Color.to_cpp_enum()}\n\n"
            f"{Inner.to_cpp_obj()}\n\n"
            f"{ManyTypesTx.to_cpp_obj()}\n",
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
    std::vector<uint8_t> buffer((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());

    ManyTypesTx tx;
    size_t offset = 0;
    tx.unpack(buffer, offset);
    require(offset == buffer.size(), "offset mismatch");

    require(tx.u1 == 1, "bad u1");
    require(tx.u7 == 0x7e, "bad u7");
    require(tx.u9 == 0x1ab, "bad u9");
    require(tx.u33[0] == 0x89 && tx.u33[1] == 0x67 && tx.u33[2] == 0x45 &&
            tx.u33[3] == 0x23 && tx.u33[4] == 0x01, "bad u33");
    require(tx.u65[0] == 0xef && tx.u65[8] == 0x01, "bad u65");
    require(tx.s5 == -7, "bad s5");
    require(tx.s12 == -1023, "bad s12");
    require(tx.i32 == -0x1234567, "bad i32");
    require(tx.i64 == -0x0102030405060708LL, "bad i64");
    require(tx.color == Color::GREEN, "bad color");
    require(tx.text == "many-types", "bad text");
    require(std::fabs(tx.fp64 - (-3.14159265358979323846)) < 1e-12, "bad fp64");
    require(std::fabs(tx.fp32 - 12.5f) < 1e-6, "bad fp32");
    require(tx.fixed_bits[0] == 0 && tx.fixed_bits[3] == 7, "bad fixed_bits");
    require(tx.fixed_signed[0] == -1 && tx.fixed_signed[1] == -17 && tx.fixed_signed[2] == 15, "bad fixed_signed");
    require(tx.matrix[0][0] == 1 && tx.matrix[1][1] == -4, "bad matrix");
    require(tx.dyn_bits.size() == 3 && tx.dyn_bits[2] == 0x3ff, "bad dyn_bits");
    require(tx.dyn_signed.size() == 3 && tx.dyn_signed[1] == -100, "bad dyn_signed");
    require(tx.q_colors.size() == 3 && tx.q_colors[1] == Color::BLUE, "bad q_colors");
    require(tx.assoc["apple"] == 11 && tx.assoc["banana"] == -22, "bad assoc");
    require(tx.q_inner.size() == 2 && tx.q_inner[1] != nullptr && tx.q_inner[1]->a == -20 && tx.q_inner[1]->b == 0x22, "bad q_inner");
    require(tx.inner_arr[0] != nullptr && tx.inner_arr[1] != nullptr && tx.inner_arr[0]->a == 30 && tx.inner_arr[1]->b == 0x44, "bad inner_arr");
    require(tx.nested.a == 50 && tx.nested.b == 0x55, "bad nested");

    tx.u1 = 0;
    tx.u7 = 0x55;
    tx.u9 = 0x12a;
    tx.u33 = {0x11, 0x11, 0x11, 0x11, 0x01};
    tx.u65 = {0x10, 0x32, 0x54, 0x76, 0x98, 0xba, 0xdc, 0xfe, 0x01};
    tx.s5 = -8;
    tx.s12 = 1023;
    tx.i32 = 0x10203040;
    tx.i64 = 0x0102030405060708LL;
    tx.color = Color::BLUE;
    tx.text = "cpp-many-types";
    tx.fp64 = 2.75;
    tx.fp32 = -3.5f;
    tx.fixed_bits = {7, 6, 1, 0};
    tx.fixed_signed = {-16, 0, 15};
    tx.matrix = {{{9, 8}, {7, 6}}};
    tx.dyn_bits = {1, 2, 0x3fe};
    tx.dyn_signed = {-128, -1, 0, 127};
    tx.q_colors = {Color::GREEN, Color::RED};
    tx.assoc.clear();
    tx.assoc["cat"] = 31;
    tx.assoc["dog"] = -41;
    tx.q_inner.resize(2);
    tx.q_inner[0] = new Inner();
    tx.q_inner[0]->a = 101;
    tx.q_inner[0]->b = 0xa1;
    tx.q_inner[1] = new Inner();
    tx.q_inner[1]->a = -102;
    tx.q_inner[1]->b = 0xa2;
    tx.inner_arr[0]->a = 201;
    tx.inner_arr[0]->b = 0xb1;
    tx.inner_arr[1]->a = -202;
    tx.inner_arr[1]->b = 0xb2;
    tx.nested.a = 303;
    tx.nested.b = 0xc3;

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
            ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "manytypes_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            ["./manytypes_test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stderr + run_result.stdout

        out_tx = ManyTypesTx()
        out_tx.from_bytes((tmp_path / "output.bin").read_bytes())

    assert out_tx.u1.value == 0
    assert out_tx.u7.value == 0x55
    assert out_tx.u9.value == 0x12A
    assert out_tx.u33.value == 0x1_1111_1111
    assert out_tx.u65.value == 0x1_FEDC_BA98_7654_3210
    assert out_tx.s5.value == -8
    assert out_tx.s12.value == 1023
    assert out_tx.i32.value == 0x10203040
    assert out_tx.i64.value == 0x0102030405060708
    assert out_tx.color.value == Color.BLUE
    assert out_tx.text.value == "cpp-many-types"
    assert abs(out_tx.fp64.value - 2.75) < 1e-12
    assert abs(out_tx.fp32.value - (-3.5)) < 1e-6
    assert out_tx.fixed_bits.value == [7, 6, 1, 0]
    assert out_tx.fixed_signed.value == [-16, 0, 15]
    assert out_tx.matrix.value == [[9, 8], [7, 6]]
    assert out_tx.dyn_bits.value == [1, 2, 0x3FE]
    assert out_tx.dyn_signed.value == [-128, -1, 0, 127]
    assert out_tx.q_colors.value == [Color.GREEN, Color.RED]
    assert out_tx.assoc.value == {"cat": 31, "dog": -41}
    assert [(v.a.value, v.b.value) for v in out_tx.q_inner] == [(101, 0xA1), (-102, 0xA2)]
    assert [(v.a.value, v.b.value) for v in out_tx.inner_arr] == [(201, 0xB1), (-202, 0xB2)]
    assert out_tx.nested.a.value == 303
    assert out_tx.nested.b.value == 0xC3
