from svtypes import SvObject, Int, svobj, get_package

@svobj
class A(SvObject):
    x = Int()

@svobj(name="B_custom")
class B(SvObject):
    y = Int()

def test_svobj_overloads():
    pkg = get_package("$unit")

    assert pkg.get("A") is A
    assert pkg.get("B_custom") is B
    assert pkg.get("B") is None

    print("svobj overloads test passed!")

if __name__ == "__main__":
    test_svobj_overloads()
