import pytest

from svtypes import (
    AssocArray,
    Bits,
    DynArray,
    Enum,
    Int,
    Object,
    Real,
    ShortReal,
    String,
    SvObject,
    clear_object_registry,
    clear_object_registry,
    get_package,
    get_object,
    svobj,
    unregister_object,
    sv_weak_runtime_file,
    cpp_include_dir,
    runtime_root,
    sv_runtime_file,
    __version__,
)


class ErrorColor(Enum, width=32, signed=False):
    RED = 0
    GREEN = 1


@pytest.mark.parametrize(
    ("type_obj", "payload"),
    [
        (Bits(16), b"\x01"),
        (Int(), b"\x01\x02\x03"),
        (Real(), b"\x00" * 7),
        (ShortReal(), b"\x00" * 3),
        (ErrorColor(), b"\x00\x00\x00"),
    ],
)
def test_fixed_width_unpack_rejects_truncated_payload(type_obj, payload):
    with pytest.raises(ValueError, match="Not enough bytes"):
        type_obj.unpack(payload)


def test_string_unpack_rejects_truncated_length():
    with pytest.raises(ValueError, match="String length"):
        String().unpack(b"\x01\x02")


def test_string_unpack_rejects_truncated_data():
    with pytest.raises(ValueError, match="String data"):
        String().unpack(b"\x05\x00\x00\x00abc")


def test_dyn_array_unpack_rejects_truncated_length():
    with pytest.raises(ValueError, match="DynArray length"):
        DynArray(Int()).unpack(b"\x01")


def test_dyn_array_unpack_rejects_truncated_element():
    with pytest.raises(ValueError, match="Not enough bytes"):
        DynArray(Int()).unpack(b"\x01\x00\x00\x00\x01")


def test_assoc_array_unpack_rejects_truncated_length():
    with pytest.raises(ValueError, match="AssocArray length"):
        AssocArray(String(), Int()).unpack(b"\x01")


def test_assoc_array_unpack_rejects_truncated_value():
    payload = b"\x01\x00\x00\x00" + String().pack("key") + b"\x01"
    with pytest.raises(ValueError, match="Not enough bytes"):
        AssocArray(String(), Int()).unpack(payload)


def test_late_bound_object_descriptor_supports_recursive_graphs():
    registry = get_package("test_svtypes_errors_recursive")

    @svobj(registry=registry)
    class RecursiveNode(SvObject):
        data = Int()
        next = Object("RecursiveNode", registry=registry)

    node = RecursiveNode()
    node.data.value = 3
    node.next = node

    out, count = RecursiveNode().unpack(node.to_bytes())
    assert count == len(node.to_bytes())
    assert out.next is out


def test_object_null_presence_marker_roundtrip():
    class NullablePayload(SvObject):
        value = Int()

    payload = NullablePayload()
    assert payload.pack(None) == b"\x00"
    value, count = payload.unpack(b"\x00")
    assert value is None
    assert count == 1


def test_object_registry_tracks_global_id():
    clear_object_registry()

    class RegistryPayload(SvObject):
        data = Int()

    payload = RegistryPayload()
    assert get_object(payload.svtypes_object_number) is payload

    clone = RegistryPayload()
    decoded, _ = clone.unpack(payload.to_bytes())
    assert decoded.svtypes_object_number == payload.svtypes_object_number
    assert get_object(payload.svtypes_object_number) is decoded


def test_runtime_object_registry_supports_neutral_cleanup_api():
    clear_object_registry()

    class CleanupPayload(SvObject):
        data = Int()

    payload = CleanupPayload()
    object_number = payload.svtypes_object_number
    assert get_object(object_number) is payload
    assert unregister_object(object_number) is payload
    assert get_object(object_number) is None
    assert unregister_object(object_number) is None


def test_type_registry_rejects_duplicate_name_for_different_type():
    from svtypes.object import ObjectRegistry

    class First(SvObject):
        pass

    class Second(SvObject):
        pass

    registry = ObjectRegistry()
    registry.register(First, "Duplicate")
    registry.register(First, "Duplicate")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Second, "Duplicate")


def test_public_version_matches_project_metadata():
    assert __version__ == "1.0.0"


def test_public_exception_hierarchy_is_importable():
    from svtypes import CompatibilityError, DecodeError, SvTypesError

    assert issubclass(CompatibilityError, DecodeError)
    assert issubclass(DecodeError, SvTypesError)


def test_experimental_weak_runtime_is_discoverable():
    path = sv_weak_runtime_file()
    assert path.is_file()
    assert "SVTYPES_USE_WEAK_REFERENCE" in path.read_text()


def test_all_runtime_assets_are_discoverable():
    assert runtime_root().is_dir()
    assert sv_runtime_file().is_file()
    assert sv_weak_runtime_file().is_file()
    assert (cpp_include_dir() / "svtypes.hpp").is_file()
