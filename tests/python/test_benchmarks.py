"""Benchmark executability is a correctness gate; elapsed time is not."""

import json
import os
from pathlib import Path
import subprocess
import sys

from benchmarks.coverage import ScalarPacket, measure
from benchmarks.complex_randomization import run_case, worker


ROOT = Path(__file__).resolve().parents[2]


def test_randomization_benchmark_runs_all_scenarios():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "python"), str(ROOT))))
    result = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks/randomization.py"), "--calls", "1", "--warmup", "0", "--json"],
        env=env, capture_output=True, text=True, check=True,
    )
    document = json.loads(result.stdout)
    assert document["format"] == "svtypes.randomization-benchmark.v1"
    assert len(document["results"]) == 7
    assert all(item["calls"] == 1 for item in document["results"])


def test_coverage_benchmark_scalar_fixture_has_256_bins_and_samples():
    packet = ScalarPacket()
    _, result = measure("sample", packet.cg.sample)
    snapshot = packet.cg.instance.snapshot_document()
    assert snapshot["sample_count"] == 1
    assert len(snapshot["definition"]["points"][0]["bins"]) == 256
    assert result["operations"] == 1
    assert result["peak_python_bytes"] >= result["retained_python_bytes"] >= 0


def test_coverage_measure_separates_memory_and_cpu_instrumentation(tmp_path):
    value, result = measure("simple", lambda: 3, trace_memory=False, profile_dir=tmp_path)
    assert value == 3
    assert result["peak_python_bytes"] is None
    assert (tmp_path / "simple.prof").is_file()


def test_complex_probes_verify_sat_unsat_and_joint_graph_results():
    for case in ("permutation", "chain", "adjacent_index", "graph", "nonlinear", "dependent_dist"):
        result = worker(case, 4, 2, 23063)
        assert result["status"] == "verified"
        assert result["successes"] == 2
    result = worker("unsat", 5, 1, 23063)
    assert result["successes"] == 0
    assert result["diagnostics"][0]["reason"] == "unsat"


def test_dependent_distribution_large_total_completes_within_deadline():
    # Bound the subprocess so a regression of the former infinite loop fails
    # the test instead of hanging the entire suite.
    result = run_case("dependent_dist", 128, 1, 23063, 30)
    assert result["status"] == "verified"
    assert result["successes"] == 1
    assert result["weight_analysis"]["integer_total_bits"] > 64
