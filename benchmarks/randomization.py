#!/usr/bin/env python3
"""Repeatable SvTypes constrained-random microbenchmarks.

This is intentionally a standalone script rather than a pytest test: elapsed
time is environment-dependent and must not make correctness regressions flaky.
Run it with ``PYTHONPATH=python:. .venv/bin/python benchmarks/randomization.py``.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Callable

from svtypes import (
    Bit,
    DynArray,
    Object,
    RandomContext,
    SvObject,
    constraint,
    dist,
    get_package,
    svobj,
)


handle_pkg = get_package("benchmark_randomization_handles")


class ScalarPacket(SvObject):
    kind = Bit(4)
    length = Bit(8)
    payload = Bit(8)

    @constraint
    def legal(self):
        self.kind < 6
        self.length <= 64
        self.payload == self.kind + self.length


class DistributionPacket(SvObject):
    choice = Bit(4)

    @constraint
    def legal(self):
        self.choice @ dist[1 @ 1, (2, 5) / 4, 9 @ 3]


class LargeDistributionPacket(SvObject):
    choice = Bit(32)

    @constraint
    def legal(self):
        self.choice @ dist[
            (0x10200000, 0x10201000) / 1,
            0x50607080 @ 3,
        ]


class DynamicPacket(SvObject):
    length = Bit(4)
    data = DynArray(Bit(8), rand=True, max_length=16)

    @constraint
    def legal(self):
        self.length == 8
        self.data.size() == self.length
        for index in range(self.data.size()):
            self.data[index] == index + 1


@svobj(registry=handle_pkg)
class HandleChild(SvObject):
    data = Bit(8)

    @constraint
    def legal(self):
        self.data <= 10


@svobj(registry=handle_pkg)
class HandleParent(SvObject):
    child = Object("HandleChild", registry=handle_pkg, rand=True)
    parent_data = Bit(8)

    @constraint
    def legal(self):
        self.parent_data == self.child.data + 1


@svobj(registry=handle_pkg)
class ContainerHandleParent(SvObject):
    children = DynArray(Object("HandleChild", registry=handle_pkg, rand=True))


@dataclass(frozen=True)
class Result:
    scenario: str
    calls: int
    elapsed_seconds: float
    calls_per_second: float
    microseconds_per_call: float


def _measure(name: str, factory: Callable[[], SvObject], calls: int, warmup: int) -> Result:
    obj = factory()
    with RandomContext(seed=0x5A17):
        for _ in range(warmup):
            if not obj.randomize():
                raise RuntimeError(f"{name}: warmup randomize() failed")
        started = perf_counter()
        for _ in range(calls):
            if not obj.randomize():
                raise RuntimeError(f"{name}: randomize() failed")
        elapsed = perf_counter() - started
    return Result(
        scenario=name,
        calls=calls,
        elapsed_seconds=elapsed,
        calls_per_second=calls / elapsed,
        microseconds_per_call=elapsed * 1_000_000 / calls,
    )


def _handle_parent() -> SvObject:
    parent = HandleParent()
    parent.child = HandleChild()
    return parent


def _container_handle_parent() -> SvObject:
    parent = ContainerHandleParent()
    parent.children.value = [HandleChild()]
    return parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=1_000, help="measured calls per scenario")
    parser.add_argument("--warmup", type=int, default=100, help="unmeasured warmup calls")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    if args.calls <= 0 or args.warmup < 0:
        parser.error("--calls must be positive and --warmup must be non-negative")

    scenarios: tuple[tuple[str, Callable[[], SvObject]], ...] = (
        ("scalar_hard_constraints", ScalarPacket),
        ("scalar_distribution", DistributionPacket),
        ("large_range_distribution", LargeDistributionPacket),
        ("dynamic_size_and_foreach", DynamicPacket),
        ("direct_rand_handle_graph", _handle_parent),
        ("container_rand_handle_graph", _container_handle_parent),
    )
    results = [_measure(name, factory, args.calls, args.warmup) for name, factory in scenarios]
    if args.json:
        print(json.dumps({
            "format": "svtypes.randomization-benchmark.v1",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "results": [asdict(result) for result in results],
        }, indent=2, sort_keys=True))
        return 0
    for result in results:
        print(
            f"{result.scenario}: {result.microseconds_per_call:.1f} us/call "
            f"({result.calls_per_second:.1f} calls/s, {result.calls} calls)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
