import pytest

from svtypes import SvObject, Int, Array, Bits, svobj
import svtypes


@svobj
class MultiDim(SvObject):
    matrix = Array(Array(Int(), 2), 3)
    matrix_v2 = Array(Int(), (3, 2)) # New syntax
    packed_matrix = Bits((2, 8))
    packed_matrix_array = Array(Bits((2, 8)), 3)

def test_multi_dim():
    md = MultiDim()

    # 1. Test indexing
    assert isinstance(md.matrix[0], svtypes.collection._Array)
    assert isinstance(md.matrix[0][0], Int)
    assert isinstance(md.matrix_v2[0], svtypes.collection._Array)
    assert isinstance(md.matrix_v2[0][0], Int)

    # 2. Test .value access
    md.matrix[0][0].value = 42
    md.matrix_v2[0][0].value = 42

    assert md.matrix.value == md.matrix_v2.value

    # 4. Test codegen
    sv_code = MultiDim.to_sv_obj()
    print("--- SV MultiDim ---")
    print(sv_code)
    assert "int matrix [3] [2];" in sv_code
    assert "int matrix_v2 [3] [2];" in sv_code
    assert "bit [1:0] [7:0] packed_matrix;" in sv_code
    assert "bit [1:0] [7:0] packed_matrix_array [3];" in sv_code

    cpp_code = MultiDim.to_cpp_obj()
    print("\n--- C++ MultiDim ---")
    print(cpp_code)
    assert "std::array<std::array<int32_t, 2>, 3> matrix_v2;" in cpp_code

    print("Multi-dimensional test passed!")


def test_multidimensional_packed_bits_shape_and_flat_bytes():
    shaped = Bits((2, 3, 5), signed=True)
    flat = Bits(30, signed=True)

    assert shaped.shape == (2, 3, 5)
    assert shaped.width == 30
    assert flat.shape is None
    assert shaped.sv_decl("payload") == "bit signed [1:0] [2:0] [4:0] payload"
    assert shaped.pack(-7) == flat.pack(-7)
    assert shaped.unpack(flat.pack(-7)) == (-7, 4)
    assert shaped.cpp_decl("payload") == flat.cpp_decl("payload")


def test_one_element_shape_normalizes_to_flat_identity():
    from svtypes import LogicBits, unified_type_name

    assert Bits((8,)).shape is None
    assert unified_type_name(Bits((8,))) == unified_type_name(Bits(8))
    assert LogicBits((8,)).shape is None
    assert unified_type_name(LogicBits((8,))) == unified_type_name(LogicBits(8))


def test_packed_width_resource_limit_is_enforced_at_declaration():
    from svtypes import DeclarationError, LogicBits

    with pytest.raises(DeclarationError, match="declaration limit"):
        Bits((1025, 1025))
    with pytest.raises(DeclarationError, match="declaration limit"):
        LogicBits(1_048_577)


@pytest.mark.parametrize("shape", [(), (0,), (-1, 2)])
def test_multidimensional_packed_bits_rejects_invalid_shape(shape):
    with pytest.raises(ValueError):
        Bits(shape)


@pytest.mark.parametrize("shape", [(2, 3.5), (True, 2)])
def test_multidimensional_packed_bits_rejects_non_integer_shape(shape):
    with pytest.raises(TypeError):
        Bits(shape)

if __name__ == "__main__":
    test_multi_dim()
