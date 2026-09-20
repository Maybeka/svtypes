"""Ensure the public coverage-tools walkthrough runs end to end."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys


def test_coverage_tools_walkthrough(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "post_2_0_coverage_tools" / "run_demo.py"), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert {path.name for path in tmp_path.iterdir()} == {
        "packet.design.json", "packet.catalog.json", "add-opcode-two.proposal.json",
        "add-opcode-two.review.txt",
    }
    assert "add_bin" in (tmp_path / "add-opcode-two.review.txt").read_text(encoding="utf-8")
    catalog = json.loads((tmp_path / "packet.catalog.json").read_text(encoding="utf-8"))
    hierarchy = catalog["type_hierarchy"]
    packet_name = "packet.Packet[reserved_first:int=4,reserved_last:int=7]"
    top = next(item for item in hierarchy["nodes"] if item["type_name"] == "packet.Top")
    assert hierarchy["roots"] == sorted(["packet.Monitor", packet_name, "packet.Top"])
    assert top["references"] == [
        {
            "field": "packet",
            "kind": "object_ref",
            "target_type": packet_name,
        }
    ]
    assert any(
        group["declaration_name"] == "activity" and group["owner_type"] == "packet.Monitor"
        for group in catalog["groups"]
    )
    transaction = next(group for group in catalog["groups"] if group["declaration_name"] == "cg")
    assert [item["name"] for item in transaction["items"]] == [
        "opcode_cp", "addr_cp", "mode_cp", "kind_cp", "length_cp", "flag_cp", "sparse_opcode_cp", "mode_sequence",
        "opcode_mode", "opcode_compact",
    ]
    kind = next(point for point in transaction["points"] if point["name"] == "kind_cp")
    assert [bin_["name"] for bin_ in kind["bins"]] == ["request", "response", "error"]
    assert [bin_["display_selector"] for bin_ in kind["bins"]] == [
        "PacketKind.request", "PacketKind.response", "PacketKind.error",
    ]
    opcode = next(point for point in transaction["points"] if point["name"] == "opcode_cp")
    assert [bin_["name"] for bin_ in opcode["bins"]][:6] == [
        "zero", "one", "data", "control", "reserved", "other",
    ]
    flag = next(point for point in transaction["points"] if point["name"] == "flag_cp")
    assert [bin_["name"] for bin_ in flag["bins"]] == ["off"]
    assert flag["options"]["comparison_domain"] == {
        "four_state": False,
        "kind": "bit",
        "signed": False,
        "width": 1,
    }
    sparse = next(point for point in transaction["points"] if point["name"] == "sparse_opcode_cp")
    assert sparse["bins"][0]["selector"] == {
        "kind": "values",
        "items": [
            {"kind": "constant", "value": 1},
            {"kind": "range", "lower": {"kind": "constant", "value": 4}, "upper": {"kind": "constant", "value": 5}},
            {"kind": "constant", "value": 9},
        ],
    }
    assert {int(value): label for value, label in kind["enum_labels"].items()} == {
        0: "PacketKind.request",
        1: "PacketKind.response",
        2: "PacketKind.error",
    }
    addr = next(point for point in transaction["points"] if point["name"] == "addr_cp")
    assert [bin_["name"] for bin_ in addr["bins"]] == [
        "header", "page", "window", "high", "reserved",
    ]
    assert {cross["name"] for cross in transaction["crosses"]} == {"opcode_mode", "opcode_compact"}
    opcode_mode = next(cross for cross in transaction["crosses"] if cross["name"] == "opcode_mode")
    assert opcode_mode.get("member_views", []) == []
    opcode_compact = next(cross for cross in transaction["crosses"] if cross["name"] == "opcode_compact")
    assert len(opcode_compact["member_views"]) == 1
    local_opcode = opcode_compact["member_views"][0]
    assert local_opcode["member"] == "opcode_cp"
    assert [bin_["name"] for bin_ in local_opcode["point"]["bins"]] == [
        "compact", "control", "reserved",
    ]
    assert "semantic_id" not in local_opcode["point"]
    assert "begin_read" in {bin_["name"] for point in transaction["points"] if point["name"] == "mode_sequence" for bin_ in point["bins"]}
    assert {bin_["name"] for point in transaction["points"] if point["name"] == "length_cp" for bin_ in point["bins"]} >= {
        "length_band[0]", "length_band[1]", "length_band[2]", "length_band[3]",
    }
    length = next(point for point in transaction["points"] if point["name"] == "length_cp")
    assert [bin_["name"] for bin_ in length["bins"]][:5] == [
        "length_band[0]", "length_band[1]", "length_band[2]", "length_band[3]", "invalid",
    ]
    explicit_window = next(
        group for group in catalog["groups"] if group["declaration_name"] == "explicit_window"
    )
    assert explicit_window["cover_input_shapes"] == [
        {"name": "first", "type_name": "int"},
        {"name": "last", "type_name": "int"},
    ]
    assert explicit_window["points"][0]["cover_input_shapes"] == [
        {"name": "first", "type_name": "int"},
        {"name": "last", "type_name": "int"},
    ]
    parameter_window = next(
        group for group in catalog["groups"] if group["declaration_name"] == "parameter_window"
    )
    parameter_bin = parameter_window["points"][0]["bins"][0]
    assert parameter_bin["selector"]["kind"] == "array_split"
    range_selector = parameter_bin["selector"]["selector"]
    assert range_selector["lower"]["kind"] == "parameter_ref"
    assert range_selector["upper"]["kind"] == "parameter_ref"
