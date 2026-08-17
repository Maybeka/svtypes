from svtypes import get_package
import tests.fixtures.package.A as A_mod

def test_package_equivalence():
    print("Testing Package Equivalence...")

    # In tests.fixtures.package.A, __package__ is 'tests.fixtures.package'
    # Our code should use only 'package'
    full_pkg_name = A_mod.__package__
    assert isinstance(full_pkg_name, str)
    print(f"DEBUG: A_mod.__package__ = {full_pkg_name}")

    short_pkg_name = full_pkg_name.split('.')[-1]
    print(f"DEBUG: short_pkg_name = {short_pkg_name}")

    pkg = get_package(short_pkg_name)
    pkg.clear()

    # Manually collect module to get Parameters
    pkg.collect_module("tests.fixtures.package.A")

    sv_code = pkg.to_sv_pkg()
    print("--- SV Package ---")
    print(sv_code)

    assert f"package {short_pkg_name};" in sv_code
    assert "parameter int t = 32'd1;" in sv_code
    assert "class A;" in sv_code

    cpp_code = pkg.to_cpp_pkg()
    print("\n--- C++ Namespace ---")
    print(cpp_code)

    assert f"namespace {short_pkg_name} {{" in cpp_code
    assert "static constexpr int32_t t = 1;" in cpp_code
    assert "struct A : public svtypes::SvObject {" in cpp_code

    print("\nPackage Equivalence Test Passed!")

if __name__ == "__main__":
    test_package_equivalence()
