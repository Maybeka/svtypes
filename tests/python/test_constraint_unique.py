from __future__ import annotations

import pytest

from svtypes import Bit, ConstraintSyntaxError, RandomContext, SvObject, constraint, unique
from svtypes.constraint.eval import eval_bool
from svtypes.constraint.leaves import iter_class_leaves, leaf_unsigned, resolve_attr


class UniquePacket(SvObject):
    first = Bit(2)
    second = Bit(2)
    third = Bit(2)

    @constraint
    def legal(self):
        unique(self.first, self.second, self.third)


def test_unique_is_a_hard_scalar_constraint_in_python_and_sv():
    obj = UniquePacket()
    ir = UniquePacket._SvObject__svtypes_constraint_irs["legal"]
    assert ir.predicates[0].op == "unique"
    assert "unique {first, second, third};" in UniquePacket.to_sv_obj()
    with RandomContext(seed=501):
        for _ in range(32):
            assert obj.randomize() is True
            assert len({obj.first.value, obj.second.value, obj.third.value}) == 3
            env = {}
            widths = {}
            for path, desc, _declared in iter_class_leaves(UniquePacket):
                target = resolve_attr(obj, path)
                env[path] = leaf_unsigned(target, target.value)
                widths[path] = (desc.width, bool(desc.signed))
            assert eval_bool(ir.predicates[0], env, widths)


def test_unique_requires_two_or_more_arguments():
    with pytest.raises(ConstraintSyntaxError, match="at least two"):
        class Bad(SvObject):
            first = Bit(2)

            @constraint
            def legal(self):
                unique(self.first)


def test_unique_mixed_widths_uses_sv_equality_sizing():
    class Mixed(SvObject):
        narrow = Bit(2)
        wide = Bit(3)

        @constraint
        def legal(self):
            unique(self.narrow, self.wide)

    obj = Mixed()
    with RandomContext(seed=502):
        for _ in range(16):
            assert obj.randomize() is True
            assert obj.narrow.value != obj.wide.value
