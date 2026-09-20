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
from typing import Any

import pytest

from svtypes import Bit, CovPoint, CovPointArray, CoverGroupOption, CoverInput, CoverRef, Cross, CrossOption, DynArray, Enum, Logic, LogicValue, Parameter, SvObject, bins, coverage_init, covergroup, default_bins, ignore_bins, illegal_bins, repeat, transition_bins
from svtypes.coverage.observation import compare_manifest_hits, parse_observation
from svtypes.coverage.sv import observation_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_COMMAND = os.environ.get("SVTYPES_REMOTE_SV_RUNNER", "svtypes_remote_sv_runner")


class CoveragePacket(SvObject):
    opcode = Bit(1, cov=False)
    mode = Bit(1, cov=False)
    valid = Bit(1, cov=False)
    code = Bit(2, cov=False)
    data = DynArray(Bit(2), cov=False)

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

        class code_cp(CovPoint, source=self.code, iff=lambda: self.valid == 1):
            zero = bins[0]
            reserved = illegal_bins[3]
            other = default_bins

        class code_trans(CovPoint, source=self.code):
            rise = transition_bins[0, 1]

        class slots(CovPointArray, source=self.data, length=4):
            low = bins[0:1]

        class values(CovPoint, source=self.data):
            all_values = bins[0:3]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _assign_sample(packet: CoveragePacket, sample: tuple[int, int, int, int, tuple[int, int]]) -> None:
    opcode, mode, valid, code, data = sample
    packet.opcode.value, packet.mode.value, packet.valid.value, packet.code.value = opcode, mode, valid, code
    packet.data.value = list(data)


def _samples(count: int) -> tuple[tuple[int, int, int, int, tuple[int, int]], ...]:
    # The configured target aborts a run when an illegal bin is hit, so the
    # codec-sync vector keeps that bin declared and compares zero illegal
    # counts. Non-zero illegal hits are covered by Python unit tests.
    samples = []
    for index in range(count):
        opcode = (index >> 1) & 1
        mode = index & 1
        valid = int(index % 5 != 0)
        code = index % 4
        if valid and code == 3:
            valid = 0
        samples.append((opcode, mode, valid, code, (index % 4, (index >> 2) & 3)))
    return tuple(samples)


def _write_fixture(
    out: Path,
    samples: tuple[tuple[int, int, int, int, tuple[int, int]], ...] = (
        (0, 0, 1, 0, (0, 1)),
        (1, 1, 1, 1, (1, 0)),
        (1, 0, 0, 3, (2, 3)),
    ),
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage_packet.sv").write_text(CoveragePacket.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(observation_manifest([CoveragePacket]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payloads: list[bytes] = []
    for sample in samples:
        packet = CoveragePacket()
        _assign_sample(packet, sample)
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
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}", cov.get_coverage(), SAMPLE_COUNT);
    $fclose(fd);
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
    assert "cov.get_coverage()" in testbench
    source = (tmp_path / "coverage_packet.sv").read_text(encoding="utf-8")
    assert "iff ((item.data.size() > 2))" in source
    assert "bins rise = (0 => 1);" in source
    assert "illegal_bins reserved = {3};" in source
    assert "bins other = default;" in source
    assert "acc += cg.get_coverage() * " in source
    assert "acc += cg_values.get_coverage() * " in source
    assert json.loads((tmp_path / "observation-manifest.json").read_text(encoding="utf-8"))["covergroups"]


@pytest.mark.remote_sv
def test_remote_generated_cross_covergroup() -> None:
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    remote_root = os.environ["SVTYPES_REMOTE_SV_ROOT"]
    out = REPO_ROOT / ".tmp" / "coverage_target"
    if out.exists():
        shutil.rmtree(out)
    samples = _samples(10_000)
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
    for sample in samples:
        _assign_sample(packet, sample)
        packet.cg.sample()
    manifest = observation_manifest([CoveragePacket])
    names = {
        item["python_name"]: item["observation_label"]
        for group in manifest["covergroups"]
        for collection in (group["points"], group["crosses"])
        for item in collection
    }
    expected = {
        "items": {
            names[name]: {"hits": counters["hits"], "illegal_hits": counters["illegal_hits"]}
            for name, counters in packet.cg.instance.snapshot().items()
        },
        "summary": {"coverage": packet.cg.get_inst_coverage(), "sample_count": packet.cg.sample_count},
    }
    compare_manifest_hits(
        manifest,
        parse_observation(json.loads(observed.stdout)),
        expected,
    )


def _ssh(host: str, directory: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", host, f"bash -ilc 'cd {directory} && {command}'"],
        capture_output=True,
        text=True,
    )


def _remote_compile_run(out: Path, sources: str) -> dict[str, Any]:
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    remote_root = os.environ["SVTYPES_REMOTE_SV_ROOT"]
    remote_directory = f"{remote_root}/{out.relative_to(REPO_ROOT).as_posix()}"
    compiled = _ssh(host, remote_directory, f"{RUNNER_COMMAND} compile {sources}")
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = _ssh(host, remote_directory, f"{RUNNER_COMMAND} run")
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert "SVTYPES_COVERAGE_TARGET_PASS" in log, log
    observed = _ssh(host, remote_directory, "cat coverage-observation.json")
    assert observed.returncode == 0, observed.stdout + observed.stderr
    return {"host": host, "remote_directory": remote_directory, "observation": json.loads(observed.stdout)}


def _labels(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        item["python_name"]: item["observation_label"]
        for group in manifest["covergroups"]
        for collection in (group["points"], group["crosses"])
        for item in collection
    }


def _expected_from_instance(instance: Any, manifest: dict[str, Any], sample_count: int, coverage: float) -> dict[str, Any]:
    names = _labels(manifest)
    record = next((
        item for item in manifest.get("instances", [])
        if item["logical_instance_key"] == instance.logical_instance_key
    ), None)
    return {
        "items": {
            names[name]: {
                kind: dict(counters[kind])
                for kind in ("hits", "illegal_hits")
            }
            for name, counters in instance.snapshot().items()
        },
        "summary": {"coverage": coverage, "sample_count": sample_count},
    }


def _write_codec_sync_fixture(
    out: Path,
    packet_type: type,
    payloads: list[bytes],
    *,
    source: str | None = None,
    collector_new: str = "new()",
    covergroup: str = "cg",
) -> None:
    type_name = packet_type.__name__
    collector = f"{type_name}::{type_name}__{covergroup}__coverage"
    (out / "coverage_packet.sv").write_text(source if source is not None else packet_type.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(observation_manifest([packet_type]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  __TYPE__ packet;
  __COLLECTOR__ cov;
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
    cov = __COLLECTOR_NEW__;
    fd = $fopen("samples.hex", "r");
    if (fd == 0) $fatal(2, "cannot open samples.hex");
    for (index = 0; index < SAMPLE_COUNT; index++) begin
      if (!load_hex(fd, bytes)) $fatal(2, "sample input truncated");
      offset = 0; packet.unpack(bytes, offset); if (offset != bytes.size()) $fatal(2, "sample offset"); cov.sample(packet);
    end
    $fclose(fd);
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}", cov.get_coverage(), SAMPLE_COUNT);
    $fclose(fd);
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__TYPE__", type_name)
        .replace("__COLLECTOR__", collector)
        .replace("__COLLECTOR_NEW__", collector_new)
        .replace("__SAMPLE_COUNT__", str(len(payloads)))
        .replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )


class TransitionPacket(SvObject):
    enable = Bit(1, cov=False)
    code = Bit(2, cov=False)
    signal = Logic(2, cov=False)

    @covergroup
    def cg(self):
        class gated(CovPoint, source=self.code, iff=lambda: self.enable == 1):
            rise = transition_bins[0, 1]
            skipped = ignore_bins[2]
            reserved = illegal_bins[3]
            other = default_bins

        class xz_trans(CovPoint, source=self.signal):
            rise = transition_bins[0, 1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


class CrossLocalPacket(SvObject):
    opcode = Bit(2, cov=False)
    mode = Bit(1, cov=False)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            public = bins[0:3]

        class mode_cp(CovPoint, source=self.mode):
            read = bins[0]

        class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
            class opcode_cp(CovPoint, source=self.opcode):
                compact = bins[0:1]

            selected = bins[opcode_cp.compact, mode_cp.read]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


@pytest.mark.remote_sv
def test_remote_cross_local_covpoint_is_private_and_matches_python():
    out = REPO_ROOT / ".tmp" / "coverage_cross_local"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    samples = ((0, 0), (1, 0), (2, 0))
    payloads: list[bytes] = []
    for opcode, mode in samples:
        packet = CrossLocalPacket()
        packet.opcode.value, packet.mode.value = opcode, mode
        payloads.append(packet.pack(packet))
    _write_codec_sync_fixture(out, CrossLocalPacket, payloads)
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = CrossLocalPacket()
    for opcode, mode in samples:
        packet.opcode.value, packet.mode.value = opcode, mode
        packet.cg.sample()
    manifest = observation_manifest([CrossLocalPacket])
    group = manifest["covergroups"][0]
    assert [item["python_name"] for item in group["points"]] == ["mode_cp", "opcode_cp"]
    _compare_single_instance_remote(packet, remote["observation"])


def _assign_transition(packet: TransitionPacket, sample: tuple[int, int, str]) -> None:
    enable, code, signal = sample
    packet.enable.value, packet.code.value = enable, code
    packet.signal.value = LogicValue.from_string(signal)


def _transition_samples() -> tuple[tuple[int, int, str], ...]:
    pattern = (
        (1, 0, "00"),
        (0, 1, "1x"),
        (1, 1, "01"),
        (1, 2, "00"),
        (1, 0, "00"),
        (1, 1, "01"),
    )
    return tuple(pattern[index % len(pattern)] for index in range(10_000))


def test_transition_conformance_fixture_emits_iff_ignore_illegal_default_and_xz(tmp_path: Path) -> None:
    source = TransitionPacket.to_sv_obj()
    assert "iff ((item.enable == 1))" in source
    assert "bins rise = (0 => 1);" in source
    assert "ignore_bins skipped = {2};" in source
    assert "illegal_bins reserved = {3};" in source
    assert "bins other = default;" in source


class InstancePacket(SvObject):
    sel = Bit(1, cov=False)
    opcode = Bit(1, cov=False)

    @covergroup
    def cg(self):
        class option(CoverGroupOption):
            per_instance = 1

        class opcode_cp(CovPoint, source=self.opcode):
            zero = bins[0]
            one = bins[1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def test_per_instance_fixture_emits_option_and_two_collector_instances(tmp_path: Path) -> None:
    source = InstancePacket.to_sv_obj()
    assert "option.per_instance = 1;" in source
    left, right = InstancePacket(), InstancePacket()
    left.cg.bind_logical_instance("dut.left")
    right.cg.bind_logical_instance("dut.right")
    manifest = observation_manifest(
        [InstancePacket],
        instances=(left.cg.instance, right.cg.instance),
        target_labels={"dut.left": "dut_left", "dut.right": "dut_right"},
    )
    assert {item["logical_instance_key"] for item in manifest["instances"]} == {"dut.left", "dut.right"}


@pytest.mark.remote_sv
def test_remote_transition_history_iff_ignore_default_and_xz() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_transition"
    if out.exists():
        shutil.rmtree(out)
    samples = _transition_samples()
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage_packet.sv").write_text(TransitionPacket.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(observation_manifest([TransitionPacket]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payloads = []
    for sample in samples:
        packet = TransitionPacket()
        _assign_transition(packet, sample)
        payloads.append(packet.pack(packet))
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  TransitionPacket packet;
  TransitionPacket::TransitionPacket__cg__coverage cov;
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
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}", cov.get_coverage(), SAMPLE_COUNT);
    $fclose(fd);
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__SAMPLE_COUNT__", str(len(samples))).replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = TransitionPacket()
    for sample in samples:
        _assign_transition(packet, sample)
        packet.cg.sample()
    manifest = observation_manifest([TransitionPacket])
    compare_manifest_hits(
        manifest,
        parse_observation(remote["observation"]),
        _expected_from_instance(packet.cg.instance, manifest, packet.cg.sample_count, packet.cg.get_inst_coverage()),
    )


@pytest.mark.remote_sv
def test_remote_per_instance_covergroups_compare_instance_coverage() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_per_instance"
    if out.exists():
        shutil.rmtree(out)
    samples = tuple((0, 0) for _ in range(5_000)) + tuple((1, 1) for _ in range(5_000))
    out.mkdir(parents=True, exist_ok=True)
    left, right = InstancePacket(), InstancePacket()
    left.cg.bind_logical_instance("dut.left")
    right.cg.bind_logical_instance("dut.right")
    left.cg.set_inst_name("dut_left")
    right.cg.set_inst_name("dut_right")
    (out / "coverage_packet.sv").write_text(InstancePacket.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(
            observation_manifest(
                [InstancePacket],
                instances=(left.cg.instance, right.cg.instance),
                target_labels={"dut.left": "dut_left", "dut.right": "dut_right"},
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    payloads = []
    for sel, opcode in samples:
        packet = InstancePacket()
        packet.sel.value, packet.opcode.value = sel, opcode
        payloads.append(packet.pack(packet))
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  InstancePacket packet;
  InstancePacket::InstancePacket__cg__coverage cov_left;
  InstancePacket::InstancePacket__cg__coverage cov_right;
  localparam int SAMPLE_COUNT = __SAMPLE_COUNT__;
  localparam int SAMPLE_BYTES = __SAMPLE_BYTES__;
  integer left_count;
  integer right_count;
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
    cov_left = new();
    cov_right = new();
    cov_left.cg.option.name = "dut_left";
    cov_right.cg.option.name = "dut_right";
    left_count = 0;
    right_count = 0;
    fd = $fopen("samples.hex", "r");
    if (fd == 0) $fatal(2, "cannot open samples.hex");
    for (index = 0; index < SAMPLE_COUNT; index++) begin
      if (!load_hex(fd, bytes)) $fatal(2, "sample input truncated");
      offset = 0; packet.unpack(bytes, offset); if (offset != bytes.size()) $fatal(2, "sample offset");
      if (packet.sel == 0) begin cov_left.sample(packet); left_count++; end
      else begin cov_right.sample(packet); right_count++; end
    end
    $fclose(fd);
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"instances\\\":{\\\"dut.left\\\":{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d},\\\"dut.right\\\":{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}}}", cov_left.cg.get_inst_coverage(), left_count, cov_right.cg.get_inst_coverage(), right_count);
    $fclose(fd);
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__SAMPLE_COUNT__", str(len(samples))).replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    for sel, opcode in samples:
        packet = left if sel == 0 else right
        packet.sel.value, packet.opcode.value = sel, opcode
        packet.cg.sample()
    manifest = observation_manifest(
        [InstancePacket],
        instances=(left.cg.instance, right.cg.instance),
        target_labels={"dut.left": "dut_left", "dut.right": "dut_right"},
    )
    compare_manifest_hits(
        manifest,
        parse_observation(remote["observation"]),
        {
            "instances": {
                "dut.left": _expected_from_instance(left.cg.instance, manifest, left.cg.sample_count, left.cg.get_inst_coverage()),
                "dut.right": _expected_from_instance(right.cg.instance, manifest, right.cg.sample_count, right.cg.get_inst_coverage()),
            }
        },
    )


class UcisPacket(SvObject):
    code = Bit(1, cov=False)

    @covergroup
    def cg(self):
        class code_cp(CovPoint, source=self.code):
            low = bins[0]
            high = bins[1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


@pytest.mark.remote_sv
def test_remote_target_ucis_export_imports_into_coverage_database() -> None:
    from svtypes import CoverageDatabase

    out = REPO_ROOT / ".tmp" / "coverage_ucis"
    if out.exists():
        shutil.rmtree(out)
    samples = tuple((index & 1,) for index in range(256))
    out.mkdir(parents=True, exist_ok=True)
    packet = UcisPacket()
    packet.cg.bind_logical_instance("dut.pkt")
    type_id = UcisPacket.cg.freeze().covergroup_type_id
    (out / "coverage_packet.sv").write_text(UcisPacket.to_sv_obj(), encoding="utf-8")
    (out / "observation-manifest.json").write_text(
        json.dumps(observation_manifest([UcisPacket]), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out / "ucis-bindings.json").write_text(
        json.dumps({"logical_instance_key": "dut.pkt", "covergroup_type_id": type_id}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payloads = []
    for (code,) in samples:
        item = UcisPacket()
        item.code.value = code
        payloads.append(item.pack(item))
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  UcisPacket packet;
  UcisPacket::UcisPacket__cg__coverage cov;
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
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}", cov.get_coverage(), SAMPLE_COUNT);
    $fclose(fd);
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__SAMPLE_COUNT__", str(len(samples))).replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    exported = _ssh(remote["host"], remote["remote_directory"], f"{RUNNER_COMMAND} export-ucis && cat coverage.ucis.xml")
    assert exported.returncode == 0, exported.stdout + exported.stderr
    source = _ssh(remote["host"], remote["remote_directory"], "cat coverage.ucis.source")
    xml = exported.stdout[exported.stdout.find("<?xml"):] if "<?xml" in exported.stdout else exported.stdout
    for (code,) in samples:
        packet.code.value = code
        packet.cg.sample()
    restored = CoverageDatabase()
    restored.import_ucis(xml, bindings={"dut.pkt": UcisPacket().cg.instance})
    record = restored.snapshot_document()["records"][0]
    assert record["logical_instance_key"] == "dut.pkt"
    assert record["points"]["code_cp"]["hits"]["low"] == 128
    assert record["points"]["code_cp"]["hits"]["high"] == 128
    assert source.stdout.strip() in {"native", "adapter-projection"}


def _compare_single_instance_remote(packet: Any, observation: dict[str, Any]) -> None:
    manifest = observation_manifest([type(packet)])
    compare_manifest_hits(
        manifest,
        parse_observation(observation),
        _expected_from_instance(
            packet.cg.instance,
            manifest,
            packet.cg.sample_count,
            packet.cg.get_inst_coverage(),
        ),
    )


class CrossIffPacket(SvObject):
    code = Bit(2, cov=False)
    mode = Bit(1, cov=False)
    enabled = Bit(1, cov=False)
    gate = Bit(1, cov=False)

    @covergroup
    def cg(self):
        class code_cp(CovPoint, source=self.code, iff=lambda: self.enabled == 1):
            zero = bins[0]
            low = bins[0:1]

        class mode_cp(CovPoint, source=self.mode):
            zero = bins[0]
            one = bins[1]

        class combined(Cross, members=(code_cp, mode_cp), iff=lambda: self.gate == 1):
            exact = bins[code_cp.zero, mode_cp.zero]
            overlapping = bins[code_cp.low, mode_cp.zero]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _assign_cross_iff(packet: CrossIffPacket, sample: tuple[int, int, int, int]) -> None:
    packet.code.value, packet.mode.value, packet.enabled.value, packet.gate.value = sample


def _cross_iff_samples() -> tuple[tuple[int, int, int, int], ...]:
    pattern = (
        (0, 0, 1, 1),
        (1, 0, 1, 1),
        (0, 0, 0, 1),
        (0, 0, 1, 0),
        (0, 1, 1, 1),
    )
    return tuple(pattern[index % len(pattern)] for index in range(10_000))


def test_cross_iff_fixture_emits_member_and_cross_guards() -> None:
    source = CrossIffPacket.to_sv_obj()
    assert "code_cp: coverpoint item.code iff ((item.enabled == 1)) {" in source
    assert "combined: cross code_cp, mode_cp iff ((item.gate == 1)) {" in source
    assert "bins exact = binsof(code_cp.zero) && binsof(mode_cp.zero);" in source
    assert "bins overlapping = binsof(code_cp.low) && binsof(mode_cp.zero);" in source


@pytest.mark.remote_sv
def test_remote_cross_iff_member_skip_and_overlapping_normal_bins() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_cross_iff"
    if out.exists():
        shutil.rmtree(out)
    samples = _cross_iff_samples()
    out.mkdir(parents=True, exist_ok=True)
    payloads = []
    for sample in samples:
        packet = CrossIffPacket()
        _assign_cross_iff(packet, sample)
        payloads.append(packet.pack(packet))
    _write_codec_sync_fixture(out, CrossIffPacket, payloads)
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = CrossIffPacket()
    for sample in samples:
        _assign_cross_iff(packet, sample)
        packet.cg.sample()
    _compare_single_instance_remote(packet, remote["observation"])

class CoverageOverlapColor(Enum, width=8, signed=False):
    R = 0
    G = 1


class OverlapPacket(SvObject):
    color = CoverageOverlapColor(cov=False)
    code = Bit(2, cov=False)

    @covergroup
    def cg(self):
        class color_cp(CovPoint, source=self.color):
            red = bins[CoverageOverlapColor.R]
            zero = bins[0]

        class code_cp(CovPoint, source=self.code):
            span = bins[0:2]
            pair = bins[0, 2]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _assign_overlap(packet: OverlapPacket, sample: tuple[int, int]) -> None:
    packet.color.value, packet.code.value = sample


def _overlap_samples() -> tuple[tuple[int, int], ...]:
    pattern = (
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 3),
    )
    return tuple(pattern[index % len(pattern)] for index in range(10_000))


def test_overlap_fixture_emits_enum_and_range_set_bins() -> None:
    source = OverlapPacket.to_sv_obj()
    assert "color_cp: coverpoint item.color {" in source
    assert "bins red = {R};" in source
    assert "bins zero = {0};" in source
    assert "bins span = {[0:2]};" in source
    assert "bins pair = {0, 2};" in source


@pytest.mark.remote_sv
def test_remote_enum_and_range_set_overlapping_normal_bins() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_overlap"
    if out.exists():
        shutil.rmtree(out)
    samples = _overlap_samples()
    out.mkdir(parents=True, exist_ok=True)
    payloads = []
    for sample in samples:
        packet = OverlapPacket()
        _assign_overlap(packet, sample)
        payloads.append(packet.pack(packet))
    _write_codec_sync_fixture(
        out,
        OverlapPacket,
        payloads,
        source=CoverageOverlapColor.to_sv_enum() + "\n\n" + OverlapPacket.to_sv_obj(),
    )
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = OverlapPacket()
    for sample in samples:
        _assign_overlap(packet, sample)
        packet.cg.sample()
    _compare_single_instance_remote(packet, remote["observation"])


class FormalsPacket(SvObject):
    opcode = Bit(2, cov=False)
    mode = Bit(1, cov=False)

    @covergroup
    def cg(self, limit: CoverInput[int], mode: CoverRef[Bit]):
        class limited(CovPoint, source=self.opcode):
            window = bins[0:limit]

        class ref_cp(CovPoint, source=mode):
            zero = bins[0]
            one = bins[1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate(2)


def _assign_formals(packet: FormalsPacket, sample: tuple[int, int]) -> None:
    packet.opcode.value, packet.mode.value = sample


def _formals_samples() -> tuple[tuple[int, int], ...]:
    pattern = (
        (0, 0),
        (2, 1),
        (3, 0),
        (1, 1),
    )
    return tuple(pattern[index % len(pattern)] for index in range(10_000))


def test_formals_fixture_emits_cover_input_constructor_and_cover_ref_path() -> None:
    source = FormalsPacket.to_sv_obj()
    assert "covergroup cg (int limit) with function sample(FormalsPacket item);" in source
    assert "bins window = {[0:limit]};" in source
    assert "ref_cp: coverpoint item.mode {" in source
    assert "cg = new(limit);" in source


@pytest.mark.remote_sv
def test_remote_cover_input_and_cover_ref_sample_bindings() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_formals"
    if out.exists():
        shutil.rmtree(out)
    samples = _formals_samples()
    out.mkdir(parents=True, exist_ok=True)
    payloads = []
    for sample in samples:
        packet = FormalsPacket()
        _assign_formals(packet, sample)
        payloads.append(packet.pack(packet))
    _write_codec_sync_fixture(out, FormalsPacket, payloads, collector_new="new(2)")
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = FormalsPacket()
    for sample in samples:
        _assign_formals(packet, sample)
        packet.cg.sample()
    _compare_single_instance_remote(packet, remote["observation"])


class ParameterCoveragePacket(SvObject):
    """Parameter-driven bin selector used for Python/SV coverage parity."""

    opcode = Bit(4, cov=False)
    reserved_opcode = Parameter()(15)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            reserved = bins[self.reserved_opcode]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


@pytest.mark.remote_sv
def test_remote_parameter_coverage_bin_preserves_symbol_and_hits() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_parameter"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    source = ParameterCoveragePacket.to_sv_obj()
    assert "bins reserved = {reserved_opcode};" in source
    packet = ParameterCoveragePacket()
    assert packet.cg.instance.instance_ir.points[0].bins[0].selector == {
        "kind": "constant",
        "value": 15,
    }
    payloads = []
    for value in (14, 15, 15):
        item = ParameterCoveragePacket()
        item.opcode.value = value
        payloads.append(item.pack(item))
        packet.opcode.value = value
        packet.cg.sample()
    _write_codec_sync_fixture(out, ParameterCoveragePacket, payloads)
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    _compare_single_instance_remote(packet, remote["observation"])


class InputLayoutPacket(SvObject):
    """One declaration whose constructor input selects an instance bin range."""

    opcode = Bit(2, cov=False)

    @covergroup
    def cg(self, first: CoverInput[int], last: CoverInput[int]):
        class option(CoverGroupOption):
            per_instance = 1

        class opcode_cp(CovPoint, source=self.opcode):
            window = bins[first:last].split(max_bins=None)

    def __init__(self, first: int, last: int):
        super().__init__()
        self.configure_coverage(first, last)

    @coverage_init
    def configure_coverage(self, first: int, last: int):
        self.cg.instantiate(first, last)


@pytest.mark.remote_sv
def test_remote_cover_input_instances_have_disjoint_bin_layouts() -> None:
    """Compare two host-initialized dynamic array-bin layouts to Python."""
    out = REPO_ROOT / ".tmp" / "coverage_input_layouts"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    low = InputLayoutPacket(0, 1)
    high = InputLayoutPacket(2, 3)
    low.cg.bind_logical_instance("dut.low")
    high.cg.bind_logical_instance("dut.high")
    low.cg.set_inst_name("dut_low")
    high.cg.set_inst_name("dut_high")
    samples = (0, 1, 2, 3)
    payloads = []
    for value in samples:
        packet = InputLayoutPacket(0, 1)
        packet.opcode.value = value
        payloads.append(packet.pack(packet))

    (out / "coverage_packet.sv").write_text(InputLayoutPacket.to_sv_obj(), encoding="utf-8")
    manifest = observation_manifest(
        [InputLayoutPacket],
        instances=(low.cg.instance, high.cg.instance),
        target_labels={"dut.low": "dut_low", "dut.high": "dut_high"},
    )
    (out / "observation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out / "samples.hex").write_text(
        "".join(f"{byte:02x}\n" for payload in payloads for byte in payload), encoding="ascii"
    )
    (out / "tb.sv").write_text(
        """
module tb;
  InputLayoutPacket packet;
  InputLayoutPacket low_host;
  InputLayoutPacket high_host;
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
    low_host = new();
    high_host = new();
    low_host.configure_coverage(0, 1);
    high_host.configure_coverage(2, 3);
    low_host.cg.cg.option.name = "dut_low";
    high_host.cg.cg.option.name = "dut_high";
    fd = $fopen("samples.hex", "r");
    if (fd == 0) $fatal(2, "cannot open samples.hex");
    for (index = 0; index < SAMPLE_COUNT; index++) begin
      if (!load_hex(fd, bytes)) $fatal(2, "sample input truncated");
      offset = 0; packet.unpack(bytes, offset); if (offset != bytes.size()) $fatal(2, "sample offset");
      low_host.cg.sample(packet);
      high_host.cg.sample(packet);
    end
    $fclose(fd);
    fd = $fopen("coverage-summary.json", "w");
    if (fd == 0) $fatal(2, "cannot write coverage summary");
    $fwrite(fd, "{\\\"instances\\\":{\\\"dut.low\\\":{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d},\\\"dut.high\\\":{\\\"coverage\\\":%.6f,\\\"sample_count\\\":%0d}}}", low_host.cg.cg.get_inst_coverage(), SAMPLE_COUNT, high_host.cg.cg.get_inst_coverage(), SAMPLE_COUNT);
    $fclose(fd);
    $display("SVTYPES_COVERAGE_TARGET_PASS");
    $finish;
  end
endmodule
""".replace("__SAMPLE_COUNT__", str(len(samples))).replace("__SAMPLE_BYTES__", str(len(payloads[0]))),
        encoding="utf-8",
    )

    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    for value in samples:
        low.opcode.value = high.opcode.value = value
        low.cg.sample()
        high.cg.sample()
    compare_manifest_hits(
        manifest,
        parse_observation(remote["observation"]),
        {
            "instances": {
                "dut.low": _expected_from_instance(low.cg.instance, manifest, len(samples), low.cg.get_inst_coverage()),
                "dut.high": _expected_from_instance(high.cg.instance, manifest, len(samples), high.cg.get_inst_coverage()),
            }
        },
    )


class RepeatPacket(SvObject):
    opcode = Bit(2, cov=False)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            burst = transition_bins[0, repeat(1, 1, 2), 2]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _repeat_samples() -> tuple[int, ...]:
    pattern = (0, 1, 1, 2, 0, 1, 1, 1, 2)
    return tuple(pattern[index % len(pattern)] for index in range(10_000))


def test_repeat_fixture_emits_bounded_repetition_syntax() -> None:
    source = RepeatPacket.to_sv_obj()
    assert "bins burst = (0 => 1[*1:2] => 2);" in source


@pytest.mark.remote_sv
def test_remote_transition_repeat_matches_finite_repetition_lengths() -> None:
    out = REPO_ROOT / ".tmp" / "coverage_repeat"
    if out.exists():
        shutil.rmtree(out)
    samples = _repeat_samples()
    out.mkdir(parents=True, exist_ok=True)
    payloads = []
    for value in samples:
        packet = RepeatPacket()
        packet.opcode.value = value
        payloads.append(packet.pack(packet))
    _write_codec_sync_fixture(out, RepeatPacket, payloads)
    remote = _remote_compile_run(out, "coverage_packet.sv tb.sv")
    packet = RepeatPacket()
    for value in samples:
        packet.opcode.value = value
        packet.cg.sample()
    _compare_single_instance_remote(packet, remote["observation"])
