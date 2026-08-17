from svtypes import Package, Int, Parameter

def test_package_vars():
    print("Testing Package Variables and Parameters...")
    pkg = Package("my_pkg")

    # Define variables and parameters
    pkg.my_var = Int()
    pkg.MY_PARAM = Parameter()(1)

    # Test intrinsic access via .value. Parameters are immutable after binding.
    pkg.my_var.value = 100
    try:
        pkg.MY_PARAM.value = 2
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass

    assert pkg.my_var.value == 100
    assert pkg.MY_PARAM.value == 1

    # Test codegen
    sv_code = pkg.to_sv_pkg()
    print("--- SV Package ---")
    print(sv_code)

    assert "int my_var;" in sv_code
    assert "parameter int MY_PARAM = 32'd1;" in sv_code

    cpp_code = pkg.to_cpp_pkg()
    print("\n--- C++ Namespace ---")
    print(cpp_code)

    assert "int32_t my_var;" in cpp_code
    assert "static constexpr int32_t MY_PARAM = 1;" in cpp_code

    print("\nPackage Vars Test Passed!")

if __name__ == "__main__":
    test_package_vars()
