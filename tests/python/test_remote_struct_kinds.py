"""Generated struct value and packed-unique conformance on the configured target."""

import os
import json
from pathlib import Path
import shlex
import subprocess

import pytest

from test_struct_kinds import (PackedHeader, UnpackedHeader, Payload, SmallPacked,
                               WholeUnique, PackedQueueUnique, MixedUnpacked, MixedHost, StructLayers,
                               PartialPayload, StructCoverage, StructContainerCoverage,
                               PackedCycle, ConstrainedPackedCycle, LayerElement, StructContainerLayers,
                               StructChild, HandleRecord, StructFlag, EnumPacked, EnumPackedHost,
                               PackedCoverageExpressions)


@pytest.mark.remote_sv
def test_remote_struct_value_codecs_and_packed_unique():
    root = Path(__file__).resolve().parents[2]
    output = root / ".tmp/struct_kinds_conformance"
    output.mkdir(parents=True, exist_ok=True)
    declarations = StructFlag.to_sv_enum() + "\n\n" + "\n\n".join(cls.to_sv_obj() for cls in (
        PackedHeader, UnpackedHeader, Payload, SmallPacked, WholeUnique, PackedQueueUnique,
        MixedUnpacked, MixedHost, StructLayers, PartialPayload, StructCoverage,
        StructContainerCoverage, PackedCycle, ConstrainedPackedCycle, LayerElement, StructContainerLayers,
        StructChild, HandleRecord, EnumPacked, EnumPackedHost, PackedCoverageExpressions))
    bench = r"""
module tb;
  PackedHeader_t packed_value;
  UnpackedHeader_t unpacked_value;
  Payload_t source, restored;
  WholeUnique scalar_unique;
  PackedQueueUnique queue_unique;
  MixedHost mixed;
  StructLayers layers;
  PartialPayload_t partial;
  StructCoverage covered;
  StructCoverage::StructCoverage__svtypes_coverage auto_cov;
  StructCoverage::StructCoverage__cg__coverage explicit_cov;
  StructContainerCoverage covered_items;
  StructContainerCoverage::StructContainerCoverage__svtypes_coverage item_cov;
  MixedUnpacked_t sample_value;
  PackedCycle packed_cycle;
  ConstrainedPackedCycle constrained_cycle;
  bit [15:0] seen;
  bit [2:0] legal_seen;
  StructContainerLayers struct_layers;
  StructChild child;
  HandleRecord_t graph_source, graph_restored;
  byte unsigned child_bytes[$], graph_bytes[$];
  EnumPackedHost enum_host;
  bit enum_domain_supported;
  svtypes_pkg::runtime_capabilities capabilities;
  PackedCoverageExpressions expr_host;
  PackedCoverageExpressions::PackedCoverageExpressions__cg__coverage expr_cov;
  byte unsigned packed_bytes[$], unpacked_bytes[$], payload_bytes[$];
  int offset;
  initial begin
    capabilities = svtypes_pkg::get_runtime_capabilities();
    if (!capabilities.provides("svtypes.struct-kinds.v1"))
      $fatal(1, "struct data contract capability missing");
    packed_value = '{opcode: 4'ha, length: 12'habc};
    unpacked_value = '{opcode: 4'ha, length: 12'habc};
    PackedHeader_packer::pack(packed_value, packed_bytes);
    UnpackedHeader_packer::pack(unpacked_value, unpacked_bytes);
    if (packed_bytes != unpacked_bytes || packed_bytes.size() != 3 ||
        packed_bytes[0] != 'h0a || packed_bytes[1] != 'hbc || packed_bytes[2] != 'h0a)
      $fatal(1, "struct memberwise encoding differs");
    source.header = packed_value;
    source.label = "payload";
    source.data = {8'h01, 8'h02, 8'hff};
    Payload_packer::pack(source, payload_bytes);
    offset = 0;
    Payload_packer::unpack(restored, payload_bytes, offset);
    if (offset != payload_bytes.size() || restored.header != source.header ||
        restored.label != source.label || restored.data != source.data)
      $fatal(1, "unpacked nested/dynamic fields did not roundtrip");
    scalar_unique = new();
    if (!scalar_unique.randomize()) $fatal(1, "packed unique incorrectly failed");
    if (scalar_unique.left.high != 0 || scalar_unique.right.high != 0 ||
        scalar_unique.left.low != 1 || scalar_unique.right.low != 2)
      $fatal(1, "packed unique solved individual members instead of whole values");
    queue_unique = new();
    if (!queue_unique.randomize() || queue_unique.data.size() != 3)
      $fatal(1, "dynamic packed unique failed");
    foreach (queue_unique.data[i]) foreach (queue_unique.data[j])
      if (i != j && queue_unique.data[i] == queue_unique.data[j])
        $fatal(1, "packed queue contains equal values");
    mixed = new();
    if (!mixed.randomize() || mixed.packet.selected != 3 || mixed.packet.fixed != 2 ||
        mixed.packet.label != "state" || mixed.packet.data.size() != 2 ||
        mixed.packet.data[0] == mixed.packet.data[1])
      $fatal(1, "mixed struct randomization changed state members");
    mixed.packet.selected.rand_mode(0);
    mixed.packet.selected = 0;
    if (mixed.randomize()) $fatal(1, "disabled struct member was solved");
    layers = new();
    layers.packet.data = new[2];
    layers.packet.data[0] = 1;
    layers.packet.data[1] = 2;
    layers.packet.data[0].rand_mode(0);
    if (!layers.layered_randomize() || layers.packet.selected != 3 || layers.packet.fixed != 2 ||
        layers.packet.data[0] != 1 || layers.packet.data[0].rand_mode() != 0 ||
        layers.packet.selected.rand_mode() != 1)
      $fatal(1, "struct member layer did not restore modes");
    partial.encoded = 42;
    partial.transient_value = 99;
    payload_bytes.delete();
    PartialPayload_packer::pack(partial, payload_bytes);
    if (payload_bytes.size() != 1 || payload_bytes[0] != 42)
      $fatal(1, "struct pack_bytes policy mismatch");
    partial.transient_value = 7;
    offset = 0;
    PartialPayload_packer::unpack(partial, payload_bytes, offset);
    if (partial.transient_value != 7) $fatal(1, "unpack changed unencoded struct member");
    covered = new();
    auto_cov = new();
    explicit_cov = new();
    covered.packed_value = 9;
    covered.unpacked_value.selected = 3;
    covered.unpacked_value.data = new[2];
    covered.unpacked_value.data[0] = 1;
    covered.unpacked_value.data[1] = 7;
    auto_cov.sample(covered);
    explicit_cov.sample(covered);
    if (explicit_cov.get_coverage() < 15.624 || explicit_cov.get_coverage() > 15.626)
      $fatal(1, "packed/member explicit coverage mismatch");
    if (auto_cov.get_coverage() < 20.311 || auto_cov.get_coverage() > 20.314)
      $fatal(1, "unpacked member auto coverage mismatch");
    covered_items = new();
    item_cov = new();
    sample_value.selected = 3;
    sample_value.data = new[2];
    sample_value.data[0] = 1;
    sample_value.data[1] = 7;
    covered_items.fixed = '{default: sample_value};
    covered_items.dynamic = {sample_value, sample_value};
    covered_items.slotted = {sample_value};
    covered_items.keyed["first"] = sample_value;
    covered_items.keyed["second"] = sample_value;
    item_cov.sample(covered_items);
    if (item_cov.get_coverage() < 20.832 || item_cov.get_coverage() > 20.835)
      $fatal(1, "struct container projected coverage mismatch");
    packed_cycle = new();
    seen = 0;
    repeat (16) begin
      if (!packed_cycle.randomize() || seen[packed_cycle.cycle_value])
        $fatal(1, "packed randc repeated within a cycle");
      seen[packed_cycle.cycle_value] = 1;
    end
    if (seen != '1) $fatal(1, "packed randc did not visit whole-value domain");
    constrained_cycle = new();
    repeat (2) begin
      legal_seen = 0;
      repeat (3) begin
        if (!constrained_cycle.randomize() || constrained_cycle.cycle_value > 2 ||
            legal_seen[constrained_cycle.cycle_value])
          $fatal(1, "packed randc constrained domain mismatch");
        legal_seen[constrained_cycle.cycle_value] = 1;
      end
    end
    struct_layers = new();
    struct_layers.data = new[1];
    struct_layers.data[0].selected = 3;
    struct_layers.data[0].header = 9;
    struct_layers.data[0].selected.rand_mode(0);
    struct_layers.data[0].header.rand_mode(0);
    if (!struct_layers.layered_randomize() || struct_layers.data.size() != 2 ||
        struct_layers.data[0].selected != 3 || struct_layers.data[0].header != 9 ||
        struct_layers.data[0].selected.rand_mode() != 0 ||
        struct_layers.data[0].header.rand_mode() != 0 ||
        struct_layers.data[1].selected.rand_mode() != 1 ||
        struct_layers.data[1].header.rand_mode() != 1 ||
        struct_layers.data[0].label != "state" || struct_layers.data[1].label != "state")
      $fatal(1, "dynamic struct modes or defaults were not preserved");
    child = new();
    child.number = 7;
    child.next_child = child;
    graph_source.left = child;
    graph_source.right = child;
    graph_source.children = {child, null};
    graph_source.text = "tail";
    child.pack(child_bytes);
    HandleRecord_packer::pack(graph_source, graph_bytes);
    if (graph_bytes[child_bytes.size()] != 2)
      $fatal(1, "struct fields do not share one graph pack context");
    offset = 0;
    HandleRecord_packer::unpack(graph_restored, graph_bytes, offset);
    if (offset != graph_bytes.size() || graph_restored.left != graph_restored.right ||
        graph_restored.left.next_child != graph_restored.left ||
        graph_restored.children[0] != graph_restored.left || graph_restored.children[1] != null ||
        graph_restored.text != "tail")
      $fatal(1, "struct shared/cyclic/null graph did not roundtrip");
    enum_host = new();
    enum_domain_supported = enum_host.randomize() && enum_host.header == 1;
    $display("SVTYPES_TARGET_PACKED_ENUM_INTEGRAL_DOMAIN=%0d", enum_domain_supported);
    expr_host = new();
    expr_cov = new();
    expr_host.flag = 1;
    expr_cov.sample(expr_host);
    expr_host.header = 9;
    expr_cov.sample(expr_host);
    if (expr_cov.get_coverage() < 99.999)
      $fatal(1, "packed coverage expression/iff/local cross mismatch");
    mixed.packet.rand_mode(0);
    mixed.packet.selected.rand_mode(1);
    if (mixed.packet.selected.rand_mode() != 1)
      $fatal(1, "struct aggregate mode prevented member reenable");
    mixed.packet.rand_mode(1);
    if (mixed.packet.data[0].rand_mode() != 1)
      $fatal(1, "struct aggregate mode did not enable dynamic members");
    $display("SVTYPES_STRUCT_KINDS_PASS");
    $finish;
  end
endmodule
"""
    (output / "tb.sv").write_text(declarations + "\n" + bench, encoding="utf-8")
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    remote = os.environ["SVTYPES_REMOTE_SV_ROOT"].rstrip("/") + "/.tmp/struct_kinds_conformance"
    runner = shlex.join(shlex.split(os.environ["SVTYPES_REMOTE_SV_RUNNER"]))
    for operation in ("compile tb.sv", "run"):
        command = f"cd {shlex.quote(remote)} && {runner} {operation}"
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
                                 "bash -ilc " + shlex.quote(command)], capture_output=True, text=True)
        log = output / ("target-" + operation.split()[0] + ".log")
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, f"remote target failed; private diagnostic: {log}"
    assert "SVTYPES_STRUCT_KINDS_PASS" in result.stdout + result.stderr, f"success marker missing; private diagnostic: {log}"
    observations = [line.rsplit("=", 1)[1] for line in result.stdout.splitlines()
                    if line.startswith("SVTYPES_TARGET_PACKED_ENUM_INTEGRAL_DOMAIN=")]
    assert observations in (["0"], ["1"]), f"capability observation missing; private diagnostic: {log}"
    (output / "target-capabilities.json").write_text(json.dumps({
        "packed_enum_integral_random_domain": observations == ["1"]
    }) + "\n", encoding="utf-8")
