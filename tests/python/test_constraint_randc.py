from __future__ import annotations

import pytest

from svtypes import Bit, ConstraintTypeError, Logic, RandomContext, SvObject, constraint, dist


def test_randc_cycle_mode_pause_and_generated_declaration():
    class Cycle(SvObject):
        choice = Bit(2, randc=True)

    obj = Cycle()
    with RandomContext(seed=401):
        first = []
        for _ in range(2):
            assert obj.randomize() is True
            first.append(obj.choice.value)
        obj.choice.rand_mode(0)
        held = obj.choice.value
        assert obj.randomize() is True
        assert obj.choice.value == held
        obj.choice.rand_mode(1)
        for _ in range(2):
            assert obj.randomize() is True
            first.append(obj.choice.value)
    assert len(set(first)) == 4
    assert "randc bit [1:0] choice;" in Cycle.to_sv_obj()


def test_randc_constraint_exhaustion_resets_and_unsat_does_not_consume():
    class Limited(SvObject):
        choice = Bit(2, randc=True)
        limit = Bit(2, rand=False)

        @constraint
        def legal(self):
            self.choice < self.limit

    obj = Limited()
    obj.limit.value = 3
    with RandomContext(seed=402):
        values = []
        for _ in range(3):
            assert obj.randomize() is True
            values.append(obj.choice.value)
        assert set(values) == {0, 1, 2}
        # No old legal value remains, so a satisfiable next call starts a new cycle.
        assert obj.randomize() is True
        assert obj.choice.value in {0, 1, 2}

    frozen = Limited()
    frozen.limit.value = 0
    before = dict(frozen._SvObject__svtypes_randc_state)
    assert frozen.randomize() is False
    assert frozen.svtypes_randomize_status.reason == "unsat"
    assert frozen._SvObject__svtypes_randc_state == before


def test_randc_rejects_logic_dist_and_explicit_rand_policy():
    with pytest.raises(ValueError, match="unsupported"):
        class BadLogic(SvObject):
            choice = Logic(2, randc=True)

    with pytest.raises(ValueError, match="cannot be combined"):
        Bit(2, rand=False, randc=True)
    with pytest.raises(TypeError, match="randc"):
        Bit(2, randc=None)

    with pytest.raises(ConstraintTypeError, match="randc"):
        class BadDist(SvObject):
            choice = Bit(2, randc=True)

            @constraint
            def legal(self):
                self.choice @ dist[0 @ 1, 1 @ 1]
