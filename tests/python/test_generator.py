import json
import os
import hashlib
import stat
from pathlib import Path
import pytest

from svtypes import DeclarationError, Int, Package, Parameter, SvObject, generate, get_package, svobj, unified_type_name


def _package():
    package = get_package("generation_test")
    package.clear()

    @svobj(registry=package)
    class Packet(SvObject):
        value = Int()

    return package


def test_generator_separates_unbound_templates_from_concrete_schemas(tmp_path):
    class Template(SvObject):
        WIDTH = Parameter[Int]()
        value = Int()

    class Concrete(SvObject):
        value = Int()

    result = generate([Template, Concrete], tmp_path, targets={"cpp", "schema"})
    assert not result.failed
    schema = json.loads((tmp_path / "schema.schema.json").read_text())
    manifest = json.loads((tmp_path / "svtypes-manifest.json").read_text())
    assert [entry["unified_type_name"] for entry in schema["types"]] == [unified_type_name(Concrete)]
    assert schema["unbound_templates"] == manifest["unbound_templates"] == [unified_type_name(Template)]
    assert len(manifest["schema_fingerprints"]) == len(manifest["encoding_fingerprints"]) == 1


@pytest.mark.parametrize("targets", [{"sv"}, {"cpp"}, {"sv", "cpp"}, {"schema"}])
def test_manifest_requires_wire_helpers_only_for_generated_code(tmp_path, targets):
    package = _package()
    result = generate(package, tmp_path, targets=targets)
    assert not result.failed
    manifest = json.loads((tmp_path / "svtypes-manifest.json").read_text())
    required = manifest["required_runtime_capabilities"]
    assert ("svtypes.generated-wire-helpers.v1" in required) == bool(targets & {"sv", "cpp"})


def test_generator_rejects_stale_manifest_path_outside_output_tree(tmp_path):
    package = _package()
    output = tmp_path / "generated"
    generate(package, output)
    outside = tmp_path / "user-file"
    outside.write_text("preserve this file")
    manifest_path = output / "svtypes-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"].append({
        "path": "../user-file",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    })
    manifest_path.write_text(json.dumps(manifest))
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(DeclarationError, match="artifact path"):
        generate(package, output, overwrite_modified=True)
    assert outside.read_text() == "preserve this file"
    assert before == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_generator_rejects_symbolic_link_in_artifact_path(tmp_path):
    package = _package()
    output = tmp_path / "generated"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "support").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DeclarationError, match="symbolic link"):
        generate(package, output, overwrite_modified=True)
    assert not list(outside.iterdir())


def test_generator_rejects_stale_artifact_symlink_without_touching_target(tmp_path):
    package = _package()
    output = tmp_path / "generated"
    generate(package, output)
    outside = tmp_path / "user-file"
    outside.write_text("preserve this file")
    (output / "retired.hpp").symlink_to(outside)
    manifest_path = output / "svtypes-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"].append({
        "path": "retired.hpp",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    })
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(DeclarationError, match="symbolic link"):
        generate(package, output, overwrite_modified=True)
    assert outside.read_text() == "preserve this file"
    assert (output / "retired.hpp").is_symlink()


@pytest.mark.parametrize("edited", ["main", "sv_support", "cpp_support"])
def test_generator_preserves_hand_edits_until_explicit_overwrite(tmp_path, edited):
    package = _package()
    generate(package, tmp_path)
    artifact = tmp_path / "generation_test.sv"
    if edited != "main":
        extension = "*.svh" if edited == "sv_support" else "*.hpp"
        artifact = next((tmp_path / "support").glob(extension))
    relative = str(artifact.relative_to(tmp_path))
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"\n// emergency change\n")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    checked = generate(package, tmp_path, mode="check")
    assert relative in checked.written
    refused = generate(package, tmp_path)
    assert refused.failed == (relative,)
    assert not refused.written
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    replaced = generate(package, tmp_path, overwrite_modified=True)
    assert not replaced.failed
    assert artifact.read_bytes() == original
    assert generate(package, tmp_path, mode="check").is_current


def test_generator_does_not_claim_existing_unmanaged_output(tmp_path):
    artifact = tmp_path / "generation_test.sv"
    artifact.write_text("user source\n")
    result = generate(_package(), tmp_path)
    assert result.failed == (artifact.name,)
    assert artifact.read_text() == "user source\n"
    assert not (tmp_path / "svtypes-manifest.json").exists()


@pytest.mark.parametrize("preserve", [None, "unmanaged", "edited"])
def test_stale_support_cleanup_preserves_user_contents(tmp_path, preserve):
    package = _package()
    generate(package, tmp_path)
    support = tmp_path / "support"
    retained = None
    if preserve == "unmanaged":
        retained = support / "user_notes.txt"
        retained.write_text("keep me")
    elif preserve == "edited":
        retained = next(support.glob("*.svh"))
        retained.write_text("keep me")
    result = generate(package, tmp_path, targets={"schema"})
    assert not result.failed
    if retained is None:
        assert not support.exists()
        assert not result.stale
    else:
        assert retained.read_text() == "keep me"
        assert list(support.iterdir()) == [retained]
        expected = (str(retained.relative_to(tmp_path)),) if preserve == "edited" else ()
        assert result.stale == expected


def test_multifile_generator_writes_and_checks_deterministic_artifacts(tmp_path):
    package = _package()
    first = generate(package, tmp_path)

    assert {path for path in first.written if not path.startswith("support/")} == {
        "generation_test.hpp",
        "generation_test.coverage-manifest.json",
        "generation_test.schema.json",
        "generation_test.sv",
        "svtypes-manifest.json",
    }
    support = [path for path in first.written if path.startswith("support/")]
    assert len(support) == 2
    sv_support = next(path for path in support if path.endswith(".svh"))
    cpp_support = next(path for path in support if path.endswith(".hpp"))
    assert f'`include "{sv_support}"' in (tmp_path / "generation_test.sv").read_text()
    assert f'#include "{cpp_support}"' in (tmp_path / "generation_test.hpp").read_text()
    assert "function void pack_body" in (tmp_path / sv_support).read_text()
    assert not first.failed
    assert not first.stale

    check = generate(package, tmp_path, mode="check")
    assert check.is_current
    assert set(check.unchanged) == set(first.written)

    manifest = json.loads((tmp_path / "svtypes-manifest.json").read_text())
    assert manifest["generator"] == "svtypes"
    assert manifest["schema_fingerprints"]
    assert manifest["encoding_fingerprints"]
    assert manifest["required_runtime_capabilities"] == [
        "svtypes.generated-field-visitors.v1", "svtypes.optional-codec-diagnostics.v1",
        "svtypes.generated-wire-helpers.v1",
        "svtypes.generated-field-operations.v1",
    ]
    assert {entry["path"] for entry in manifest["artifacts"]} == set(first.written) - {"svtypes-manifest.json"}
    assert {entry["path"] for entry in manifest["artifacts"] if not entry["path"].startswith("support/")} == {
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
    for path in tmp_path.iterdir():
        if path.is_file():
            path.chmod(0o640)
    before = {path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
              for path in tmp_path.iterdir() if path.is_file()}

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
    after = {path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
             for path in tmp_path.iterdir() if path.is_file()}
    assert after == before


def test_generator_restores_nested_and_manifest_files_when_stale_removal_fails(tmp_path, monkeypatch):
    package = _package()
    generate(package, tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            path.chmod(0o640)
    before = {path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
              for path in tmp_path.rglob("*") if path.is_file()}
    stale = sorted(path for path in before if path.suffix == ".hpp")
    assert len(stale) >= 2
    failure = tmp_path / stale[-1]
    real_unlink = Path.unlink

    def fail_stale_unlink(path, *args, **kwargs):
        if path == failure:
            raise OSError("injected stale removal failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_stale_unlink)
    result = generate(package, tmp_path, targets={"sv", "schema"})
    assert result.failed == (stale[-1].as_posix(),)
    after = {path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_generator_preserves_existing_file_mode_when_overwriting(tmp_path):
    package = _package()
    generate(package, tmp_path)
    artifact = tmp_path / "generation_test.hpp"
    artifact.chmod(0o640)
    before = stat.S_IMODE(artifact.stat().st_mode)
    artifact.write_text(artifact.read_text() + "\n// user edit\n")
    result = generate(package, tmp_path, overwrite_modified=True)
    assert not result.failed
    assert stat.S_IMODE(artifact.stat().st_mode) == before


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

    first = {str(path.relative_to(first_dir)): path.read_bytes() for path in first_dir.rglob("*") if path.is_file()}
    second = {str(path.relative_to(second_dir)): path.read_bytes() for path in second_dir.rglob("*") if path.is_file()}
    assert first == second
