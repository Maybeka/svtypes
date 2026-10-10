#!/usr/bin/env python3
"""Reproducible coverage throughput and Python-allocation measurements.

Run with PYTHONPATH=python:. .venv/bin/python benchmarks/coverage.py --json.
Measurements are descriptive, not hardware-independent pass/fail limits.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import platform
from pathlib import Path
import sys
import tracemalloc
from time import perf_counter

from svtypes import Bit, CovPoint, CoverageDatabase, Cross, SvObject, bins, covergroup
from svtypes.coverage import export_ucis


class ScalarPacket(SvObject):
    opcode = Bit[8]()

    def __init__(self):
        super().__init__()
        self.cg.instantiate()

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            values = bins[0:255].split(256)


class LargeCrossPacket(SvObject):
    opcode = Bit[8]()
    mode = Bit[8]()

    def __init__(self):
        super().__init__()
        self.cg.instantiate()

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            values = bins[0:255].split(256)

        class mode_cp(CovPoint, source=self.mode):
            values = bins[0:255].split(256)

        class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
            pass


def measure(name, operation, operations=1, *, trace_memory=True, profile_dir=None):
    profiler = cProfile.Profile() if profile_dir else None
    if trace_memory:
        tracemalloc.start()
    started = perf_counter()
    try:
        if profiler:
            profiler.enable()
        value = operation()
        if profiler:
            profiler.disable()
        elapsed = perf_counter() - started
        current, peak = tracemalloc.get_traced_memory() if trace_memory else (None, None)
    finally:
        if trace_memory:
            tracemalloc.stop()
        if profiler:
            profiler.disable()
            directory = Path(profile_dir)
            directory.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(directory / f"{name}.prof"))
    return value, {
        "scenario": name,
        "operations": operations,
        "elapsed_seconds": elapsed,
        "operations_per_second": operations / elapsed,
        "retained_python_bytes": current,
        "peak_python_bytes": peak,
    }


def run(samples=10_000, runs=100, cross_samples=10, *, trace_memory=True, profile_dir=None):
    def meter(name, operation, operations=1):
        return measure(name, operation, operations, trace_memory=trace_memory, profile_dir=profile_dir)

    results = []
    packet = ScalarPacket()

    def sample_scalar():
        for index in range(samples):
            packet.opcode.value = index % 256
            packet.cg.sample()

    _, result = meter("scalar_sample_256_bins", sample_scalar, samples)
    results.append(result)
    assert packet.cg.instance.snapshot_document()["sample_count"] == samples

    def construct_cross():
        return LargeCrossPacket()

    cross, result = meter("instantiate_cross_65536_bins", construct_cross)
    results.append(result)
    cross_snapshot = cross.cg.instance.snapshot_document()
    assert len(cross_snapshot["definition"]["crosses"][0]["bins"]) == 65_536

    def sample_cross():
        for index in range(cross_samples):
            cross.opcode.value = index % 256
            cross.mode.value = (index // 256) % 256
            cross.cg.sample()

    _, result = meter("sample_cross_65536_bins", sample_cross, cross_samples)
    results.append(result)
    assert cross.cg.instance.snapshot_document()["sample_count"] == cross_samples

    # A persisted run has no live collector identity. Merge its counters into
    # the same logical instance repeatedly, rather than accumulating instances.
    one_run = CoverageDatabase()
    one_run.record(packet.cg.instance, logical_instance_key="bench.packet")
    encoded_run = one_run.to_bytes()
    aggregate = CoverageDatabase.from_bytes(encoded_run)

    def merge_runs():
        for _ in range(runs):
            aggregate.merge(CoverageDatabase.from_bytes(encoded_run))

    _, result = meter("persisted_run_decode_and_merge", merge_runs, runs)
    results.append(result)
    assert aggregate.snapshot_document()["records"][0]["sample_count"] == samples * (runs + 1)
    large_database = CoverageDatabase()
    large_database.record(cross.cg.instance, logical_instance_key="bench.cross")
    for name, operation in (
        ("large_cross_snapshot_json", large_database.snapshot_json),
        ("large_cross_binary_encode", large_database.to_bytes),
        ("large_cross_type_summary", lambda: large_database.type_summary(cross_snapshot["covergroup_type_id"])),
        ("large_cross_ucis_export", lambda: export_ucis(large_database)),
    ):
        value, result = meter(name, operation)
        results.append(result)
        if isinstance(value, (str, bytes)):
            result["output_bytes"] = len(value.encode() if isinstance(value, str) else value)
        if name == "large_cross_binary_encode":
            assert CoverageDatabase.from_bytes(value).snapshot_document() == large_database.snapshot_document()
        if name == "large_cross_ucis_export":
            assert value[0] and not value[1]["losses"]
    return {
        "format": "svtypes.coverage-benchmark.v1",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "parameters": {"samples": samples, "runs": runs, "cross_samples": cross_samples, "trace_memory": trace_memory, "cpu_profile": bool(profile_dir)},
        "memory_scope": "tracemalloc Python allocations; excludes native allocations and process RSS",
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--cross-samples", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-memory", action="store_true", help="measure normal runtime without allocation tracking")
    parser.add_argument("--profile-dir", help="write one CPU profile per measured operation")
    args = parser.parse_args()
    if min(args.samples, args.runs, args.cross_samples) <= 0:
        parser.error("all counts must be positive")
    document = run(args.samples, args.runs, args.cross_samples, trace_memory=not args.no_memory, profile_dir=args.profile_dir)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        for result in document["results"]:
            print(f"{result['scenario']}: {result['elapsed_seconds']:.3f}s, peak Python {result['peak_python_bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
