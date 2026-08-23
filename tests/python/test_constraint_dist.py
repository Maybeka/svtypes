from __future__ import annotations

import pytest

from svtypes import Bit, ConstraintTypeError, RandomContext, SvObject, constraint, dist
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
