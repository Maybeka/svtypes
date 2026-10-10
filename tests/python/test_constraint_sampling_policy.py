from __future__ import annotations

import copy
from collections import Counter

import pytest

from svtypes import Array, Bit, DynArray, Int, RandomContext, SvObject, constraint, dist, rand_layer, soft, solve_before, unique
from svtypes.constraint import budget
from svtypes.constraint.backend.model import SolveResult
from svtypes.constraint.backend.sampling import sample
from svtypes.constraint.backend.model import SolveRequest
from svtypes.constraint.sample import BitStream


class NativeSigned(SvObject):
    x = Int(rand=True)
    y = Int(rand=True)
    low = Int(-3, rand=False)
    high = Int(3, rand=False)

    @constraint
    def legal(self):
        self.x >= self.low
        self.x <= self.high
        self.y == self.x


class NativePermutation(SvObject):
    data = Array[Bit[8], 32]()

    @constraint
    def legal(self):
        unique(self.data)
        for i in range(32):
            self.data[i] < 32


class Exact(SvObject):
    word = Bit[32]()
    hooks = []

    @constraint
    def legal(self):
        self.word == 0x12345678

    def post_randomize(self):
        self.hooks.append("post")


@pytest.mark.parametrize("name", ["solve_timeout_ms", "solve_check_limit"])
def test_budget_parameters_copy_validation_and_zero(name):
    for value in (-1, True, 1.5, "1"):
        with pytest.raises(ValueError):
            RandomContext(**{name: value})
    context = RandomContext(seed=1, **{name: 0})
    assert getattr(copy.copy(context), name) == 0
    assert getattr(copy.deepcopy(context), name) == 0
    obj = Exact()
    obj.word.value = 42
    obj.hooks.clear()
    with context:
        assert not obj.randomize()
    status = obj.svtypes_randomize_status
    assert status.reason == ("timeout" if name == "solve_timeout_ms" else "resource_limit")
    assert status.failure_phase
    assert obj.word.value == 42 and not obj.hooks
    with RandomContext(seed=1, solve_timeout_ms=None, solve_check_limit=None):
        assert obj.randomize()
    assert obj.svtypes_randomize_status.failure_phase is None


def test_backend_unknown_is_not_unsat_or_soft_conflict(monkeypatch):
    import z3
    class Preferred(Exact):
        @constraint
        def preferred(self):
            soft(self.word == 0x12345678)

    obj = Preferred()
    obj.word.value = 91
    monkeypatch.setattr(z3.Solver, "check", lambda self, *args: z3.unknown)
    monkeypatch.setattr(z3.Solver, "reason_unknown", lambda self: "injected incomplete backend")
    with RandomContext(seed=1):
        assert not obj.randomize()
    assert obj.word.value == 91
    assert obj.svtypes_randomize_status.reason == "unknown"
    assert obj.svtypes_randomize_status.backend_reason == "injected incomplete backend"
    result = SolveResult.unknown("incomplete")
    assert not result.is_sat and result.reason == "unknown"


def test_backend_timeout_reason_and_deadline_are_distinct_from_unsat(monkeypatch):
    import z3
    obj = Exact()
    monkeypatch.setattr(z3.Solver, "check", lambda self, *args: z3.unknown)
    monkeypatch.setattr(z3.Solver, "reason_unknown", lambda self: "timeout")
    with RandomContext(seed=1):
        assert not obj.randomize()
    assert obj.svtypes_randomize_status.reason == "timeout"


def test_budget_spans_dynamic_size_and_element_solvers():
    class Dynamic(SvObject):
        data = DynArray[Bit[32]](max_length=4)

        @constraint
        def legal(self):
            self.data.size() == 3
            for i in range(self.data.size()):
                self.data[i] == 0x12345678

    obj = Dynamic()
    obj.data.value = [71, 72]
    with RandomContext(seed=1, solve_check_limit=35):
        assert not obj.randomize()
    assert obj.svtypes_randomize_status.reason == "resource_limit"
    assert list(obj.data.value) == [71, 72]
    with RandomContext(seed=1):
        assert obj.randomize()
    assert list(obj.data.value) == [0x12345678] * 3


def test_budget_is_shared_and_unknown_never_completes_enumeration(monkeypatch):
    import z3
    calls = 0
    original = z3.Solver.check
    def checking(self, *args):
        nonlocal calls
        calls += 1
        return z3.unknown if calls == 3 else original(self, *args)
    monkeypatch.setattr(z3.Solver, "check", checking)
    monkeypatch.setattr(z3.Solver, "reason_unknown", lambda self: "injected enumeration interruption")
    class Tiny(SvObject):
        word = Bit[2]()
        @constraint
        def legal(self):
            self.word < 3
    ir = Tiny._SvObject__svtypes_constraint_irs["legal"]
    request = SolveRequest((ir,), ("word",), {}, {v.path: v for v in ir.vars})
    with budget.solving(None, 20):
        with pytest.raises(budget.SolveFailure, match="unknown"):
            sample(request, BitStream(1))


@pytest.mark.parametrize("size", [4, 8, 16, 32])
def test_uniform_permutations_are_diverse_and_replayable(size):
    class Permutation(SvObject):
        count = Bit[8](rand=False)
        data = DynArray[Bit[8]](max_length=32)
        @constraint
        def legal(self):
            unique(self.data)
            self.data.size() == self.count
            for i in range(self.data.size()):
                self.data[i] < self.count
    def run():
        obj = Permutation()
        obj.count.value = size
        values = []
        with RandomContext(seed=401):
            for _ in range(12):
                assert obj.randomize()
                value = tuple(obj.data.value)
                assert sorted(value) == list(range(size))
                values.append(value)
        return values
    first = run()
    assert first == run()
    assert len(set(first)) >= (7 if size == 4 else 11)


def test_uniform_small_asymmetric_space_and_soft_priority():
    class Asymmetric(SvObject):
        gate = Bit[1]()
        word = Bit[2]()
        @constraint
        def legal(self):
            if self.gate == 0:
                self.word == 0
            else:
                self.word < 3
    obj = Asymmetric()
    ir = Asymmetric._SvObject__svtypes_constraint_irs["legal"]
    request = SolveRequest((ir,), ("gate", "word"), {}, {v.path: v for v in ir.vars})
    stream = BitStream(811)
    counts = Counter()
    for _ in range(800):
        result = sample(request, stream)
        counts[tuple(result.assignments[p] for p in request.random_paths)] += 1
    assert set(counts) == {(0, 0), (1, 0), (1, 1), (1, 2)}
    assert all(150 <= count <= 250 for count in counts.values())


def test_distribution_above_4096_models_keeps_weights_and_replays():
    class LargeDependent(SvObject):
        x = Bit[13]()
        y = Bit[13]()
        @constraint
        def legal(self):
            self.x == self.y
            self.x @ dist[(0, 4095) @ 1, (4096, 8191) @ (self.y * 0 + 3)]
    def run(calls):
        obj = LargeDependent()
        output = []
        with RandomContext(seed=1201):
            for _ in range(calls):
                assert obj.randomize(), obj.svtypes_randomize_status
                assert obj.x.value == obj.y.value
                output.append(obj.x.value)
        return output
    first = run(240)
    assert first[:20] == run(20)
    assert len(set(first)) > 200
    assert 150 < sum(x >= 4096 for x in first) < 210


def test_dist_support_sampling_does_not_discard_global_soft():
    class Preferred(SvObject):
        word = Bit[32]()
        @constraint
        def legal(self):
            self.word @ dist[0x12345678 @ 1, 0x9abcdef0 @ 9]
            soft(self.word == 0x12345678)
    obj = Preferred()
    with RandomContext(seed=9):
        for _ in range(5):
            assert obj.randomize()
            assert obj.word.value == 0x12345678


def test_unique_proposal_preserves_disabled_element_modes():
    class Fixed(SvObject):
        data = Array[Bit[8], 32]()
        @constraint
        def legal(self):
            unique(self.data)
            for i in range(32):
                self.data[i] < 32
    obj = Fixed()
    obj.data[0].value = 3
    obj.data[0].rand_mode(0)
    values = []
    with RandomContext(seed=4):
        for _ in range(5):
            assert obj.randomize()
            assert obj.data[0].value == 3 and obj.data[0].rand_mode() == 0
            values.append(tuple(obj.data.value))
            assert sorted(values[-1]) == list(range(32))
    assert len(set(values)) == 5


def test_solve_before_unique_alias_keeps_conditional_uniform_completion():
    class Ordered(SvObject):
        tag = Bit[8]()
        data = Array[Bit[8], 16]()
        @constraint
        def legal(self):
            self.tag == self.data[0]
            unique(self.data)
            for i in range(16):
                self.data[i] < 16
            solve_before(self.tag, self.data[1])
    obj = Ordered()
    with RandomContext(seed=17):
        for _ in range(5):
            assert obj.randomize(), obj.svtypes_randomize_status
            assert sorted(obj.data.value) == list(range(16))
            assert obj.tag.value == obj.data[0].value


def test_signed_narrow_domain_crossing_zero_is_sampled_as_numeric_interval():
    obj = NativeSigned()
    observed = set()
    with RandomContext(seed=47):
        for _ in range(70):
            assert obj.randomize(), obj.svtypes_randomize_status
            assert obj.x.value == obj.y.value and -3 <= obj.x.value <= 3
            observed.add(obj.x.value)
    assert observed == set(range(-3, 4))


def test_failed_budget_does_not_consume_randc_history():
    class Cycle(Exact):
        choice = Bit[2](randc=True)
    obj = Cycle()
    with RandomContext(seed=1):
        assert obj.randomize()
    before = copy.deepcopy(obj._SvObject__svtypes_randc_state)
    values = (obj.word.value, obj.choice.value)
    with RandomContext(seed=1, solve_check_limit=0):
        assert not obj.randomize()
    assert obj.svtypes_randomize_status.reason == "resource_limit"
    assert obj._SvObject__svtypes_randc_state == before
    assert (obj.word.value, obj.choice.value) == values


def test_layered_budget_failure_reports_layer_and_restores_modes():
    class Layered(SvObject):
        flag = Bit[1]()
        word = Bit[32]()
        @constraint
        def high_rule(self):
            self.flag == 1
        @constraint
        def low_rule(self):
            self.word == 0x12345678
        @rand_layer(10)
        def high(self):
            self.flag
            self.high_rule
        @rand_layer(-10)
        def low(self):
            self.word
            self.low_rule
    obj = Layered()
    obj.high_rule.constraint_mode(0)
    obj.word.value = 51
    with RandomContext(seed=9, solve_check_limit=0):
        assert not obj.layered_randomize()
    status = obj.svtypes_layered_randomize_status
    assert status.reason == "resource_limit" and status.failed_priority == -10
    assert obj.flag.value == 1 and obj.word.value == 51
    assert obj.high_rule.constraint_mode() == 0
    assert obj.flag.rand_mode() == 1 and obj.word.rand_mode() == 1


def test_ordered_dist_rejects_only_completion_not_ordered_projection():
    class OrderedDist(SvObject):
        gate = Bit[1]()
        word = Bit[32]()
        @constraint
        def legal(self):
            if self.gate == 0:
                self.word @ dist[0x12345678 @ 1, 0x12345679 @ 3]
            else:
                self.word @ dist[0xabcdef00 @ 1]
            solve_before(self.gate, self.word)
    obj = OrderedDist()
    counts = Counter()
    with RandomContext(seed=523):
        for _ in range(240):
            assert obj.randomize(), obj.svtypes_randomize_status
            counts[(obj.gate.value, obj.word.value)] += 1
    zeros = sum(count for (gate, _), count in counts.items() if gate == 0)
    assert 90 < zeros < 150
    assert counts[(0, 0x12345679)] > counts[(0, 0x12345678)] * 2


def test_randc_projection_is_not_weighted_by_completion_count():
    class Cycle(SvObject):
        cycle = Bit[2](randc=True)
        word = Bit[3]()
        @constraint
        def legal(self):
            if self.cycle == 0:
                self.word == 0
    counts = Counter()
    for seed in range(240):
        obj = Cycle()
        with RandomContext(seed=seed):
            assert obj.randomize()
        counts[obj.cycle.value] += 1
    assert all(40 < count < 85 for count in counts.values())


def test_deadline_after_backend_sat_does_not_publish_or_call_post(monkeypatch):
    import z3
    clock = [0.0]
    monkeypatch.setattr(budget, "monotonic", lambda: clock[0])
    original = z3.Solver.check
    def delayed(self, *args):
        result = original(self, *args)
        clock[0] += 1
        return result
    monkeypatch.setattr(z3.Solver, "check", delayed)
    obj = Exact()
    obj.word.value = 79
    obj.hooks.clear()
    with RandomContext(seed=1, solve_timeout_ms=10):
        assert not obj.randomize()
    assert obj.word.value == 79 and not obj.hooks
    assert obj.svtypes_randomize_status.reason == "timeout"
    assert obj.svtypes_randomize_status.failure_phase == "backend_check"


def test_before_variable_keeps_its_own_distribution_weights():
    class WeightedBefore(SvObject):
        first = Bit[1]()
        second = Bit[32]()
        @constraint
        def legal(self):
            self.first @ dist[0 @ 1, 1 @ 3]
            self.second == self.first
            solve_before(self.first, self.second)
    obj = WeightedBefore()
    ones = 0
    with RandomContext(seed=617):
        for _ in range(160):
            assert obj.randomize()
            assert obj.first.value == obj.second.value
            ones += obj.first.value
    assert 100 < ones < 140


def test_same_before_group_is_a_joint_projection_not_sequential_fields():
    class Grouped(SvObject):
        gate = Bit[1]()
        word = Bit[2]()
        tail = Bit[1]()
        @constraint
        def legal(self):
            if self.gate == 0:
                self.word == 0
            else:
                self.word < 3
            solve_before((self.gate, self.word), self.tail)
    obj = Grouped()
    counts = Counter()
    with RandomContext(seed=619):
        for _ in range(360):
            assert obj.randomize()
            counts[(obj.gate.value, obj.word.value)] += 1
    assert set(counts) == {(0, 0), (1, 0), (1, 1), (1, 2)}
    assert all(60 < count < 120 for count in counts.values())


@pytest.mark.parametrize("timeout_ms", [1 << 48, 10**400])
def test_large_runtime_timeout_does_not_wrap_backend_option(timeout_ms):
    import z3
    class Solver:
        timeout = None
        def set(self, *, timeout):
            self.timeout = timeout
        def check(self):
            return z3.sat
    solver = Solver()
    with budget.solving(timeout_ms, None):
        assert budget.check(solver, z3) == z3.sat
    assert solver.timeout in (None, (1 << 32) - 1)


@pytest.mark.remote_sv
def test_remote_signed_domain_and_structured_unique():
    import os
    from pathlib import Path
    from test_constraint_sv import _run_remote

    out = Path(__file__).resolve().parents[2] / ".tmp" / "sampling_policy_sv"
    out.mkdir(parents=True, exist_ok=True)
    (out / "definitions.sv").write_text("\n".join([
        "package sampling_policy_sv;", "import svtypes_pkg::*;",
        NativeSigned.to_sv_obj(level=1), NativePermutation.to_sv_obj(level=1), "endpackage",
    ]))
    (out / "tb.sv").write_text("""module tb;
  import sampling_policy_sv::*;
  initial begin
    NativeSigned s;
    NativePermutation p;
    bit [31:0] seen;
    bit found_negative;
    bit found_nonnegative;
    s = new(); p = new();
    s.low = -3; s.high = 3;
    repeat (32) begin
      if (!s.randomize() || !p.randomize()) $fatal(1, "sampling policy randomize failed");
      if (s.x < -3 || s.x > 3 || s.y != s.x) $fatal(1, "signed comparison mismatch");
      if (s.x < 0) found_negative = 1;
      else found_nonnegative = 1;
      seen = 0;
      foreach (p.data[i]) begin
        if (p.data[i] >= 32 || seen[p.data[i]]) $fatal(1, "permutation mismatch");
        seen[p.data[i]] = 1;
      end
      if (seen != '1) $fatal(1, "permutation incomplete");
    end
    if (!found_negative || !found_nonnegative) $fatal(1, "signed support incomplete");
    $display("SVTYPES_SAMPLING_POLICY_PASS");
    $finish;
  end
endmodule
""")
    result = _run_remote(os.environ["SVTYPES_REMOTE_SV_HOST"],
        "bash -ilc 'cd $SVTYPES_REMOTE_SV_ROOT/.tmp/sampling_policy_sv && "
        "$SVTYPES_REMOTE_SV_RUNNER compile definitions.sv tb.sv && $SVTYPES_REMOTE_SV_RUNNER run'")
    assert result.returncode == 0, "remote sampling-policy execution failed"
    assert "SVTYPES_SAMPLING_POLICY_PASS" in result.stdout + result.stderr
