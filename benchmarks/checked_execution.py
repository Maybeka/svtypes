#!/usr/bin/env python3
"""Checked-call/storage costs, with timing and allocation tracing separated.

Run: PYTHONPATH=python:. .venv/bin/python benchmarks/checked_execution.py --json
Results describe this machine and do not impose portable timing thresholds.
"""

import argparse
import gc
import json
import platform
from statistics import median
from time import perf_counter
import tracemalloc

from svtypes import (
    Int, MemoryExternalFieldStorage, Queue, Ref, bind_external_value,
    borrow_element, relocated_slot, run_checked,
)


def increment(value: Ref[Int]):
    value.value += 1


def increment_many(value: Ref[Int], count: int):
    for _ in range(count):
        value.value += 1


def native_integer(count):
    value = 0
    for _ in range(count):
        value += 1
    assert value == count


def local_value(count):
    value = Int()
    for _ in range(count):
        value.value += 1
    assert value.value == count


def checked_entry_each(count):
    value = Int()
    for _ in range(count):
        run_checked(increment, value)
    assert value.value == count


def checked_one_entry(count):
    value = Int()
    run_checked(increment_many, value, count)
    assert value.value == count


def external_value(count):
    storage = MemoryExternalFieldStorage()
    codec = Int()
    storage.seed("value", codec, 0)
    value = bind_external_value(codec, storage, "value")
    for _ in range(count):
        value.value += 1
    assert value.value == count


def element_borrow_each(count):
    values = Queue[Int]()
    values.value = [0]
    for _ in range(count):
        with borrow_element(values, 0) as element:
            element.value += 1
    assert values[0].value == count


def relocated_scope_each(count):
    value = Int()
    released = []

    def create(initial, identity):
        identity.require_open()
        storage = MemoryExternalFieldStorage()
        storage.seed("value", Int(), initial)
        return storage, "value", lambda: released.append(True)

    for _ in range(count):
        with relocated_slot(value, create):
            value.value += 1
    assert value.value == count and len(released) == count


def run(operations=1000, repeats=3):
    if operations < 1 or repeats < 1:
        raise ValueError("operations and repeats must be positive")
    results = []
    for operation in (native_integer, local_value, checked_entry_each,
                      checked_one_entry, external_value, element_borrow_each,
                      relocated_scope_each):
        started = perf_counter()
        operation(1)
        first_use = perf_counter() - started
        timings = []
        for _ in range(repeats):
            gc.collect()
            started = perf_counter()
            operation(operations)
            timings.append(perf_counter() - started)
        gc.collect()
        tracemalloc.start()
        try:
            operation(operations)
            _, peak = tracemalloc.get_traced_memory()
            gc.collect()
            retained, _ = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        elapsed = median(timings)
        results.append({
            "scenario": operation.__name__, "operations": operations,
            "first_use_seconds": first_use, "median_seconds": elapsed,
            "microseconds_per_operation": elapsed * 1e6 / operations,
            "timing_seconds": timings, "peak_python_bytes": peak,
            "retained_python_bytes_after_gc": retained,
        })
    return {"python": platform.python_version(), "platform": platform.platform(),
            "repeats": repeats, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.operations, args.repeats)
    print(json.dumps(result, indent=2) if args.json else result)
