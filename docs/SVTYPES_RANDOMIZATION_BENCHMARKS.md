# SvTypes constrained-random benchmarks

`benchmarks/randomization.py` is the reproducible microbenchmark suite for the
Python constrained-random engine. It is deliberately separate from pytest:
elapsed time is hardware- and dependency-version-specific, whereas the test
suite must remain a correctness gate.

Run it from the repository root:

```sh
PYTHONPATH=python:. .venv/bin/python benchmarks/randomization.py --calls 1000
```

Use `--json` when recording a baseline in CI or a release note. The output
contains the interpreter and platform plus per-scenario elapsed time, rate, and
microseconds per call. It does not prescribe a universal pass/fail threshold.

The baseline scenarios intentionally cover the principal solver paths:

- scalar hard constraints;
- weighted scalar `dist`;
- wide direct-range `dist` without support expansion;
- wide-domain `solve_before` selection;
- dynamic collection size plus `foreach` constraints;
- direct and container-held non-null `rand Object` graphs.

New solver features should add a scenario when they introduce a materially
different search strategy. Performance investigations compare the same Python,
Z3, machine class, and `--calls`/`--warmup` settings; measurements from unlike
environments are trend information, not regressions.
