"""Remote-target conformance test for CoverageIR-generated covergroups.

The configured external adapter owns target invocation and coverage-database
inspection. It receives the generated source directory and produces the
simulator-neutral observation JSON described by the generated manifest.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from svtypes import Bit, CovPoint, Cross, CrossOption, SvObject, bins, covergroup, ignore_bins
from svtypes.coverage.observation import compare_manifest_hits, parse_observation
from svtypes.coverage.sv import observation_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_COMMAND = os.environ.get("SVTYPES_REMOTE_SV_RUNNER", "svtypes_remote_sv_runner")


class CoveragePacket(SvObject):
    opcode = Bit(1, cov=False)
    mode = Bit(1, cov=False)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            zero = bins[0]
            one = bins[1]

        class mode_cp(CovPoint, source=self.mode):
            read = bins[0]
            write = bins[1]

        class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
            class option(CrossOption):
                cross_retain_auto_bins = 0

            zero_read = bins[opcode_cp.zero, mode_cp.read]
            one_write = bins[opcode_cp.one, mode_cp.write]
            masked = ignore_bins[opcode_cp.one, mode_cp.read]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _write_fixture(out: Path, samples: tuple[tuple[int, int], ...] = ((0, 0), (1, 1), (1, 0))) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage_packet.sv").write_text(CoveragePacket.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(observation_manifest([CoveragePacket]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payloads: list[bytes] = []
    for opcode, mode in samples:
        packet = CoveragePacket()
        packet.opcode.value, packet.mode.value = opcode, mode
        payloads.append(packet.pack(packet))
    if len({len(payload) for payload in payloads}) != 1:
        raise AssertionError("coverage fixture samples must have one encoded size")
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  CoveragePacket packet;
  CoveragePacket::CoveragePacket__cg__coverage cov;
  localparam int SAMPLE_COUNT = __SAMPLE_COUNT__;
  localparam int SAMPLE_BYTES = __SAMPLE_BYTES__;
  function automatic bit load_hex(integer fd, ref byte unsigned bytes[$]);
    int unsigned value;
    int index;
    bytes.delete();
    for (index = 0; index < SAMPLE_BYTES; index++) begin
      if ($fscanf(fd, "%h", value) != 1) return 0;
      bytes.push_back(value[7:0]);
    end
    return 1;
  endfunction
  initial begin
    byte unsigned bytes[$];
    int offset;
    integer fd;
    int index;
    packet = new();
    cov = new();
    fd = $fopen("samples.hex", "r");
    if (fd == 0) $fatal(2, "cannot open samples.hex");
    for (index = 0; index < SAMPLE_COUNT; index++) begin
      if (!load_hex(fd, bytes)) $fatal(2, "sample input truncated");
      offset = 0; packet.unpack(bytes, offset); if (offset != bytes.size()) $fatal(2, "sample offset"); cov.sample(packet);
    end
    $fclose(fd);
    if (cov.cg.get_coverage() != 100.0) $fatal(2, "unexpected coverage: %f", cov.cg.get_coverage());
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__SAMPLE_COUNT__", str(len(samples))).replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )


def test_cross_conformance_fixture_uses_codec_synchronized_vectors(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    assert (tmp_path / "samples.hex").read_text(encoding="ascii")
    testbench = (tmp_path / "tb.sv").read_text(encoding="utf-8")
    assert "packet.unpack(bytes, offset)" in testbench
    assert "cov.sample(packet)" in testbench
    assert json.loads((tmp_path / "observation-manifest.json").read_text(encoding="utf-8"))["covergroups"]


@pytest.mark.remote_sv
def test_remote_generated_cross_covergroup() -> None:
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    remote_root = os.environ["SVTYPES_REMOTE_SV_ROOT"]
    out = REPO_ROOT / ".tmp" / "coverage_target"
    if out.exists():
        shutil.rmtree(out)
    samples = tuple(((index >> 1) & 1, index & 1) for index in range(10_000))
    _write_fixture(out, samples)
    remote_directory = f"{remote_root}/.tmp/coverage_target"
    compiled = subprocess.run(
        ["ssh", host, f"bash -ilc 'cd {remote_directory} && {RUNNER_COMMAND} compile coverage_packet.sv tb.sv'"],
        capture_output=True,
        text=True,
    )
    compile_log = compiled.stdout + compiled.stderr
    assert compiled.returncode == 0, compile_log
    result = subprocess.run(
        ["ssh", host, f"bash -ilc 'cd {remote_directory} && {RUNNER_COMMAND} run'"],
        capture_output=True,
        text=True,
    )
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert "SVTYPES_COVERAGE_TARGET_PASS" in log, log
    observed = subprocess.run(
        ["ssh", host, f"bash -ilc 'cd {remote_directory} && cat coverage-observation.json'"],
        capture_output=True,
        text=True,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    packet = CoveragePacket()
    for opcode, mode in samples:
        packet.opcode.value, packet.mode.value = opcode, mode
        packet.cg.sample()
    manifest = observation_manifest([CoveragePacket])
    names = {
        item["python_name"]: item["observation_label"]
        for group in manifest["covergroups"]
        for collection in (group["points"], group["crosses"])
        for item in collection
    }
    expected = {
        names[name]: {"hits": counters["hits"], "illegal_hits": counters["illegal_hits"]}
        for name, counters in packet.cg.instance.snapshot().items()
    }
    compare_manifest_hits(
        manifest,
        parse_observation(json.loads(observed.stdout)),
        expected,
    )
