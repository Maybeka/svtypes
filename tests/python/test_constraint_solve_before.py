from __future__ import annotations

import pytest

from svtypes import Bit, ConstraintError, ConstraintTypeError, RandomContext, SvObject, constraint, solve_before


def test_solve_before_accepts_scalar_groups_and_renders_sv():
    class Ordered(SvObject):
        first = Bit(2)
        second = Bit(2)
        third = Bit(2)

        @constraint
        def order(self):
            solve_before((self.second, self.first), self.third)

    obj = Ordered()
    with RandomContext(seed=701):
        assert obj.randomize() is True
    ir = Ordered._SvObject__svtypes_constraint_irs["order"]
    assert ir.solve_before == [(('second', 'first'), ('third',))]
    assert "solve second, first before third;" in Ordered.to_sv_obj()


def test_solve_before_rejects_non_rand_randc_overlap_and_cycles():
    with pytest.raises(ConstraintTypeError, match="declared rand"):
        class Fixed(SvObject):
            first = Bit(2, rand=False)
            second = Bit(2)

            @constraint
            def order(self):
                solve_before(self.first, self.second)

    with pytest.raises(ConstraintTypeError, match="cannot include randc"):
        class Cyclic(SvObject):
            first = Bit(2, randc=True)
            second = Bit(2)

            @constraint
            def order(self):
                solve_before(self.first, self.second)

    class Cycle(SvObject):
        first = Bit(2)
        second = Bit(2)

        @constraint
        def first_order(self):
            solve_before(self.first, self.second)

        @constraint
        def second_order(self):
            solve_before(self.second, self.first)

    with pytest.raises(ConstraintError, match="cycle"):
        Cycle().randomize()
