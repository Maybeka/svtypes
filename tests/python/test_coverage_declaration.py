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
            raise AssertionError("declaration body must never execute")

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
            raise AssertionError("declaration body must never execute")

        def __init__(self):
            super().__init__()
            self.cg.instantiate(7)

    packet = Packet()

    assert packet.cg.instantiated
    assert packet.cg.instance.constructor_actuals == (7,)

    class DoublePacket(SvObject):
        @covergroup
        def cg(self):
            raise AssertionError("declaration body must never execute")

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
            raise AssertionError("declaration body must never execute")

    packet = Packet()

    with pytest.raises(CoverageError, match="host __init__"):
        packet.cg.instantiate()
