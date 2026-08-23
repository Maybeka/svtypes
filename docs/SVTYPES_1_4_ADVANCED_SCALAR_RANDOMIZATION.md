# SvTypes 1.4 Advanced Scalar Randomization Design

## Scope

1.4 adds the SystemVerilog scalar constrained-random constructs that need
solver-policy support in addition to ordinary Boolean predicates: `dist`,
`randc`, `soft`, `solve before`, and `unique`.  The work is deliberately
independent of container randomization (1.5) and non-null object-handle
participation (1.6).

This document records the accepted scalar interfaces and their semantic
contracts.  `soft` and `solve before` remain pending the shared solver-policy
layer.

## `dist` source interface

Import the source-only marker and place a distribution as a complete
constraint statement:

```python
from svtypes import constraint, dist

@constraint
def legal(self):
    (self.opcode + self.offset) @ dist[
        MODE_A @ self.weight_a,
        (self.min_addr, self.max_addr - 1) @ 2,
        (self.limit - 1, self.limit) / self.range_weight,
        MODE_IDLE,
    ]
```

It renders as:

```systemverilog
(opcode + offset) dist {
    MODE_A := weight_a,
    [min_addr : max_addr - 1] := 2,
    [limit - 1 : limit] :/ range_weight,
    MODE_IDLE := 1
};
```

The outer `@` is DSL syntax only; a constraint method is parsed rather than
executed.  `dist` is exported for explicit imports and type-checker visibility,
but has no general runtime operation.

Python `@` has multiplication-level precedence, so a compound left expression
must be parenthesized.  The left expression, item values, range bounds, and
weights all accept the supported integral constraint expression subset.  The
left expression must include at least one randomized solver leaf; normal type
resolution rejects non-integral expressions.

`value @ weight` means SV `value := weight`; `(lo, hi) @ weight` means
`[lo:hi] := weight`; and `(lo, hi) / total` means `[lo:hi] :/ total`.
Unweighted single values are accepted and mean `:= 1`.  A zero weight excludes
that item.  A statically negative weight is a declaration error; a dynamically
negative result is an invalid solve candidate and cannot be selected.

`dist` remains a complete constraint statement, matching the SV
`expression_or_dist` grammar.  It may be put in an ordinary constraint `if`
body, which controls whether that distribution is active.  Combining it with
Python `and`/`or` is rejected rather than given an accidental weighting rule.

## IR and execution semantics

The typed IR uses a Boolean `dist` expression with an immutable sequence of
items.  Each item records `low`, optional `high`, `weight`, and whether the
weight is per value (`:=`) or total across a range (`:/`).  It is included in
the stable constraint digest, rendered directly into generated SV, evaluated as
a hard membership constraint by the Python evaluator, and encoded as the same
membership restriction by the SMT backend.

For Python randomization, a satisfying candidate is accepted according to the
product of its active distribution weights.  Range-total weights are divided
exactly by the number of values in their inclusive range; the implementation
uses rational arithmetic and a 64-bit rejection draw, not floating point.  This
makes `:=` and `:/` observably distinct while preserving deterministic seeded
execution.

When direct sampling is impractical for a sparse, wide distribution made only
of single values whose bounds and weights are already state-resolvable, the
Python path proves every support value SAT and selects among the satisfiable
values by the exact declared weights.  This avoids the former minimum-model
bias for the common case `wide_field @ dist[VALUE_A @ 1, VALUE_B @ 3]`.

The general SMT fallback remains a hard-satisfiability fallback for sparse
ranges, multiple interacting distributions, and distribution bounds/weights
that depend on unsolved leaves.  The later unified 1.4 solver-policy pass will
extend exact weighted model selection to those shapes.

## Restrictions and future integration

- `dist` on a future `randc` target will be rejected, following SV.
- The current scalar implementation supports integral Bit, Logic, and Enum
  leaves; collection, dynamic-size, and handle targets belong to later
  milestones.
- Dynamic expressions are represented in IR and rendered to SV without Python
  pre-evaluation.  Exact sparse fallback already covers state-resolvable
  singleton distributions; dynamic ranges and interacting distributions remain
  part of the pending solver-policy work.
- `soft`, `solve before`, `unique`, and `randc` must share the solver-policy
  layer so their priority/order behavior cannot silently alter this interface.

## `randc` semantic design

The field-policy spelling is `randc=True`, mutually exclusive with an explicit
`rand=` policy, and it renders the target declaration with SV `randc` rather
than `rand`.

Regardless of that spelling, the semantic contract is fixed as follows:

- It applies only to 2-state scalar integral leaves (`Bit`, `Int`, `LongInt`,
  and Enum).  Four-state `Logic`, containers, and object handles are rejected.
- Cycle state belongs to an object instance and the resolved scalar leaf path;
  it is not schema identity and is not serialized.
- At a successful `randomize()`, `randc` values are selected before ordinary
  `rand` values.  A value is not repeated until every currently legal value in
  its cycle has appeared.
- A failed randomization does not consume a cyclic value.  `rand_mode(0)`
  pauses the cycle; re-enabling resumes it.
- Changing the enabled constraint set that applies to a cyclic variable starts
  a new cycle.  If the current cycle has no remaining value satisfying the
  current constraints but the full domain is satisfiable, it also starts a new
  cycle.  This is the practical equivalent of SV's constraint-change and
  exhausted-remaining-value reset behavior.
- A `dist` expression whose left side includes a `randc` leaf is a declaration
  error.  `solve before` must likewise reject cyclic variables as ordering
  operands.

Python verification will use finite small domains to prove no repetition,
reset, constraint exhaustion, failure non-consumption, and mode pause/resume.
SystemVerilog target verification will generate the same `randc` declarations and assert the
same observable cycle invariants; it will not assert identical random-number
streams across the two implementations.

## `unique` scalar interface

Use `unique(...)` as a complete constraint statement:

```python
from svtypes import constraint, unique

@constraint
def legal(self):
    unique(self.source, self.destination, self.reply)
```

It emits `unique {source, destination, reply};`.  It is a hard constraint:
fewer than two arguments are rejected, and no satisfying solve can contain a
duplicate scalar value.  The initial 1.4 implementation accepts scalar integral
expressions; array flattening and collection forms belong with 1.5.  Python
uses the same pairwise SV equality sizing rules as the SMT encoding, including
mixed-width operands.

## Verification

`tests/python/test_constraint_dist.py` covers source parsing, expression
positions, stable hard membership, `:=` versus `:/` sampling, and invalid
source.  `tests/python/test_constraint_sv.py::test_remote_target_dist_expression_simulation`
generates the same IR to SV, compiles it with SystemVerilog target, and checks the resulting
support set over repeated target-language randomizations.

`tests/python/test_constraint_randc.py` covers Python cycle uniqueness, mode
pause/resume, constrained-cycle reset, failed-call non-consumption, generated
declarations, and invalid combinations.  The matching SystemVerilog target regression is
`tests/python/test_constraint_sv.py::test_remote_target_randc_cycle_simulation`.

`tests/python/test_constraint_unique.py` covers scalar and mixed-width Python
semantics; `test_remote_target_unique_scalar_simulation` verifies the generated
constraint with SystemVerilog target.
