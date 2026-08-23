from __future__ import annotations

from svtypes import Bit, RandomContext, SvObject, constraint, soft


def test_soft_conflict_with_hard_constraint_is_dropped_not_unsat():
    class HardWins(SvObject):
        choice = Bit(32)

        @constraint
        def legal(self):
            soft(self.choice == 1)
            self.choice == 0

    obj = HardWins()
    with RandomContext(seed=601):
        assert obj.randomize() is True
    assert obj.choice.value == 0
    text = HardWins.to_sv_obj()
    assert "soft (choice == 32'd1);" in text


def test_soft_priority_uses_later_and_derived_constraints_first():
    class Ordered(SvObject):
        choice = Bit(32)

        @constraint
        def bounds(self):
            self.choice < 3

        @constraint
        def preference(self):
            soft(self.choice == 0)
            soft(self.choice == 1)

    ordered = Ordered()
    with RandomContext(seed=602):
        assert ordered.randomize() is True
    assert ordered.choice.value == 1

    class Base(SvObject):
        choice = Bit(32)

        @constraint
        def base_preference(self):
            soft(self.choice == 1)

    class Derived(Base):
        @constraint
        def derived_preference(self):
            soft(self.choice == 2)

        @constraint
        def bounds(self):
            self.choice < 3

    derived = Derived()
    with RandomContext(seed=603):
        assert derived.randomize() is True
    assert derived.choice.value == 2


def test_soft_inside_if_uses_the_selected_branch_only():
    class Conditional(SvObject):
        flag = Bit(1)
        choice = Bit(32)

        @constraint
        def legal(self):
            self.flag == 0
            if self.flag:
                soft(self.choice == 1)
            else:
                soft(self.choice == 2)

    obj = Conditional()
    with RandomContext(seed=604):
        assert obj.randomize() is True
    assert obj.choice.value == 2
