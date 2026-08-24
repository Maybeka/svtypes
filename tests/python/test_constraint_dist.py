from __future__ import annotations

import pytest

from svtypes import Bit, ConstraintTypeError, DynArray, Object, RandomContext, SvObject, constraint, dist, get_package, svobj
from svtypes.constraint.eval import eval_bool
from svtypes.constraint.leaves import iter_class_leaves, leaf_unsigned, resolve_attr


class DistPacket(SvObject):
    choice = Bit(4)
    base = Bit(4, rand=False)
    weight = Bit(4, rand=False)

    @constraint
    def legal(self):
        (self.choice + self.base) @ dist[
            5 @ 3,
            (8, 10) / (self.weight + 1),
            12,
        ]


dist_handle_pkg = get_package("dist_handle_randomization")


@svobj(registry=dist_handle_pkg)
class DistHandleChild(SvObject):
    choice = Bit(2)


@svobj(registry=dist_handle_pkg)
class HandleDistPacket(SvObject):
    child = Object("DistHandleChild", registry=dist_handle_pkg, rand=True)

    @constraint
    def legal(self):
        self.child.choice @ dist[0 @ 1, 3 @ 3]


def _env_and_widths(obj: SvObject) -> tuple[dict[str, int], dict[str, tuple[int, bool]]]:
    env = {}
    widths = {}
    for path, desc, _declared in iter_class_leaves(type(obj)):
        target = resolve_attr(obj, path)
        env[path] = leaf_unsigned(target, target.value)
        widths[path] = (desc.width, bool(desc.signed))
    return env, widths


def test_dist_accepts_integral_expression_everywhere_and_renders_sv():
    obj = DistPacket()
    obj.base.value = 1
    obj.weight.value = 2
    ir = DistPacket._SvObject__svtypes_constraint_irs["legal"]
    assert ir.predicates[0].op == "dist"
    text = DistPacket.to_sv_obj()
    assert "dist {" in text
    assert ":=" in text
    assert ":/" in text
    assert "[" in text

    with RandomContext(seed=101):
        for _ in range(64):
            assert obj.randomize() is True
            assert obj.choice.value in {4, 7, 8, 9, 11}
            env, widths = _env_and_widths(obj)
            assert eval_bool(ir.predicates[0], env, widths)


def test_dist_range_each_and_total_have_observable_different_distributions():
    class Weighted(SvObject):
        choice = Bit(3)

        @constraint
        def legal(self):
            self.choice @ dist[
                (0, 3) @ 1,
                (4, 7) / 1,
            ]

    counts = [0] * 8
    obj = Weighted()
    with RandomContext(seed=202):
        for _ in range(800):
            assert obj.randomize() is True
            counts[obj.choice.value] += 1
    # := gives four values a unit weight each; :/ spreads one unit over four
    # values.  A broad margin makes this deterministic seeded regression robust.
    assert sum(counts[:4]) > 500
    assert sum(counts[4:]) < 300


def test_sparse_wide_singleton_dist_uses_weighted_sat_fallback():
    class Sparse(SvObject):
        choice = Bit(32)

        @constraint
        def legal(self):
            self.choice @ dist[
                0x12345678 @ 1,
                0x9ABCDEF0 @ 3,
            ]

    counts = {0x12345678: 0, 0x9ABCDEF0: 0}
    obj = Sparse()
    with RandomContext(seed=303):
        for _ in range(240):
            assert obj.randomize() is True
            assert obj.choice.value in counts
            counts[obj.choice.value] += 1
    assert counts[0x12345678] > 30
    assert counts[0x9ABCDEF0] > 120
    assert counts[0x9ABCDEF0] > counts[0x12345678] * 2


def test_sparse_wide_range_dist_uses_total_range_weight_fallback():
    class SparseRange(SvObject):
        choice = Bit(32)

        @constraint
        def legal(self):
            self.choice @ dist[
                (0x10203040, 0x10203043) / 1,
                0x50607080 @ 3,
            ]

    counts = {value: 0 for value in range(0x10203040, 0x10203044)}
    counts[0x50607080] = 0
    obj = SparseRange()
    with RandomContext(seed=304):
        for _ in range(240):
            assert obj.randomize() is True
            assert obj.choice.value in counts
            counts[obj.choice.value] += 1
    # The four-value range shares a total weight of one, while the singleton
    # has weight three.
    assert counts[0x50607080] > 150
    assert sum(counts[value] for value in range(0x10203040, 0x10203044)) < 90


def test_interacting_direct_distributions_use_the_product_of_declared_weights():
    class Correlated(SvObject):
        first = Bit(1)
        second = Bit(1)

        @constraint
        def legal(self):
            self.first @ dist[0 @ 1, 1 @ 3]
            self.second @ dist[0 @ 1, 1 @ 3]
            self.first == self.second

    counts = {0: 0, 1: 0}
    obj = Correlated()
    with RandomContext(seed=305):
        for _ in range(400):
            assert obj.randomize()
            assert obj.first.value == obj.second.value
            counts[obj.first.value] += 1
    # The feasible (0, 0) and (1, 1) choices have weights 1*1 and 3*3.
    assert counts[1] > 320
    assert counts[1] > counts[0] * 5


def test_conditional_dist_uses_branch_local_weight_normalization():
    class Conditional(SvObject):
        gate = Bit(1)
        choice = Bit(8)

        @constraint
        def legal(self):
            if self.gate == 0:
                self.choice @ dist[2 @ 3, 3 @ 1]
            else:
                self.choice @ dist[0 @ 1, 1 @ 3]

    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    packet = Conditional()
    with RandomContext(seed=308):
        for _ in range(240):
            assert packet.randomize()
            counts[packet.choice.value] += 1
    assert counts[1] > counts[0] * 2
    assert counts[2] > counts[3] * 2
    # Both branch totals are normalized independently: a high-weight value
    # from either branch is common, not one branch being favored by its raw
    # total weight.
    assert abs(counts[1] - counts[2]) < 70


def test_dist_weight_can_depend_on_another_random_leaf():
    class DependentWeight(SvObject):
        gate = Bit(2)
        choice = Bit(8)

        @constraint
        def legal(self):
            self.gate < 2
            self.choice @ dist[0 @ (self.gate + 1), 1 @ 1]

    counts = {0: 0, 1: 0}
    packet = DependentWeight()
    with RandomContext(seed=309):
        for _ in range(240):
            assert packet.randomize()
            counts[packet.choice.value] += 1
    # gate is unconstrained and uniform.  The two conditional distributions
    # yield P(choice=0) = (1/2 + 2/3) / 2 = 7/12.
    assert counts[0] > counts[1]


def test_dist_applies_after_dynamic_element_expansion():
    class DynamicDist(SvObject):
        data = DynArray(Bit(2), rand=True, max_length=2)

        @constraint
        def legal(self):
            self.data.size() == 1
            self.data[0] @ dist[0 @ 1, 3 @ 3]

    counts = {0: 0, 3: 0}
    packet = DynamicDist()
    with RandomContext(seed=306):
        for _ in range(160):
            assert packet.randomize()
            counts[packet.data[0].value] += 1
    assert counts[3] > counts[0] * 2


def test_dist_applies_to_an_allocated_rand_handle_leaf():
    counts = {0: 0, 3: 0}
    packet = HandleDistPacket()
    packet.child = DistHandleChild()
    with RandomContext(seed=307):
        for _ in range(160):
            assert packet.randomize()
            counts[packet.child.choice.value] += 1
    assert counts[3] > counts[0] * 2


def test_dist_rejects_negative_weights_and_boolean_composition():
    with pytest.raises(ConstraintTypeError, match="non-negative"):
        class Negative(SvObject):
            choice = Bit(4)

            @constraint
            def legal(self):
                self.choice @ dist[1 @ -1]

    with pytest.raises(ConstraintTypeError, match="complete constraint statement"):
        class Combined(SvObject):
            choice = Bit(4)
            flag = Bit(1)

            @constraint
            def legal(self):
                (self.choice @ dist[1 @ 1]) and self.flag

    with pytest.raises(ConstraintTypeError, match="at least one rand"):
        class Fixed(SvObject):
            choice = Bit(4, rand=False)

            @constraint
            def legal(self):
                self.choice @ dist[1 @ 1]
