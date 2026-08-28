import json
import os

from svtypes import Int, Package, SvObject, generate, get_package, svobj


def _package():
    package = get_package("generation_test")
    package.clear()

    @svobj(registry=package)
    class Packet(SvObject):
        value = Int()

    return package


def test_multifile_generator_writes_and_checks_deterministic_artifacts(tmp_path):
    package = _package()
    first = generate(package, tmp_path)

    assert set(first.written) == {
        "generation_test.hpp",
        "generation_test.coverage-manifest.json",
        "generation_test.schema.json",
        "generation_test.sv",
        "svtypes-manifest.json",
    }
    assert not first.failed
    assert not first.stale

    check = generate(package, tmp_path, mode="check")
    assert check.is_current
    assert set(check.unchanged) == set(first.written)

    manifest = json.loads((tmp_path / "svtypes-manifest.json").read_text())
    assert manifest["generator"] == "svtypes"
    assert manifest["schema_fingerprints"]
    assert manifest["encoding_fingerprints"]
    assert {entry["path"] for entry in manifest["artifacts"]} == {
        "generation_test.hpp",
        "generation_test.coverage-manifest.json",
        "generation_test.schema.json",
        "generation_test.sv",
    }


def test_generator_check_reports_modified_and_missing_files_without_writing(tmp_path):
    package = _package()
    generate(package, tmp_path)
    sv_path = tmp_path / "generation_test.sv"
    sv_path.write_text("user modification\n")
    schema_path = tmp_path / "generation_test.schema.json"
    schema_path.unlink()

    result = generate(package, tmp_path, mode="check")
    assert set(result.written) == {
        "generation_test.schema.json",
        "generation_test.sv",
    }
    assert sv_path.read_text() == "user modification\n"
    assert not schema_path.exists()


def test_generator_removes_only_unchanged_managed_stale_files(tmp_path):
    package = _package()
    generate(package, tmp_path)
    generated_cpp = tmp_path / "generation_test.hpp"

    result = generate(package, tmp_path, targets={"sv", "schema"})
    assert not generated_cpp.exists()
    assert not result.stale

    # Recreate a managed C++ file, modify it, then remove C++ from the target
    # set. The generator must report but preserve user-modified content.
    generate(package, tmp_path, targets={"sv", "cpp", "schema"})
    generated_cpp.write_text("user-owned replacement\n")
    result = generate(package, tmp_path, targets={"sv", "schema"})
    assert result.stale == ("generation_test.hpp",)
    assert generated_cpp.read_text() == "user-owned replacement\n"


def test_generator_rolls_back_all_published_files_after_io_failure(tmp_path, monkeypatch):
    package = _package()
    generate(package, tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}

    @svobj(registry=package)
    class AddedPacket(SvObject):
        other = Int()

    real_replace = os.replace
    calls = 0

    def fail_second_publish(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr("svtypes.generator.os.replace", fail_second_publish)
    result = generate(package, tmp_path)

    assert result.failed
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert after == before


def test_generation_is_independent_of_registration_order(tmp_path):
    class Alpha(SvObject):
        value = Int()

    class Beta(SvObject):
        value = Int()

    forward = Package("order_independent")
    forward.register(Alpha)
    forward.register(Beta)
    reverse = Package("order_independent")
    reverse.register(Beta)
    reverse.register(Alpha)

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    generate(forward, first_dir)
    generate(reverse, second_dir)

    first = {path.name: path.read_bytes() for path in first_dir.iterdir()}
    second = {path.name: path.read_bytes() for path in second_dir.iterdir()}
    assert first == second
