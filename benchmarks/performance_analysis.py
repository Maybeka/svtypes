#!/usr/bin/env python3
"""Local analysis instrumentation; does not modify SvTypes implementation."""
from __future__ import annotations

import argparse
import cProfile
import gc
from dataclasses import asdict
import json
from pathlib import Path
import resource
import subprocess
import sys
from time import perf_counter
import tracemalloc
from xml.etree import ElementTree as ET

from benchmarks import randomization as rand
from benchmarks.coverage import LargeCrossPacket
from svtypes import Bit, CodecSession, CoverageDatabase, DynArray, SvObject, constraint
from svtypes.coverage import ucis


class DynamicNoCov(SvObject):
    length = Bit[4](cov=False)
    data = DynArray[Bit[8]](rand=True, max_length=16, cov=False)

    @constraint
    def legal(self):
        self.length == 8
        self.data.size() == self.length
        for index in range(self.data.size()):
            self.data[index] == index + 1


def dynamic_retention_probe():
    results = []
    for name, factory in (("cov_default", rand.DynamicPacket), ("cov_disabled", DynamicNoCov)):
        session = CodecSession()
        tracemalloc.start()
        obj = factory(session=session)
        completed = 0
        checkpoints = []
        try:
            for target in (0, 1, 10, 100, 500):
                while completed < target:
                    assert obj.randomize()
                    assert obj.data.value == list(range(1, 9))
                    completed += 1
                gc.collect()
                current, peak = tracemalloc.get_traced_memory()
                checkpoints.append({"calls": completed, "registered_objects": len(session._objects),
                                    "retained_python_bytes": current, "peak_python_bytes": peak})
        finally:
            tracemalloc.stop()
            session.clear()
        results.append({"fixture": name, "checkpoints": checkpoints})
    return results


def random_cases():
    return (("scalar", rand.ScalarPacket), ("dist", rand.DistributionPacket),
            ("wide_dist", rand.LargeDistributionPacket), ("solve_before", rand.WideOrderedPacket),
            ("dynamic", rand.DynamicPacket), ("graph", rand._handle_parent),
            ("container_graph", rand._container_handle_parent))


def analyze_random(kind, calls, directory):
    results = []
    for name, factory in random_cases():
        profiler = cProfile.Profile() if kind == "random-profile" else None
        if profiler:
            profiler.enable()
        else:
            tracemalloc.start()
        try:
            result = asdict(rand._measure(name, factory, calls, 10))
            if not profiler:
                result["retained_python_bytes"], result["peak_python_bytes"] = tracemalloc.get_traced_memory()
                gc.collect()
                result["retained_after_gc_bytes"] = tracemalloc.get_traced_memory()[0]
                result["allocation_sites_after_gc"] = [
                    {"location": str(item.traceback), "bytes": item.size, "count": item.count}
                    for item in tracemalloc.take_snapshot().statistics("lineno")[:8]
                ]
        finally:
            if profiler:
                profiler.disable()
                directory.mkdir(parents=True, exist_ok=True)
                profiler.dump_stats(str(directory / f"random-{name}.prof"))
            else:
                tracemalloc.stop()
        results.append(result)
    return results


def ucis_membership_probe():
    packet = LargeCrossPacket()
    packet.cg.sample()
    database = CoverageDatabase()
    database.record(packet.cg.instance, logical_instance_key="bench.cross")
    started = perf_counter()
    original_result = ucis.export_ucis(database)
    original_seconds = perf_counter() - started
    original_function = ucis._exportable_cross_bins

    class IndexedNames(list):
        def __init__(self, names):
            super().__init__(names)
            self.index = set(names)

        def __contains__(self, name):
            return name in self.index

    # Only this experiment's process changes. Preserve the original iteration
    # order and strings, replace repeated linear membership checks alone.
    ucis._exportable_cross_bins = lambda *args: IndexedNames(original_function(*args))
    try:
        started = perf_counter()
        indexed_result = ucis.export_ucis(database)
        indexed_seconds = perf_counter() - started
    finally:
        ucis._exportable_cross_bins = original_function
    def without_export_timestamp(xml):
        root = ET.fromstring(xml)
        root.attrib.pop("writtenTime")
        for node in root.findall("historyNodes"):
            node.attrib.pop("date")
        return ET.tostring(root)

    assert without_export_timestamp(original_result[0]) == without_export_timestamp(indexed_result[0]), "probe changed XML semantics"
    assert original_result[1] == indexed_result[1], "probe changed loss report"
    return {"original_seconds": original_seconds, "indexed_seconds": indexed_seconds,
            "identical_xml_except_export_timestamps_and_loss_report": True, "xml_bytes": len(original_result[0].encode()),
            "note": "process-local prototype, not a production optimization"}


def baseline_repeat(calls, repeats):
    results = []
    root = Path(__file__).resolve().parents[1]
    # Sequential child processes: no other benchmark worker is launched until
    # the previous one exits. Profiles/allocation tracing are not enabled.
    for repeat in range(repeats):
        for component, arguments in (
            ("randomization", ["--calls", str(calls), "--warmup", "100", "--json"]),
            ("coverage", ["--no-memory", "--json"]),
        ):
            process = subprocess.run([sys.executable, str(root / "benchmarks" / f"{component}.py"), *arguments],
                                     cwd=root, capture_output=True, text=True, check=True)
            results.append({"repeat": repeat + 1, "component": component, "measurement": json.loads(process.stdout)})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("random-profile", "random-memory", "ucis-probe", "dynamic-retention", "baseline-repeat"))
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--profile-dir", type=Path, default=Path(".tmp/random-cpu"))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if min(args.calls, args.repeats) <= 0:
        parser.error("calls and repeats must be positive")
    if args.kind == "ucis-probe":
        results = ucis_membership_probe()
    elif args.kind == "dynamic-retention":
        results = dynamic_retention_probe()
    elif args.kind == "baseline-repeat":
        results = baseline_repeat(args.calls, args.repeats)
    else:
        results = analyze_random(args.kind, args.calls, args.profile_dir)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"kind": args.kind, "results": results,
                      "process_high_water_rss_mib": rss / (1024**2 if sys.platform == "darwin" else 1024),
                      "rss_scope": "whole-process high water, not per-operation delta"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
