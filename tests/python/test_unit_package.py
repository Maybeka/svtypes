from svtypes import SvObject, Parameter, Int, svobj, get_package
import sys

# Since this script is run as a main module, __package__ will be None or ''
# svobj should default it to "$unit"

@svobj
class TopLevelObj(SvObject):
    x = Int()

TopLevelParam = Parameter()(123)

def test_unit_package():
    print("Testing $unit (Global) Package...")

    # Check that TopLevelObj is in "$unit"
    pkg = get_package("$unit")
    pkg.clear()
    pkg.register(TopLevelObj)
    pkg.add_parameter("TopLevelParam", TopLevelParam)

    sv_code = pkg.to_sv_pkg()
    print("--- SV $unit ---")
    print(sv_code)

    assert "package $unit;" not in sv_code
    assert "parameter int TopLevelParam = 32'd123;" in sv_code
    assert "class TopLevelObj;" in sv_code

    cpp_code = pkg.to_cpp_pkg()
    print("\n--- C++ Global ---")
    print(cpp_code)

    assert "namespace $unit" not in cpp_code
    assert "static constexpr int32_t TopLevelParam = 123;" in cpp_code
    assert "struct TopLevelObj : public svtypes::SvObject {" in cpp_code

    print("\n$unit Package Test Passed!")

if __name__ == "__main__":
    test_unit_package()


class A:
    b: B

class B:
    a: A
