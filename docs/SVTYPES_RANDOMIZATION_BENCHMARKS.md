# SvTypes randomization and coverage performance acceptance

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

## Coverage benchmark

```sh
PYTHONPATH=python:. .venv/bin/python benchmarks/coverage.py --json
```

Defaults are 10,000 scalar samples over 256 explicit bins, a 256 × 256 cross
at the supported single-cross limit (65,536 normal bins), 10 cross samples,
and 100 persisted-run decode/merge operations into one logical instance.
The suite also measures large-cross JSON snapshot reporting, binary encoding,
type-summary calculation and UCIS export. It verifies sample counts, the
materialized cross bin count, accumulated cross-run sample counts, binary
round-trip equality and a loss-free UCIS export for this supported fixture.

Coverage measurements use `tracemalloc` throughout each timed operation.
Memory numbers refer to Python allocations, **not process RSS or native
allocations**. These instrumented times are not directly comparable to the
uninstrumented randomization measurements. Allocation peaks are per operation;
objects allocated before that operation are not included in its peak. The
suite does not claim that one 65,536-bin cross proves acceptable cost for an
entire 1,048,576-bin covergroup or for every transition/queue construct.

`tests/python/test_benchmarks.py` checks executable randomization scenarios and
the scalar coverage fixture, without timing thresholds. Full coverage benchmark
runs remain explicit acceptance work, not a slow default pytest gate.

## 2026-10-09 current-tree acceptance

Baseline: package 1.4.2, commit `4d016b3` plus the benchmark migration, new
coverage benchmark and remote-test marker correction in this working tree.
Python 3.14.8, Z3 5.1.0, macOS arm64. The package release number and historical
1.3–1.10 work-stage numbers are separate; this is not a 2.0 release declaration.
Raw measurements and remote output remain ignored local acceptance artifacts.
No specific target implementation or proprietary report is part of this record.

Current-tree regression results: **685 local tests passed** with 36 remote
tests deselected; **36 remote tests passed** with 683 local tests deselected.
The two benchmark smoke tests were added after the remote run and then included
in the final local run; no mainline implementation changed between those runs.
The previously unmarked nested-struct/default-values test now participates in
the remote gate. The loopback GUI service test was run with local binding
permission; no browser interaction equivalence is claimed by that service test.

Randomization baseline: 1,000 measured calls per scenario, 100 warmup calls,
seed 23063. All seven scenarios completed successfully.

| Scenario | Microseconds per call |
| --- | ---: |
| Scalar hard constraints | 3,020.9 |
| Scalar distribution | 423.3 |
| Large-range distribution | 2,483.2 |
| Wide solve-before | 3,526.6 |
| Dynamic size and foreach | 9,575.3 |
| Direct rand handle graph | 2,641.7 |
| Container rand handle graph | 565.7 |

Coverage baseline with the default workload:

| Scenario | Seconds | Peak Python allocation (MiB) |
| --- | ---: | ---: |
| 10,000 scalar samples / 256 bins | 12.664 | 0.14 |
| Instantiate 65,536-bin cross | 7.823 | 97.41 |
| 10 samples of that cross | 1.545 | 0.007 |
| 100 persisted-run decode/merge operations | 0.281 | 0.43 |
| Large-cross JSON snapshot | 1.855 | 75.57 |
| Large-cross binary encoding | 1.828 | 100.04 |
| Large-cross type summary | 0.003 | 0.001 |
| Large-cross UCIS export | 18.386 | 148.73 |

The snapshot is 16,527,960 UTF-8 bytes and binary output is 16,528,102 bytes.
The binary format is not claimed to compress the definition. Large-layout
construction and export are visible cost centers, not a correctness failure or
evidence of a hardware-independent performance guarantee. Future optimization
must preserve the frozen declaration, bin identity, counters and merge contract.

### Freeze boundary

The randomization foundation and coverage roadmap remain the normative
semantic contracts. This acceptance does not change DSL, resource limits,
schema/digest or database format versions. Target capability gates recorded in
the roadmap remain unsupported/unobservable; passing other target fixtures
does not close them. GUI, source writeback and third-party backend work remain
post-2.0 extensions, not new mainline release requirements.

The acceptance record proves execution of the supported regression fixtures and
the measured workloads. It does not by itself freeze every public API or approve
a 2.0 release: final API/schema/version and public-package-content audits still
belong to the 2.0 RC gate.

## 2026-10-09 CPU, memory and complex-solve analysis

This section analyzes the current implementation, not a proposed optimized
engine. Only benchmark instrumentation and fixtures changed. No production
randomization, coverage, database or UCIS semantics were modified.

### Reproduction and measurement separation

```sh
PYTHONPATH=python:. .venv/bin/python benchmarks/performance_analysis.py --kind baseline-repeat --calls 1000 --repeats 3
PYTHONPATH=python:. .venv/bin/python benchmarks/performance_analysis.py --kind random-profile
PYTHONPATH=python:. .venv/bin/python benchmarks/performance_analysis.py --kind random-memory
PYTHONPATH=python:. .venv/bin/python benchmarks/performance_analysis.py --kind dynamic-retention
PYTHONPATH=python:. .venv/bin/python benchmarks/coverage.py --no-memory --samples 1000 --profile-dir .tmp/coverage-cpu --json
PYTHONPATH=python:. .venv/bin/python benchmarks/performance_analysis.py --kind ucis-probe
PYTHONPATH=python:. .venv/bin/python benchmarks/complex_randomization.py --calls 10 --timeout 30
```

Normal timing repeats run in sequential fresh subprocesses without CPU or
allocation instrumentation. CPU profiles identify call paths; their elapsed
time is not normal throughput. Random-memory measurements use 100 calls plus
10 warmups, collect garbage before examining retained allocations, and exclude
native Z3 allocations. RSS is reported only as whole-process high water, not
per-operation memory. Initial profiling/probes may overlap other workers;
the final normal timing repeats do not launch competing benchmark workers.
Host-wide background load is not controlled.

Three sequential uninstrumented runs produced these medians (same workload and
environment as above). Ranges show all three runs, not confidence intervals.

| Randomization scenario | Median ms/call | Three-run range ms/call |
| --- | ---: | ---: |
| Scalar hard constraints | 2.964 | 2.964–3.066 |
| Scalar distribution | 0.384 | 0.378–0.400 |
| Large-range distribution | 2.414 | 2.393–2.490 |
| Wide solve-before | 3.511 | 3.421–3.790 |
| Dynamic size and foreach | 9.649 | 9.349–10.376 |
| Direct rand handle graph | 2.500 | 2.483–2.608 |
| Container rand handle graph | 0.496 | 0.492–0.503 |

| Coverage operation | Median seconds | Three-run range seconds |
| --- | ---: | ---: |
| 10,000 scalar samples / 256 bins | 0.669 | 0.661–0.719 |
| First instantiate / 65,536-bin cross | 0.659 | 0.655–0.784 |
| 10 samples / 65,536-bin cross | 0.188 | 0.185–0.215 |
| 100 persisted-run decode/merges | 0.081 | 0.080–0.082 |
| JSON snapshot | 0.494 | 0.493–1.101 |
| Binary encoding | 0.495 | 0.486–0.630 |
| Type summary | 0.0028 | 0.0028–0.0030 |
| UCIS export | 13.669 | 13.478–14.689 |

The first-instance number includes first declaration freeze. Scalar sampling
is approximately 66.9 microseconds/sample; large-cross sampling is approximately
18.8 milliseconds/sample. The traced 7.8-second instantiation time in the earlier
table is therefore not its normal runtime, while the UCIS quadratic cost
remains substantial even without tracing. Do not interpret the very cheap
type-summary fixture as evidence for expensive merged-instance scoring: this
fixture uses the default independent-instance summary policy.

### Randomization hotspots

The seven-scenario CPU profile (100 measured calls and 10 warmups each) spent
4.03 of 6.21 profiled seconds cumulatively in SMT `solve`; this is an inclusive
call-path measurement, not an additive breakdown. There were 26,484 native
solver-check calls. Much of the remaining cost is Python Z3 term construction,
assertions, reference counting and bit-by-bit model selection, not just time
inside native SAT checks.

The dynamic-size fixture performed 330 `solve` calls for 110 `randomize` calls:
size candidates and expanded element solving are separate phases. The ordered
fixture spent 0.905 of 0.947 profiled seconds in `solve_ordered`, split across
random feasible-bit selection and deterministic remaining-model selection.
These observations suggest measuring request construction and repeated solver
work before assuming native Z3 is the sole bottleneck. Any caching must account
for enabled constraints, state, graph aliases, dynamic shape and modes; reusing
an old solver/model unconditionally would be incorrect.

#### Confirmed dynamic-container retention defect

One host object with an eight-element dynamic array is randomized repeatedly
in an isolated `CodecSession`. After collection growth on the first call, each
later call adds a registered host copy. Garbage collection does not free these
copies. Both fixtures independently verify all resulting element values.

| Calls | Registered objects | Retained Python MiB, default cov | Retained Python MiB, cov disabled |
| ---: | ---: | ---: | ---: |
| 0 | 1 | 0.29 | 0.001 |
| 1 | 1 | 0.70 | 0.018 |
| 10 | 10 | 1.65 | 0.14 |
| 100 | 100 | 11.11 | 1.37 |
| 500 | 500 | 53.14 | 6.84 |

Cause: `_snapshot_collection_elements` deep-copies bound scalar descriptors;
`bind_nested` places `_svtypes_mode_root` on those descriptors, so the copy
traverses the owner. `SvObject.__deepcopy__` creates a new value-state host that
registers in the session by default. The session strongly retains that host.
Coverage auto-bin materialization makes each unintended copy much larger, but
turning cov off does **not** eliminate the defect. It is not an ordinary bounded
cache or a remedy to advise users to turn off coverage.

Priority: repair snapshot ownership/identity handling while preserving rollback,
element modes and class-handle identity. Add a regression that many successful
and failing dynamic randomizations neither register extra hosts nor grow
retained state linearly. This analysis did not implement that repair.

### Coverage hotspots and verified UCIS prototype

- **Sampling:** 10 large-cross samples made 655,360 `_matches_cross_selector`
  calls. `_sample_cross` walks all normal bins even when only one tuple can hit.
  Scalar sampling likewise made 256,000 selector checks for 1,000 samples.
  Indexing is a plausible improvement, but it must preserve overlapping normal
  hits, ignore/illegal precedence, member `iff`, private member views and queue
  selectors. It is not safe to assume that every cross is a unique tuple lookup.
- **Instantiation:** the profiled first instance spent about 1.13 seconds in
  cross materialization and 0.75 seconds in first declaration freeze. Repeated
  `replace`, canonicalization and reconstruction of already-static selectors
  are substantial. Reuse only immutable static definition data; input/ref,
  options and instance counters must remain independent.
- **Persistence/reporting:** `to_bytes` spent about 0.87 of 0.98 profiled seconds
  constructing the deep-copied database snapshot, versus about 0.10 seconds in
  encoding. Avoiding unnecessary intermediate copies is more promising than
  changing the on-disk format as a first optimization. Snapshot isolation must
  remain intact.
- **UCIS:** `_export_record` spent 13.14 self seconds in a 14.60-second profile.
  Its loss-report loop checks each bin name against a list of exported names,
  causing quadratic membership work on large crosses. A process-local prototype
  retained list iteration order but used a set for membership. The same 65,536
  bins exported in **13.86 seconds originally versus 0.70 seconds indexed**.
  Complete XML matched after excluding only export timestamps, and loss reports
  matched exactly. The prototype restored the original function and did not
  change production source. This is concrete evidence for a low-risk optimization
  candidate, not a guarantee for all UCIS layouts.

The prototype process reached 380.25 MiB RSS while retaining both XML results
and comparison trees. That is a whole-process experiment high water, not the
memory required by a single normal export.

### Complex constrained-random capability

Each workload is known SAT or mathematically UNSAT, and output values are
checked independently in Python. Each case runs in its own process. The
30-second wall budget bounds the entire case, including declaration/construction
and up to ten calls; a timeout is **not** an UNSAT result. At both timed-out cases
zero calls had finished verification. Small ten-call samples are indicative;
their p95 equals the maximum and is not a statistically established tail SLA.

| Workload | Scale | Outcome / indicative median per call |
| --- | --- | --- |
| Dynamic full permutation, `unique` plus bounds | 4 / 8 / 16 / 32 elements | SAT, about 9 / 15 / 34 / 121 ms |
| Deterministic `foreach`: element equals index + 1 | 4 / 16 / 64 / 128 elements | SAT, about 8 / 22 / 117 / 361 ms |
| Parent/child joint solve, child dynamic permutation | 4 / 8 / 16 / 32 elements | SAT, about 10 / 15 / 33 / 119 ms; handle identity unchanged |
| 32-bit multiplication with small operand bounds | bounds 19 / 67 / 131 | SAT, about 13–14 ms |
| 64-bit multiplication of two bounded 16-bit factors | 65,521 × 65,519 | SAT, about 90 ms |
| 64-bit multiplication of two bounded 32-bit factors | 4,294,967,291 × 4,294,967,279 | 30-second wall timeout, zero verified calls |
| Pigeonhole `unique` | 5 elements / 4 values; 9 / 8 | UNSAT, about 6 / 50 ms; original values restored |
| Pigeonhole `unique` | 17 elements / 16 values | 30-second wall timeout, zero verified calls |
| Dynamic `foreach` adjacent reference | `data[i - 1]` under `if i > 0` | Declaration rejected: index must be compile-time integer |
| Random-dependent dist weight with correlated `y == x` | 8 / 32 feasible models | SAT, about 38 / 230 ms; 3/3 and 8/10 distinct results |
| The same dependent dist | 128 feasible models | Confirmed nonterminating selection loop; externally stopped at 10 seconds, zero verified calls |

The deterministic foreach fixture is deliberately **not** called an equivalent
replacement for adjacent-element recurrence. The latter expression remains a
separate rejected capability probe. Small-bound multiplication results do not
demonstrate scalable arbitrary factorization or arbitrary arithmetic solving.

#### Satisfiability is not sufficient random diversity

Full-permutation constraints have many valid assignments. All ten calls for
each tested permutation size nevertheless returned one distinct assignment.
For size 16, seeds 1, 2 and 3 produced identical output-set digests as well.
The general path tries 32 unconstrained candidates; narrow joint domains make
acceptance extremely unlikely. It then obtains the deterministic minimum model
using `backend.smt.solve`. Thus this is expected from the current algorithm,
not proof that the constraint has only one solution or that randomness is
uniform. A useful complex constrained-random generator needs a separate plan
for diverse feasible-model selection; seed reproducibility alone does not meet
that practical need. No uniform-all-model guarantee is made here.

The production backend has no request timeout configured. Its generic `solve`
also treats a non-SAT check as UNSAT, rather than exposing an UNKNOWN reason.
If timeout/budget controls are introduced, UNKNOWN/timeout must remain distinct
from proved UNSAT; simply setting a Z3 timeout without changing this handling
would produce misleading failures. The experiment's external wall timeout
does not modify or validate such a production API.

#### Confirmed weighted-selection nontermination

The dependent-dist fixture is:

```python
class DependentDistribution(SvObject):
    bound = Bit[16](rand=False)
    x = Bit[16]()
    y = Bit[16]()

    @constraint
    def legal(self):
        self.x < self.bound
        self.y == self.x
        self.x @ dist[0 @ 1, (1, self.bound - 1) @ (self.y + 1)]
```

With `bound=32`, profiling two calls showed 66 SMT solves: enumerate 32 models
plus one UNSAT exhaustion check per call. With `bound=128`, a timed stack dump
placed the blocked process in `BitStream.draw_bits` called by
`_solve_enumerated_dist`'s weighted-selection loop, **not in Z3**. The fixture's
normalized model weights yield a 191-bit integer total after common-denominator
conversion. The code calculates `limit = ((1 << 64) // total) * total`; that
limit is zero. It then repeatedly draws 64 bits and tests `draw < limit`, which
can never succeed. A ten-second subprocess deadline reproduces zero completed
calls; the mathematical zero-limit invariant establishes nontermination rather
than merely a slow observed solve.

The finite-support weighted path contains the same fixed-64-bit rejection
formula and also requires auditing for large totals. A correct implementation
needs arbitrary-width bounded sampling (and potentially common-factor reduction),
not a larger timeout or a silently unweighted witness. The analysis harness now
enforces an external subprocess deadline even for single-case CLI runs. Its
direct `worker()` entry point remains intended only for controlled small
profiling/test fixtures. No production fix is included.

Complete-model dist enumeration is also capped at 4,096; beyond that the source
returns to the general SMT fallback. That separate boundary has not been
statistically validated by this experiment. Three or ten distinct-output probes
are not a distribution conformance test; weights and branch normalization need
dedicated statistical/resource validation before claiming every complex SAT
fallback preserves weights.

### Recommended order at the analysis baseline

1. Fix unintended dynamic snapshot owner copies/registration and weighted
   selection nontermination first. These are unbounded retained-state growth
   and a valid-SAT request hanging, not merely throughput tuning opportunities.
2. Apply and verify ordered set-membership acceleration in UCIS export.
3. Design diverse feasible-model selection and explicit solve-budget diagnostics,
   retaining deterministic seeds, hard/soft constraints and distribution rules.
4. Add selector indexes for simple points/crosses with a semantic fallback for
   overlapping or more complex selectors; share immutable static layouts and
   reduce export snapshot copying only after isolation tests.
5. Resolve or explicitly document arithmetic foreach-index support and validate
   the complex distribution-policy boundary before final capability closure.

This analysis identifies work; it does not approve 2.0 release closure or silently
expand the public random/coverage contracts. No runtime fix, commit or push is
part of this analysis task.

Validation after adding the analysis fixtures: 687 local tests passed with
36 remote tests deselected. The focused random/constraint suites passed 56 tests;
benchmark smoke tests also passed after adding the dependent-dist probe and
deadline wrapper. Production code is unchanged, so this analysis did not rerun
the remote target or replace the earlier 36-pass remote acceptance result.

### Follow-up implementation: correctness and UCIS membership

The preceding measurements describe the pre-fix analysis baseline. The current
follow-up implements three repairs without adding a public API or changing
coverage identities, scores or interchange formats:

- Dynamic collection snapshots preserve the bound mode owner in the deepcopy
  memo. Snapshotting scalar elements no longer copies/registers a complete host.
  Failure restores the original collection shape before its leaf values; a
  shrinking UNSAT attempt previously indexed a removed element during rollback.
- Both finite-support and enumerated-model dist selection use arbitrary-width
  rejection sampling. Totals up to and including `2**64` retain the preceding
  64-bit stream consumption; larger totals use enough bits for a nonzero
  rejection limit. This does not fix the separate 4,096-model policy boundary.
- UCIS cross export indexes exportable names in a set for omitted-bin checks,
  preserving the ordered list for emitted XML.

Measured checks on the same fixture sizes (not release-wide capability proof):

| Check | Pre-fix | Current follow-up |
|---|---|---|
| 500 dynamic calls, registered hosts | 500 | 1 throughout |
| Default auto coverage, retained Python memory after 500 calls | 53.14 MiB | 729,151 bytes (about 0.70 MiB) |
| Coverage disabled, retained memory after 500 calls | 6.84 MiB | 19,934 bytes |
| Dependent dist, bound 128, 191-bit total | Nonterminating selection | 2 verified calls, about 2.18 s each |
| 65,536 cross bins, UCIS export | 13.70 s | 0.70 s |

For the UCIS comparison, the baseline exporter was loaded from the current HEAD
and both implementations exported the same sampled database in one process.
Full XML agreed after removing export timestamps; loss reports agreed exactly.
The dynamic fixture retained 728,319 bytes after its first call and 729,151
after its 500th, rather than growing by a registered host per call.

Regression tests cover arbitrary total widths, rejection retries, preserved
64-bit seed vectors, observable large-weight ratios, SAT/UNSAT snapshots for
DynArray/Queue, and a subprocess deadline around the formerly hanging dependent
dist. UCIS has a non-timing gate rejecting linear-list membership scans.
Final local full regression: 700 passed, 36 remote tests deselected (36.65 s).
The benchmark/database focused suites also passed (27 tests). The configured
remote SystemVerilog regression passed all 36 tests (165.41 s); it collected
before the final two local-only regression cases were added, with 698 deselected.
Production code was unchanged during that remote run. These are qualification
of this repair batch, not proof of the remaining complex-solver/scale goals.

Still open: complex feasible-model diversity, explicit solve budgets and
UNKNOWN/timeout diagnostics, arithmetic foreach indices, distribution behavior
past enumeration limits, sampling indexes, static layout sharing, and snapshot
copy costs. The goal is active; these repairs do not establish 2.0 readiness.
No commit or push has been performed.

### Follow-up implementation: coverage sampling indexes

`CoverageRuntime` builds private indexes for frozen integer singleton/set point
bins and complete cross-bin reference tuples. Unsupported/index-ineligible
selectors initially kept the original matcher: non-singleton ranges, context-dependent
expressions, four-state values, partial cross references and concrete queue
tuples were not approximated. The interval-index batch below extends the static
integer-range case; dynamic and four-state cases still use the original matcher.
Indexed and fallback candidates are merged in
frozen IR order; overlapping bins retain multiple hits. Ignore/illegal priority,
default handling, transition histories, iff and private member views are
unchanged. Large overlapping member-hit spaces use an indexed-key scan rather
than expanding a Cartesian product larger than the declared indexed cross.

One uninstrumented normal-runtime benchmark on the same fixture sizes measured:

| Operation | Analysis baseline median | Indexed run |
|---|---|---|
| 10,000 samples, 256 point bins | 0.669 s | 0.0701 s |
| 10 samples, 65,536 cross bins | 0.188 s | 0.000447 s |
| First large-cross instantiate | 0.659 s | 0.812 s |

These are workload measurements, not universal speedup claims. Index construction
adds time and memory: a separately allocation-traced large-cross instantiate
peaked at 117,395,044 bytes (about 112 MiB), compared with the baseline about
97 MiB. Traced timings are not compared with normal-runtime timings. Snapshot
JSON/binary and static layout construction remain open optimization targets.

New differential tests compare each sample against a runtime using linear bin
candidates, including overlapping/duplicate set entries, range/dynamic fallback,
ignore/illegal suppression, defaults, iff, transition history, source records,
queue cross tuples and private member illegal records. A 65,536-bin fixture
agrees with the linear result while requiring fewer than ten cross matcher
calls per sample; this is a non-timing algorithmic regression gate.
Current local full suite: 702 passed, 36 remote tests deselected (38.98 s).
The initial remote full run passed 36 tests (160.87 s); it started before the
final overlap-work protection change and is not final-state qualification of
that change. All 23 remote coverage tests are being rerun on the final sampling
code and passed (94.27 s). No public interface/format change, commit or push.

### Follow-up implementation: database copying and serialization

JSON, binary and file serialization now borrow database-owned JSON record data
only during synchronous read-only encoding, instead of allocating a complete
public detached snapshot first. Public `snapshot_document()` still returns an
independent document. Its internal clone specializes plain dictionaries/lists
and immutable scalar values, retaining memoized aliases/cycles and falling back
to ordinary deepcopy for other types. No database ABI, identity or merge changes.

The large-cross benchmark's normal-runtime JSON serialization was 0.0821 s and
binary encoding 0.0938 s, versus the analysis baseline about 0.49 s each. Output
lengths remain unchanged. A same-database allocation-traced binary comparison
confirmed identical complete bytes, with peak Python allocations decreasing
from 104,897,910 to 49,583,150 bytes. Traced elapsed times (1.83 s and 0.83 s)
are not normal-runtime timing measurements.

Three clone-only repetitions on one large frozen record measured original
deepcopy at 0.327–0.332 s and the specialized document clone at 0.279–0.281 s.
This is a modest copy improvement, not elimination of the required public
snapshot allocation. Differential assertions verified equal full documents
and independent nested definition bins. Regressions also cover shared aliases,
cycles, non-JSON deepcopy fallback, exact JSON/binary bytes, file bytes, record
immutability after encoding, and mutation isolation of public snapshot metadata.

Static layout construction remains open. A direct whole-cross deepcopy
experiment was slower than existing materialization (0.514 s versus 0.423 s),
so that experiment was not adopted. Reusing mutable selector dictionaries across
instances is not an acceptable substitute for safe immutable-layout sharing.

Final-state full local regression passed 704 tests with 36 remote tests
deselected (39.73 s). All 23 remote coverage tests passed (95.61 s).
This batch does not close the remaining randomization design
or static-layout requirements. No commit or push.

### Follow-up implementation: arithmetic foreach indices

The earlier adjacent-reference declaration failure is repaired. Integer index
expressions are retained in typed IR and evaluated after dynamic sizes are
resolved. Known loop guards are simplified before expanding their bodies, so
`if i > 0` excludes `data[i - 1]` at zero without Python negative-index semantics.
SV output retains native signed-int index arithmetic. Index expressions depending
on random/state fields remain unsupported and report a declaration error.

The adjacent-index benchmark now performs real randomization, constrains its
length and first element, and independently checks the recurrence result; it is
no longer a declaration-only probe. Ten calls with length 16 and seed 601 had
median 0.0250 s and maximum 0.0391 s, producing exactly `1..16` with one registered
host. One distinct output is expected for this fully determined fixture and is
not a diversity measurement.

Python tests cover dynamic arrays, queues, fixed arrays, parent/child joint solve,
empty/singleton containers, compound guards, multiplication and unary indices,
and stable-IR distinction between different index expressions. A generated-SV
remote test passed 16 repetitions for each of the three collection fixtures.
The final full local suite passed 712 tests, with 37 remote tests deselected
(38.77 s). The complete remote suite passed 37 tests, with 712 local tests
deselected (169.68 s). The focused benchmark/index suite also passed 13 tests.
Resource-budget and uniform/weighted sampling proposals remain awaiting approval;
this batch does not establish 2.0 readiness. No commit or push.

### Follow-up implementation: static layout validation and cloning

IR construction previously called `canonical_value()` for validation and then
discarded its recursively allocated, sorted JSON-compatible result. A private
validation-only traversal now checks the same accepted types and string mapping
keys, without constructing that unused copy. Public canonical serialization,
stable dictionaries and digests continue to use the original canonicalizer.
Invalid values retain their TypeError diagnostics.

Materializing exact static `cross_bin_refs` dictionaries clones their reference
list and each member dictionary directly. The shape guard admits only string
point/bin references; all other shapes, extensions, constructor expressions,
Parameter references and queue functions retain general materialization. This
does not share mutable selectors across instances and does not cache layouts.

Five alternating, uninstrumented runs on the same frozen 65,536-bin template
measured materialization median 0.4370 s with the former validation/copy paths
and 0.3942 s with both optimizations (about 10% reduction). This measures layout
materialization, not end-to-end declaration compilation, sampling-index building,
or a universal performance bound. Large-cross construction still has substantial
linear allocation cost; immutable-layout sharing has not been introduced.

Four new regression checks cover canonical acceptance/error equivalence,
validation without discarded copies, unchanged layout snapshots/digests,
per-instance selector mutation isolation, and general-selector fallback.
The focused IR/sampling suite passed 14 tests. Repeatedly materializing and
discarding five large layouts returned the number of live CoverageBinIR objects
to the baseline 132,352 after each collection, rather than retaining new bins.
The complete coverage-optimization state passed 753 tests including all remote
tests (206.95 s). This run precedes the additional fixed-loop integer correction
below and is not its final-state qualification. No commit, push or public
coverage schema/API change.

### Follow-up correction: expanded fixed-loop integer arithmetic

A further probe found that fixed-array loop constants still used minimum bit
widths on predicate right-hand sides: the known-SAT combination of
`data[-(-i)] == i + 1` and `data[i * 2 + 1] == i * 2 + 2` incorrectly reported
UNSAT. Expanded loop variables now retain signed 32-bit integer types, as
symbolic foreach indices do. Arithmetic type inference respects explicit typed
integer widths/signs; expanded constant arithmetic folds within that integer
domain before emitting fixed-array constraints. It does not evaluate user code
or random/state expressions.

The fixed compound fixture now solves to `1..8`; the remote generated-code test
checks it alongside dynamic-array, queue and fixed-array recurrences for 16
repetitions. The focused local constraint suite passed 11 tests and the focused
remote test passed. The final combined full regression passed 754 tests
(216.08 s), including all configured remote tests and without skips/xfails.

A separate deterministic statistical check drew 100,000 indices for three
integer weights summing to a 194-bit total, approximately in the ratio 1:2:3.
Observed counts were 16,697 / 33,386 / 49,917 versus expected approximately
16,667 / 33,333 / 50,000; deviations were all below 0.53 standard deviations.
The first 1,000 draws replayed exactly under the same seed. This checks the
arbitrary-width integer selection primitive only; it does not qualify correlated
`dist` sampling above the model-enumeration boundary or the replacement for
deterministic feasible-model fallback. Those changes awaited approval at this
measurement point; the approved implementation and subsequent qualification
are recorded below. No commit or push.

### Follow-up implementation: static point-range indexes

Frozen integer ranges and fully static unions of ranges/values now use a private
balanced interval tree. Intersecting/adjacent pieces within one bin are merged
for integer lookup so one bin cannot be emitted twice. Lookup keeps every
overlapping bin and sorts candidates back into frozen IR order; ignore/illegal
priority, cross-local member classification, default handling and scoring still
use the existing classifier. Wide ranges are never expanded into value tables.
Dynamic/open/noninteger endpoints and noninteger/four-state sampled values keep
the original matcher. No public interface, identity or database format changes.

A 4,096-static-range point was sampled 1,000 times per run in three alternating
uninstrumented comparisons. The former linear candidate runtime measured median
1.6592 s, versus 0.002240 s indexed. This is a sparse-range workload result,
not a general speedup guarantee: many genuinely overlapping matches still require
processing all their hits, and dynamic fallback bins remain linear work.

Allocation-traced runtime construction on the same point retained 421,352 bytes
using the former singleton-index/range-fallback path, versus 1,438,832 bytes
with the interval tree (peak 1,574,744 bytes). That is approximately 1 MiB extra
retained index storage, excluding the pre-existing declaration IR. The index
trades construction memory for lookup throughput; it is not a memory reduction.

Differential tests cover overlapping ranges, noncontinuous unions, duplicate
pieces, signed endpoints, ignore/illegal suppression, defaults, dynamic endpoint
fallback, floating samples and four-state samples. A 4,096-range plus 256-bit-wide
common-range fixture matches linear snapshots while requiring at most two
selector matches per sample, without domain enumeration. An additional generated
layout experiment made 6,090 ordered-match comparisons over 30 layouts of 80
range/union bins; all agreed exactly. The focused suite passed four tests.
Complete final-state regression passed 756 tests (209.13 s), including all
configured remote tests, with no skips/xfails. Strategy and budget approval was
outstanding at that checkpoint; see the subsequent implementation below.
No commit or push.

### Approved follow-up: uniform sampling and per-call solve budgets

The user approved the strategy in randomization-foundation §7 on 2026-10-10.
The new sampler separates feasible-model proof from public random assignment.
It decomposes independent components (including links through existential
terms), completely enumerates small/sparse projections, and otherwise uses
uniform proposals with rejection. Identical-domain `unique` groups use uniform
sampling without replacement; equal-width/type aliases have one completion per
representative. Disabled leaves and previously selected values are excluded
from the remaining proposal domain. Model lists are sorted before drawing to
avoid backend enumeration order changing seed replay.

`solve_before` selects joint before layers, not a sequence of fields in the
same layer or a prematurely frozen terminal layer. Randc projections precede
ordinary completions. A distribution whose inputs are available is applied in
its projection stage; completion rejection cannot resample that projection or
double-apply its weight. Sparse support events receive random feasible
completions rather than minimum witnesses. General distribution enumeration
can switch to weighted feasible proposals before the old 4,096 threshold;
weights are never discarded at that boundary. Rejection uses arbitrary-width
integer draws, without 64-bit probability quantization.

The complete normal solve shares `RandomContext.solve_timeout_ms` (default
30,000) and `solve_check_limit` (default 100,000). Each accepts a nonnegative
integer or `None`; zero exhausts that protection immediately. Backend checks,
soft decisions, enumeration, graph and dynamic stages share the budget.
`timeout`, `resource_limit`, backend `unknown`, and proved `unsat` remain
distinct. Failure status adds optional phase/reason diagnostics. Failed trials
publish neither values nor randc history, do not run successful post hooks,
and retain established layered restoration semantics. Hooks and cleanup do not
have a strict real-time deadline. Runtime budgets do not alter source schema,
constraint digest, or generated SV.

New statistical evidence (deterministic seeds, not formal probability proof):

- An asymmetric four-model space produced 183 / 212 / 207 / 198 hits in 800
  draws, against 200 each. Its branches contain one and three complete models;
  uniform branch selection would give a different result.
- A correlated 8,192-model distribution produced 66 lower-half and 174
  upper-half hits in 240 draws, against 60 / 180 for its 1:3 weights. There
  were 235 distinct outputs; the first 20 replayed exactly. These are successful
  weighted samples beyond 4,096, not a rejection-only test.
- Separate tests check before-group joint probabilities, a before field's own
  weights, branch-conditional completion weights, and randc projections with
  unequal completion counts. Injected UNKNOWN, a deterministic fake deadline,
  zero/low budgets, real UNSAT, dynamic rollback, and layered mode/history
  restoration are separate failure-path qualification.

Scale runs used 12 independently checked successful calls per row, seed 23063.
Times are local medians under concurrent verification load, not release limits.

| Fixture | Size | Distinct outputs / 12 | Median seconds |
|---|---:|---:|---:|
| permutation | 4 | 10 | 0.0176 |
| permutation | 8 | 12 | 0.0706 |
| permutation | 16 | 12 | 0.1430 |
| permutation | 32 | 12 | 0.3959 |
| graph permutation | 4 | 9 | 0.0183 |
| graph permutation | 8 | 12 | 0.0705 |
| graph permutation | 16 | 12 | 0.1599 |
| graph permutation | 32 | 12 | 0.3814 |
| fixed-value dynamic chain | 128 | 1 (only legal output) | 0.3567 |
| nonlinear factors | 128 | 2 | 0.0259 |
| random-dependent distribution | 128 | 11 | 0.0317 |
| adjacent-index recurrence | 16 | 1 (only legal output) | 0.0190 |

The 128-value distribution still has a 191-bit integer normalization total;
its successful measured median improves on the previous approximately 2.18 s
probe. Every scale fixture preserves its checked values, object identities and
expected registration counts. These bounded results do not promise practical
solution of every arbitrary-width nonlinear relation; resource failure is a
diagnostic, not successful sampling acceptance.

After 500 dynamic calls, both coverage-enabled and coverage-disabled probes
retain exactly one registered object. Post-GC retained Python allocation from
call 1 to call 500 changed from 829,284 to 830,030 bytes with coverage, and
19,335 to 20,143 bytes without coverage. This is a warmed plateau, not the
previous per-call owner-registration growth. New projection caches are local
to one solve, not shared mutable instance layouts or persistent model pools.

The focused final combination run passed 59 tests, including its configured
remote signed-domain and 32-element unique execution. Final whole-project
regression after the large-timeout boundary fix passed **782 tests in 264.09 s**, including all configured remote
tests, with no skips or xfails. This closes the identified performance/sampling
remediation goal, not the 2.0 release freeze. No commit or push.

### Inline constraint cache lifetime follow-up (2026-10-10)

`randomize_with()` now caches compiled IR by a weak live-function key and
receiver class, rather than `(id(function), class)`. Reused function addresses
can no longer select a discarded function's constraints. Five new regressions
cover identity reuse, repeated live-function hits, receiver-type isolation,
temporary-function/payload collection and SAT/UNSAT separation. Related
randomization controls passed 77 tests. The latest complete regression passed
**787 tests in 327.57 s**, including all configured remote tests, with no skips
or xfails. Original external integration positive and negative gates also
passed with normal caching enabled. This is correctness qualification, not
a new timing comparison or release qualification; no commit or push.
