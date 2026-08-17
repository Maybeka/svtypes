from svtypes import SvObject, Int, Array, DynArray, svobj, Queue

@svobj
class Base(SvObject):
    id = Int()
    data = Array(Int(), 2)

@svobj
class Derived(Base):
    extra = Int()
    q = Queue(Int())

def test_cross_serialization():
    # 1. Generate Code
    print("--- SystemVerilog ---")
    print(Base.to_sv_obj())
    print(Derived.to_sv_obj())

    print("\n--- C++ ---")
    print(Base.to_cpp_obj())
    print(Derived.to_cpp_obj())

    # 2. Verify Python Packing order (should be: id, data[0], data[1], extra, len(q), q[0]...)
    d = Derived()
    d.id.value = 1
    d.data.value = [10, 20]
    d.extra.value = 99
    d.q.value = [5, 6]

    packed = d.to_bytes()
    print(f"\nPacked bytes (len={len(packed)}): {packed.hex()}")

    # Expected field payload after object presence + class envelope:
    # id: 01 00 00 00
    # data[0]: 0a 00 00 00
    # data[1]: 14 00 00 00
    # extra: 63 00 00 00
    # q.len: 02 00 00 00
    # q[0]: 05 00 00 00
    # q[1]: 06 00 00 00
    expected_payload_hex = "01000000" + "0a000000" + "14000000" + "63000000" + "02000000" + "05000000" + "06000000"
    assert packed[0] == 1
    assert packed[1:5] == b"SVXO"
    assert b"Derived" in packed
    assert packed.hex().endswith(expected_payload_hex)
    print("Python packing order verified (matches Base-to-Derived)")

    # 3. Verify Codegen inheritance
    sv_derived = Derived.to_sv_obj()
    assert "extends Base" in sv_derived
    assert f'pack_object_header("{Derived._encoding_type_name()}", "{Derived._encoding_fingerprint_hex()}", 4, __svtypes_object_number, bytes);' in sv_derived
    assert f'unpack_object_header("{Derived._encoding_type_name()}", "{Derived._encoding_fingerprint_hex()}", 4, incoming_svtypes_object_number, bytes, offset);' in sv_derived

    cpp_derived = Derived.to_cpp_obj()
    assert ": public Base" in cpp_derived
    assert f'pack_object_header("{Derived._encoding_type_name()}", "{Derived._encoding_fingerprint_hex()}", 4, __svtypes_object_number, bytes);' in cpp_derived

    print("\nCross-language serialization test passed!")

if __name__ == "__main__":
    test_cross_serialization()
