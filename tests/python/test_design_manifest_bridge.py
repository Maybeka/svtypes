"""E0 regression tests for the independently packaged design-manifest bridge."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


_BRIDGE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "extensions"
    / "svtypes_design_manifest"
    / "src"
)
sys.path.insert(0, str(_BRIDGE_SOURCE))

from svtypes import Bit, CovPoint, Parameter, SvObject, bins, covergroup  # noqa: E402
from svtypes_design_manifest import (  # noqa: E402
    DesignManifestError,
    canonical_json_bytes,
    export_design_manifest,
    load_design_manifest,
    validate_design_manifest,
    write_design_manifest,
)
from svtypes_design_manifest.__main__ import main  # noqa: E402


class _Packet(SvObject):
    opcode = Bit(2)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            read = bins[0]
            write = bins[1]


def test_manifest_export_is_deterministic_and_excludes_provenance_from_digest():
    root = Path(__file__).resolve().parents[2]
    with_provenance = export_design_manifest([_Packet], design_name="packet", source_root=root)
    without_provenance = export_design_manifest([_Packet], design_name="packet")

    assert canonical_json_bytes(with_provenance) == canonical_json_bytes(
        export_design_manifest([_Packet], design_name="packet", source_root=root)
    )
    assert with_provenance["design"]["manifest_digest"] == without_provenance["design"]["manifest_digest"]
    assert with_provenance["types"][0]["unified_type_name"].endswith("._Packet")
    group = with_provenance["coverage"]["covergroups"][0]
    assert with_provenance["capabilities"] == ["coverage.ir.v1"]
    assert group["covergroup_type_id"].endswith("::cg")
    assert group["definition"]["points"][0]["bins"][0]["name"] == "read"
    assert "display_order" not in group
    assert all(not Path(item["file"]).is_absolute() for item in with_provenance["provenance"] if "file" in item)
    reordered = copy.deepcopy(with_provenance)
    reordered["presentation"]["covergroup_display_order"][group["covergroup_type_id"]] = {
        "items": [{"kind": "point", "name": "opcode_cp"}],
        "bins": {"opcode_cp": ["write", "read"]},
    }
    validate_design_manifest(reordered)
    assert reordered["design"]["manifest_digest"] == with_provenance["design"]["manifest_digest"]


def test_manifest_write_load_and_tamper_validation(tmp_path: Path):
    document = export_design_manifest([_Packet])
    path = write_design_manifest(tmp_path / "packet.json", document)
    assert load_design_manifest(path) == document

    tampered = copy.deepcopy(document)
    tampered["coverage"]["covergroups"][0]["owner_type"] = "other.Packet"
    with pytest.raises(DesignManifestError, match="digest"):
        validate_design_manifest(tampered)


def test_manifest_declares_parameter_ref_capability_when_coverage_uses_parameter():
    class Packet(SvObject):
        opcode = Bit(4)
        reserved = Parameter()(15)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                reserved_bin = bins[self.reserved]

    manifest = export_design_manifest([Packet])
    assert "coverage.ir.v1.parameter_ref" in manifest["capabilities"]
    selector = manifest["coverage"]["covergroups"][0]["definition"]["points"][0]["bins"][0]["selector"]
    assert selector == {"kind": "parameter_ref", "name": "reserved", "type": "int"}


def test_published_schema_and_minimal_fixture_are_kept_in_sync():
    bridge_root = _BRIDGE_SOURCE.parent
    schema = json.loads(
        (bridge_root / "src" / "svtypes_design_manifest" / "schemas" / "design-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    fixture = json.loads(
        (bridge_root / "tests" / "data" / "minimal.design-manifest.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["format"]["const"] == fixture["format"]
    assert set(schema["required"]).issubset(fixture)
    validate_design_manifest(fixture)


def test_export_cli_uses_explicit_type_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = tmp_path / "bridge_design.py"
    module.write_text(
        "from svtypes import Bit, CovPoint, SvObject, bins, covergroup\n"
        "class Packet(SvObject):\n"
        "    opcode = Bit(1)\n"
        "    @covergroup\n"
        "    def cg(self):\n"
        "        class opcode_cp(CovPoint, source=self.opcode):\n"
        "            zero = bins[0]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    output = tmp_path / "packet.design.json"

    assert main(["bridge_design:Packet", "--out", str(output)]) == 0
    assert load_design_manifest(output)["design"]["name"] == "design"


def test_manifest_rejects_non_type_and_duplicate_inputs():
    with pytest.raises(DesignManifestError, match="must contain types"):
        export_design_manifest([object()])
    with pytest.raises(DesignManifestError, match="duplicate type"):
        export_design_manifest([_Packet, _Packet])
