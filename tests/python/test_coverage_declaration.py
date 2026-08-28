from __future__ import annotations

import pytest

from svtypes import Bit, CoverInput, SvObject, covergroup
from svtypes.coverage import BoundCoverGroup, CoverGroupDeclaration
from svtypes.errors import CoverageError


def test_covergroup_descriptor_binds_a_read_only_slot_to_each_host():
    class Packet(SvObject):
        opcode = Bit(8)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            pass

    first = Packet()
    second = Packet()

    assert isinstance(Packet.cg, CoverGroupDeclaration)
    assert isinstance(first.cg, BoundCoverGroup)
    assert first.cg is first.cg
    assert first.cg is not second.cg
    assert first.cg.declaration is Packet.cg
    with pytest.raises(AttributeError, match="read-only"):
        first.cg = object()


def test_covergroup_is_uninstantiated_until_explicitly_instantiated_once():
    class Packet(SvObject):
        @covergroup
        def cg(self, limit: CoverInput[int]):
            pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate(7)

    packet = Packet()

    assert packet.cg.instantiated
    assert packet.cg.instance.constructor_actuals == (7,)

    class DoublePacket(SvObject):
        @covergroup
        def cg(self):
            pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate()
            self.cg.instantiate()

    with pytest.raises(CoverageError, match="already instantiated"):
        DoublePacket()


def test_covergroup_cannot_be_instantiated_outside_its_host_constructor():
    class Packet(SvObject):
        @covergroup
        def cg(self):
            pass

    packet = Packet()

    with pytest.raises(CoverageError, match="host __init__"):
        packet.cg.instantiate()
    for call in (packet.cg.sample, packet.cg.get_coverage, packet.cg.get_inst_coverage, packet.cg.start, packet.cg.stop):
        with pytest.raises(CoverageError, match="not instantiated"):
            call()


@pytest.mark.parametrize(
    ("actuals", "named", "message"),
    [
        ((1, 2), {}, "too many positional"),
        ((), {}, "missing 'limit'"),
        ((1,), {"limit": 2}, "binds 'limit' twice"),
        ((1,), {"other": 2}, "unknown argument 'other'"),
    ],
)
def test_coverinput_constructor_bindings_are_exact(actuals, named, message):
    class Packet(SvObject):
        @covergroup
        def cg(self, limit: CoverInput[int]):
            pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate(*actuals, **named)

    with pytest.raises(CoverageError, match=message):
        Packet()


def test_coverinput_builtin_type_mismatch_is_rejected_at_instantiate():
    class Packet(SvObject):
        @covergroup
        def cg(self, limit: CoverInput[int]):
            pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate("not-an-int")

    with pytest.raises(CoverageError, match="expects int"):
        Packet()


def test_coverinput_resolved_svtype_class_mismatch_is_rejected_at_instantiate():
    class Packet(SvObject):
        @covergroup
        def cg(self, limit: CoverInput[Bit]):
            pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate(1)

    with pytest.raises(CoverageError, match="expects Bit"):
        Packet()
