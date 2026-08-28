# SvTypes 1.4 Advanced Scalar Randomization Design

## Scope

1.4 adds the SystemVerilog scalar constrained-random constructs that need
solver-policy support in addition to ordinary Boolean predicates: `dist`,
`randc`, `soft`, `solve before`, and `unique`.  The work was deliberately
separated from container randomization (1.5) and non-null object-handle
participation (1.6); those later features now reuse the same IR.

This document records the accepted scalar interfaces and their semantic
contracts.

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

For state-resolvable direct distributions, Python does not immediately turn a
large declared range into a minimum SMT model merely because it exceeds the
4096-combination enumerator.  Below that limit it proves every support
combination SAT and selects among the satisfiable combinations by the product
of their exact declared weights.  Beyond it, Python draws each direct `:=`/`:/`
item by its exact total mass, draws a value uniformly within the selected
range, and uses SAT as rejection sampling for the resulting combination.  This
covers singleton choices, finite and wide ranges, `:/` total-range weights,
and multiple direct distributions that constrain one another without
materializing every range member.  If bounded rejection cannot find a feasible
combination, the general bounded model policy below remains the resource
boundary.

Conditional distributions and bounds/weights that depend on other constrained
random leaves use a second exact policy: Python enumerates complete constrained
models through the same 4096-model limit, applies each active branch's
selected/total weight, and chooses a model by the resulting rational weight.
The general SMT fallback remains a hard-satisfiability fallback only when that
bounded model space is exceeded; those large shapes must not be presented as
having an exact Python frequency contract.

## Restrictions and future integration

- `dist` on a `randc` target is rejected, following SV.
- The source form remains scalar-oriented.  After runtime expansion, a
  distribution over a current dynamic-collection element or an already
  allocated rand-handle leaf uses the same IR and solver path.
- Dynamic expressions are represented in IR and rendered to SV without Python
  pre-evaluation. State-resolvable direct support ranges use exact weighted
  draws without a support-materialization limit; conditional distributions and
  bounds/weights depending on other random leaves use bounded complete-model
  enumeration (4096 models).
- `soft`, `solve before`, `unique`, and `randc` share the runtime solve path;
  their declared priority/order behavior is covered independently of weighted
  distribution selection.

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
Target verification will generate the same `randc` declarations and assert the
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
a scalar-only form needs at least two arguments, and no satisfying solve can
contain a duplicate value.  Unpacked `Array`, `DynArray`, and `Queue` arguments
are part of the same construct: `unique(self.words)` and
`unique(self.tag, self.data)` emit native `unique {words};` /
`unique {tag, data};`.  Packed `Bit`/`Logic` remain one integral value.
`unique` does not constrain collection size; empty or singleton collections are
vacuously true.  Associative arrays, object handles, unpacked structs, and
nested dynamic collections are rejected at declaration.  Python flattens
current integral leaves after size is known and uses the same pairwise SV
equality sizing rules as the SMT encoding, including mixed-width operands.

## `soft` interface and priority

Use `soft(expression)` as a complete constraint statement:

```python
from svtypes import constraint, soft

@constraint
def legal(self):
    self.opcode < 3
    soft(self.opcode == 2)
```

It renders as `soft (opcode == 32'd2);`.  A soft expression never makes a
solve fail: it is retained only when it remains consistent with all hard
constraints and every earlier, higher-priority soft expression.  Python and
the SMT backend apply soft expressions greedily in this order:

1. inline `randomize_with` soft constraints;
2. derived-class constraint blocks before base-class blocks;
3. later soft statements before earlier statements in one block.

This order is validated against the configured target.  A `soft` statement inside a constraint
`if` is active only in the selected branch.  Python candidate sampling never
commits a merely best-effort candidate when a soft clause was missed; it uses
the incremental SMT policy to distinguish a real conflict from an unlucky
sample.

## `solve_before` interface

Use `solve_before(before, after)` as a complete constraint statement.  Each
argument is either one scalar random field or a tuple/list of scalar random
fields:

```python
from svtypes import constraint, solve_before

@constraint
def legal(self):
    solve_before((self.kind, self.length), self.payload_kind)
    self.kind < self.payload_kind
```

It emits `solve kind, length before payload_kind;`.  The groups cannot be
empty or overlap; members must be scalar `rand` leaves, and `randc` members or
conditional placement are rejected.  Cyclic ordering edges are reported at
randomization time.

The Python backend treats this as a selection-order policy, never as a hard
constraint.  For each ordered variable whose packed domain fits within 4096
values, it enumerates feasible values, selects one from the deterministic
random stream, fixes it, and proceeds to the next ordered variable.  For wider
domains, it fixes the ordered variable one satisfiable bit at a time using the
same stream, then proceeds to the next variable.  Thus a wide ordered field
does not degrade to the deterministic minimum model.  The large-domain path is
deliberately not a uniform model-counting claim; `solve before` controls
selection order, not a probability contract.

## Verification

`tests/python/test_constraint_dist.py` covers source parsing, expression
positions, stable hard membership, `:=` versus `:/` sampling, and invalid
source.  `tests/python/test_constraint_sv.py::test_remote_sv_dist_expression_simulation`
generates the same IR to SV, compiles it on the target, and checks the resulting
support set over repeated target-language randomizations.

`tests/python/test_constraint_randc.py` covers Python cycle uniqueness, mode
pause/resume, constrained-cycle reset, failed-call non-consumption, generated
declarations, and invalid combinations.  The matching target regression is
`tests/python/test_constraint_sv.py::test_remote_sv_randc_cycle_simulation`.

`tests/python/test_constraint_unique.py` covers scalar and mixed-width Python
semantics plus unpacked array/queue flattening; `test_remote_sv_unique_scalar_simulation`
and `test_remote_sv_unique_collection_simulation` verify scalar, fixed-array,
dynamic-array, and queue constraints with the target.

`tests/python/test_constraint_soft.py` covers hard-over-soft behavior,
same-block and inheritance priority, and conditional activation.  The target soft
regression verifies all of those ordering cases in generated SystemVerilog.

`tests/python/test_constraint_solve_before.py` covers groups, rendering, and
invalid operands/cycles.  The matching target regression compiles and executes
the generated `solve ... before ...` declaration.
