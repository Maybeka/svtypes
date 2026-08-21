from svtypes import SvObject, Int, Bit, Enum, Real, String, svobj

class e_color(Enum, width=8, signed=False):
    RED = 0
    GREEN = 1
    BLUE = 2

@svobj
class InnerObj(SvObject):
    a = Int()
    b = Bit(8)

@svobj
class ComplexObj(SvObject):
    f1 = Int()
    f2 = Bit(16)
    f3 = e_color()
    f4 = Real()
    f5 = String()
    nested = InnerObj()

def test_serialization():
    print("Testing Serialization (Pack/Unpack)...")

    obj = ComplexObj()
    obj.f1.value = 0x12345678
    obj.f2.value = 0xABCD
    obj.f3.value = e_color.GREEN
    obj.f4.value = 3.14159
    obj.f5.value = "Hello SV"
    obj.nested.a.value = 42
    obj.nested.b.value = 0xFF

    # Pack
    packed = obj.pack(obj)
    print(f"Packed size: {len(packed)} bytes")

    # Unpack
    new_obj, size = obj.unpack(packed)
    assert size == len(packed)

    # Verify
    assert new_obj.f1.value == 0x12345678
    assert new_obj.f2.value == 0xABCD
    assert new_obj.f3.value == e_color.GREEN
    assert abs(new_obj.f4.value - 3.14159) < 1e-6
    assert new_obj.f5.value == "Hello SV"
    assert new_obj.nested.a.value == 42
    assert new_obj.nested.b.value == 0xFF

    print("\nSerialization Test Passed!")

if __name__ == "__main__":
    test_serialization()
