from svtypes import SvObject, Enum, Int, Parameter, svobj

@svobj
class MyData(SvObject):
    ID = Parameter()(1)
    status = Int()

def test_codegen():
    print("Testing Code Generation...")

    # SV Code
    sv_code = MyData.to_sv_obj()
    print("--- SV Class ---")
    print(sv_code)

    assert "class MyData #(parameter int ID = 32'd1) extends svtypes_pkg::sv_object;" in sv_code
    assert "int status;" in sv_code

    # C++ Code
    cpp_code = MyData.to_cpp_obj()
    print("\n--- C++ Struct ---")
    print(cpp_code)

    assert "template <int32_t ID = 1>" in cpp_code
    assert "struct MyData : public svtypes::SvObject {" in cpp_code
    assert "int32_t status;" in cpp_code

    print("\nCodegen Test Passed!")

if __name__ == "__main__":
    test_codegen()
