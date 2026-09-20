"""E1 conformance tests for the independently packaged backend SDK."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
for source in (
    _ROOT / "extensions" / "svtypes_design_manifest" / "src",
    _ROOT / "extensions" / "svtypes_backend_sdk" / "src",
):
    sys.path.insert(0, str(source))

from svtypes import Bit, CovPoint, SvObject, bins, covergroup  # noqa: E402
from svtypes_design_manifest import export_design_manifest, write_design_manifest  # noqa: E402
from svtypes_backend_sdk import Artifact, ArtifactSet, BackendError, BackendMetadata, Diagnostic, build, load_backend, run_conformance  # noqa: E402
from svtypes_backend_sdk.__main__ import main as backend_main  # noqa: E402


class _Packet(SvObject):
    opcode = Bit(1)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            zero = bins[0]


def _design() -> dict[str, object]:
    return export_design_manifest([_Packet], design_name="backend-test")


class _GoodBackend:
    metadata = BackendMetadata("test.good", "1", ("svtypes.design-manifest/v1",))

    def validate(self, design):
        return (Diagnostic("TEST-WARN", "warning", "kept as build metadata"),)

    def render(self, design, options):
        return ArtifactSet((Artifact("nested/result.txt", b"stable", "text/plain"),))


def test_reference_backend_builds_deterministic_artifacts(tmp_path: Path):
    design = _design()
    backend = load_backend("svtypes_backend_sdk.reference:backend")
    output = tmp_path / "generated"

    first = build(backend, design, output)
    first_manifest = (output / "svtypes-artifacts.json").read_bytes()
    second = build(backend, design, output)

    assert first["artifacts"] == ["coverage-reference.json"]
    assert second["backend"]["id"] == "svtypes.reference-json"
    assert (output / "svtypes-artifacts.json").read_bytes() == first_manifest
    content = json.loads((output / "coverage-reference.json").read_text(encoding="utf-8"))
    assert content["manifest_digest"] == design["design"]["manifest_digest"]


def test_sdk_freezes_input_and_preserves_existing_output_on_failure(tmp_path: Path):
    class MutatingBackend:
        metadata = BackendMetadata("test.mutating", "1", ("svtypes.design-manifest/v1",))

        def validate(self, design):
            return ()

        def render(self, design, options):
            design["design"]["name"] = "changed"
            raise AssertionError("unreachable")

    output = tmp_path / "generated"
    output.mkdir()
    (output / "prior.txt").write_text("prior", encoding="utf-8")
    with pytest.raises(BackendError, match="render failed"):
        build(MutatingBackend(), _design(), output)
    assert (output / "prior.txt").read_text(encoding="utf-8") == "prior"


def test_sdk_rejects_capability_and_unsafe_or_duplicate_artifacts(tmp_path: Path):
    class RequiringBackend(_GoodBackend):
        metadata = BackendMetadata(
            "test.requires", "1", ("svtypes.design-manifest/v1",), ("coverage.cross.function_bins",)
        )

    class UnsafeBackend(_GoodBackend):
        metadata = BackendMetadata("test.unsafe", "1", ("svtypes.design-manifest/v1",))

        def render(self, design, options):
            return ArtifactSet((Artifact("../escape.txt", b"no"),))

    with pytest.raises(BackendError, match="requires unsupported capability"):
        build(RequiringBackend(), _design(), tmp_path / "capability")
    with pytest.raises(BackendError, match="unsafe artifact path"):
        build(UnsafeBackend(), _design(), tmp_path / "unsafe")
    assert not (tmp_path / "unsafe").exists()


def test_sdk_conformance_and_file_input_round_trip(tmp_path: Path):
    design_path = write_design_manifest(tmp_path / "design.json", _design())
    assert design_path.exists()
    run_conformance(_GoodBackend(), _design())
    result = build(_GoodBackend(), _design(), tmp_path / "artifacts", options={"style": "brief"})

    assert result["diagnostics"] == [{"code": "TEST-WARN", "severity": "warning", "message": "kept as build metadata"}]
    manifest = json.loads((tmp_path / "artifacts" / "svtypes-artifacts.json").read_text(encoding="utf-8"))
    assert manifest["options"] == {"style": "brief"}


def test_backend_cli_builds_reference_artifact(tmp_path: Path):
    design = write_design_manifest(tmp_path / "design.json", _design())
    output = tmp_path / "cli-output"
    assert backend_main([
        "build", "--backend", "svtypes_backend_sdk.reference:backend", "--input", str(design), "--out", str(output),
    ]) == 0
    assert (output / "coverage-reference.json").exists()
