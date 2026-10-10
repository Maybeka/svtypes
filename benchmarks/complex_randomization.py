#!/usr/bin/env python3
"""Bounded, independently verified constrained-random scalability probes.

Every case runs in a separate process. A wall timeout is a measurement result,
not a solver UNSAT result. No production solver settings are changed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
from math import lcm
import os
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter

from svtypes import Bit, CodecSession, DynArray, Object, RandomContext, SvObject, constraint, dist, get_package, svobj, unique


class Permutation(SvObject):
    count = Bit[16](rand=False)
    domain = Bit[16](rand=False)
    data = DynArray[Bit[8]](rand=True, max_length=128)

    @constraint
    def legal(self):
        self.data.size() == self.count
        unique(self.data)
        for i in range(self.data.size()):
            self.data[i] < self.domain


class Chain(SvObject):
    count = Bit[16](rand=False)
    data = DynArray[Bit[16]](rand=True, max_length=128)

    @constraint
    def legal(self):
        self.data.size() == self.count
        self.data[0] == 1
        for i in range(self.data.size()):
            self.data[i] == i + 1


class Nonlinear(SvObject):
    limit = Bit[32](rand=False)
    product = Bit[32](rand=False)
    x = Bit[32]()
    y = Bit[32]()

    @constraint
    def legal(self):
        self.x >= 2
        self.y >= 2
        self.x <= self.limit
        self.y <= self.limit
        self.x * self.y == self.product


class WideFactor(SvObject):
    limit = Bit[64](rand=False)
    product = Bit[64](rand=False)
    x = Bit[64]()
    y = Bit[64]()

    @constraint
    def legal(self):
        self.x >= 2
        self.y >= 2
        self.x <= self.limit
        self.y <= self.limit
        self.x * self.y == self.product


class DependentDistribution(SvObject):
    bound = Bit[16](rand=False)
    x = Bit[16]()
    y = Bit[16]()

    @constraint
    def legal(self):
        self.x < self.bound
        self.y == self.x
        self.x @ dist[0 @ 1, (1, self.bound - 1) @ (self.y + 1)]


registry = get_package("benchmark_complex_graph")


@svobj(registry=registry)
class Child(Permutation):
    pass


@svobj(registry=registry)
class Graph(SvObject):
    child = Object["Child"](registry=registry, rand=True)
    tag = Bit[8]()

    @constraint
    def legal(self):
        self.tag == self.child.data[0]


def worker(case, size, calls, seed):
    session = CodecSession()
    weight_analysis = None
    if case == "adjacent_index":
        class Adjacent(SvObject):
            count = Bit[16](rand=False)
            data = DynArray[Bit[16]](max_length=128)

            @constraint
            def legal(self):
                self.data.size() == self.count
                for i in range(self.data.size()):
                    if i == 0:
                        self.data[i] == 1
                    if i > 0:
                        self.data[i] == self.data[i - 1] + 1

        obj = Adjacent(session=session)
        obj.count.value = size
    elif case == "chain":
        obj = Chain(session=session)
        obj.count.value = size
    elif case == "dependent_dist":
        obj = DependentDistribution(session=session)
        obj.bound.value = size
        weights = [Fraction(1, 2)] + [Fraction(i + 1, i + 2) for i in range(1, size)]
        denominator = lcm(*(weight.denominator for weight in weights))
        total = sum(int(weight * denominator) for weight in weights)
        weight_analysis = {"integer_total_bits": total.bit_length(),
                           "fixed_64_bit_rejection_limit": ((1 << 64) // total) * total}
        print("weight_probe " + json.dumps(weight_analysis), file=sys.stderr, flush=True)
    elif case in ("nonlinear", "factor"):
        obj = Nonlinear(session=session) if case == "nonlinear" else WideFactor(session=session)
        obj.limit.value = size + 3 if case == "nonlinear" else (1 << size) - 1
        factors = {16: (65521, 65519), 32: (4294967291, 4294967279)}
        obj.product.value = (size + 1) * (size + 3) if case == "nonlinear" else factors[size][0] * factors[size][1]
    elif case == "graph":
        obj = Graph(session=session)
        obj.child = Child(session=session)
        obj.child.count.value = size
        obj.child.domain.value = size
    else:
        obj = Permutation(session=session)
        obj.count.value = size
        obj.domain.value = size - 1 if case == "unsat" else size
    elapsed = []
    outputs = set()
    successes = 0
    statuses = []
    child_identity = id(obj.child) if case == "graph" else None
    with RandomContext(seed=seed):
        for _ in range(calls):
            target = obj.child if case == "graph" else obj
            before = tuple(target.data.value) if case not in ("nonlinear", "factor", "dependent_dist") else (obj.x.value, obj.y.value)
            started = perf_counter()
            ok = obj.randomize()
            elapsed.append(perf_counter() - started)
            statuses.append(asdict(obj.svtypes_randomize_status))
            if case == "unsat":
                assert not ok, "pigeonhole fixture unexpectedly SAT"
                assert tuple(obj.data.value) == before, "UNSAT modified the input values"
                print("verified_call", file=sys.stderr, flush=True)
                continue
            assert ok, f"known-SAT fixture failed: {obj.svtypes_randomize_status}"
            successes += 1
            if case == "dependent_dist":
                values = (obj.x.value, obj.y.value)
                assert values[0] == values[1] and 0 <= values[0] < size
            elif case in ("nonlinear", "factor"):
                values = (obj.x.value, obj.y.value)
                assert min(values) >= 2 and max(values) <= obj.limit.value
                assert values[0] * values[1] == obj.product.value
            else:
                values = tuple(target.data.value)
                assert len(values) == size
                if case in ("chain", "adjacent_index"):
                    assert values == tuple(range(1, size + 1))
                else:
                    assert len(set(values)) == size and all(0 <= x < size for x in values)
                    if case == "graph":
                        assert obj.tag.value == values[0]
                        assert id(obj.child) == child_identity, "randomize replaced the child handle"
            outputs.add(values)
            print("verified_call", file=sys.stderr, flush=True)
    ordered = sorted(elapsed)
    return {"case": case, "size": size, "calls": calls, "seed": seed,
            "successes": successes, "distinct_outputs": len(outputs), "status": "verified",
            "seconds": elapsed, "median_seconds": statistics.median(elapsed),
            "max_seconds": max(elapsed), "p95_seconds": ordered[min(len(ordered)-1, int(len(ordered)*0.95))],
            "output_set_digest": hashlib.sha256(json.dumps(sorted(outputs)).encode()).hexdigest(),
            "example_output": list(sorted(outputs)[0]) if outputs else None,
            "registered_objects_after_calls": len(session._objects),
            "weight_analysis": weight_analysis,
            "diagnostics": statuses}


def run_case(case, size, calls, seed, timeout):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--case", case,
               "--size", str(size), "--calls", str(calls), "--seed", str(seed)]
    try:
        process = subprocess.run(command, env=os.environ, text=True, capture_output=True, timeout=timeout)
        return json.loads(process.stdout) if process.returncode == 0 else {
            "case": case, "size": size, "status": "error", "error": process.stderr}
    except subprocess.TimeoutExpired as exc:
        progress = exc.stderr or b""
        if isinstance(progress, bytes):
            progress = progress.decode(errors="replace")
        weight_analysis = next((json.loads(line.removeprefix("weight_probe "))
                                for line in progress.splitlines() if line.startswith("weight_probe ")), None)
        return {"case": case, "size": size, "status": "wall_timeout", "timeout_seconds": timeout,
                "completed_verified_calls": progress.count("verified_call"), "weight_analysis": weight_analysis}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("permutation", "chain", "graph", "nonlinear", "factor", "unsat", "adjacent_index", "dependent_dist"))
    parser.add_argument("--size", type=int, default=8)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--seed", type=int, default=23063)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.calls <= 0 or not 2 <= args.size <= 128 or args.timeout <= 0:
        parser.error("calls/timeout must be positive and size must be in [2,128]")
    if args.case:
        if args.case == "factor" and args.size not in (16, 32):
            parser.error("factor size must be 16 or 32")
        result = worker(args.case, args.size, args.calls, args.seed) if args.worker else run_case(
            args.case, args.size, args.calls, args.seed, args.timeout)
        print(json.dumps(result, sort_keys=True))
        return int(result["status"] == "error")
    cases = [(case, size) for case, sizes in (
        ("permutation", (4, 8, 16, 32)), ("chain", (4, 16, 64, 128)),
        ("graph", (4, 8, 16, 32)), ("nonlinear", (16, 64, 128)), ("unsat", (5, 9, 17)),
        ("factor", (16, 32)),
        ("dependent_dist", (8, 32, 128)),
        ("adjacent_index", (16,)),
    ) for size in sizes]
    results = []
    for case, size in cases:
        result = run_case(case, size, args.calls, args.seed, args.timeout)
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
    return int(any(item["status"] == "error" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
