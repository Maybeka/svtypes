from svtypes import Array, Bit, Enum, Int, Logic, Real, String, SvObject, SvStruct, is_randomizable


class Color(Enum, width=8, signed=False):
    RED = 0
    BLUE = 1


class Header(SvStruct):
    addr = Bit(32)
    data = Logic(8)


def test_is_randomizable_core_model():
    assert is_randomizable(Bit(8))
    assert is_randomizable(Logic(8))
    assert is_randomizable(Int())
    assert is_randomizable(Color())
    assert is_randomizable(Color)
    assert is_randomizable(Header())
    assert is_randomizable(Array(Bit(8), 4))
    assert not is_randomizable(String())
    assert not is_randomizable(Real())
    assert not is_randomizable(Array(String(), 2))
