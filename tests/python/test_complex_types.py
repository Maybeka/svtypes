import subprocess
from pathlib import Path

from svtypes import SvObject, SvStruct, Int, Bit, Array, DynArray, Queue, AssocArray, String, svobj

@svobj
class Pixel(SvStruct):
    r = Bit(8)
    g = Bit(8)
    b = Bit(8)

@svobj
class Image(SvObject):
    pixels = Array(Pixel(), 4) # Fixed array of 4 pixels

def test_array_access():
    print("Testing Array Access...")
    img = Image()

    # 1. Test index access (Modeling Object)
    assert isinstance(img.pixels[0], Pixel)
    img.pixels[0].r.value = 255
    assert img.pixels[0].r.value == 255

    # 2. Test .value access (Plain Python Data)
    val = img.pixels.value
    assert len(val) == 4
    assert val[0] is img.pixels[0]

    print("Array Access Passed!")

def test_dyn_array():
    print("Testing DynArray...")
    da = DynArray(Int())
    da.value = [1, 2, 3]
    assert da.value == [1, 2, 3]
    assert isinstance(da[0], Int)
    assert da[0].value == 1

    # Test pack/unpack
    b = da.to_bytes()
    da2 = DynArray(Int())
    da2.from_bytes(b)
    assert da2.value == [1, 2, 3]

    # Test codegen
    assert da.sv_decl("da") == "int da []"
    assert da.cpp_decl("da") == "std::vector<int32_t> da"
    print("DynArray Passed!")

def test_queue():
    print("Testing Queue...")
    q = Queue(Int())
    q.value = [10, 20]
    assert q.value == [10, 20]

    q.push_back(30)
    assert q.value == [10, 20, 30]

    val = q.pop_front()
    assert val == 10
    assert q.value == [20, 30]

    # Test codegen
    assert q.sv_decl("q") == "int q [$]"
    print("Queue Passed!")

def test_assoc_array():
    print("Testing AssocArray...")
    aa = AssocArray(String(), Int())
    aa.value = {"apple": 1, "banana": 2}
    assert aa.value == {"apple": 1, "banana": 2}
    assert isinstance(aa["apple"], Int)
    assert aa["apple"].value == 1

    # Test pack/unpack
    b = aa.to_bytes()
    aa2 = AssocArray(String(), Int())
    aa2.from_bytes(b)
    assert aa2.value == {"apple": 1, "banana": 2}

    # Test codegen
    assert aa.sv_decl("aa") == "int aa [string]"
    assert aa.cpp_decl("aa") == "std::map<std::string, int32_t> aa"
    print("AssocArray Passed!")

def test_codegen_complex():
    print("\nTesting Codegen for Complex Types...")

    sv_pixel = Pixel.to_sv_obj()
    print("--- SV Pixel ---")
    print(sv_pixel)
    assert "typedef struct packed {" in sv_pixel
    assert "bit [7:0] r;" in sv_pixel
    assert "} Pixel_t;" in sv_pixel

    sv_img = Image.to_sv_obj()
    print("\n--- SV Image ---")
    print(sv_img)
    assert "Pixel_t pixels [4];" in sv_img

    cpp_img = Image.to_cpp_obj()
    print("\n--- C++ Image ---")
    print(cpp_img)
    assert "std::array<Pixel, 4> pixels;" in cpp_img

    cpp_pixel = Pixel.to_cpp_obj()
    assert "struct Pixel {" in cpp_pixel
    assert "public svtypes::SvObject" not in cpp_pixel


def test_struct_is_by_value_without_identity_or_envelope():
    pixel = Pixel()
    pixel.r.value = 1
    pixel.g.value = 2
    pixel.b.value = 3

    encoded = pixel.to_bytes()
    assert encoded == b"\x01\x02\x03"
    assert b"SVXO" not in encoded
    assert not hasattr(pixel, "svtypes_object_number")

    decoded, count = Pixel().unpack(encoded)
    assert count == 3
    assert (decoded.r.value, decoded.g.value, decoded.b.value) == (1, 2, 3)


def test_generated_cpp_struct_is_by_value(tmp_path: Path):
    (tmp_path / "model.hpp").write_text(
        '#include "svtypes.hpp"\n' + Pixel.to_cpp_obj() + "\n" + Image.to_cpp_obj()
    )
    (tmp_path / "main.cpp").write_text(
        '#include "model.hpp"\n'
        "int main() {\n"
        "  Image in; in.pixels[0].r = 7; in.pixels[0].g = 8; in.pixels[0].b = 9;\n"
        "  std::vector<uint8_t> bytes; in.pack(bytes);\n"
        "  Image out; size_t offset = 0; out.unpack(bytes, offset);\n"
        "  return out.pixels[0].r == 7 && out.pixels[0].g == 8 && "
        "out.pixels[0].b == 9 ? 0 : 1;\n"
        "}\n"
    )
    result = subprocess.run(
        [
            "g++",
            "-std=c++20",
            str(tmp_path / "main.cpp"),
            "-I",
            str(Path.cwd() / "svtypes_runtime" / "cpp"),
            "-o",
            str(tmp_path / "struct_test"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(tmp_path / "struct_test")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    print("\nCodegen Complex Passed!")

if __name__ == "__main__":
    test_array_access()
    test_dyn_array()
    test_queue()
    test_assoc_array()
    test_codegen_complex()
