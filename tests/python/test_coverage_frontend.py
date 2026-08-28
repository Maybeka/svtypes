from __future__ import annotations

import pytest

from svtypes import (
    Bit,
    CovPoint,
    CoverInput,
    CoverGroupOption,
    SvObject,
    bins,
    covergroup,
    default_bins,
    ignore_bins,
    illegal_bins,
)
from svtypes.errors import CoverageDeclarationError


def test_freeze_compiles_source_only_points_and_bins_without_executing_declaration():
    class Packet(SvObject):
        opcode = Bit(8)
        valid = Bit(1)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class option(CoverGroupOption):
                goal = 100

            class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
                common = bins[0, 2, 8:15]
                reserved = illegal_bins[3]
                masked = ignore_bins[4]
                other = default_bins

    ir = Packet.cg.freeze()

    assert ir.declaration_name == "cg"
    assert ir.constructor_parameters[0].name == "limit"
    point = ir.points[0]
    assert point.name == "opcode_cp"
    assert [item.kind for item in point.bins] == ["normal", "ignore", "default", "illegal"]
    assert point.iff is not None


def test_freeze_rejects_statements_outside_the_source_only_declaration_subset():
    class Packet(SvObject):
        opcode = Bit(8)

        @covergroup
        def cg(self):
            forbidden = 1

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-SYNTAX"):
        Packet.cg.freeze()


def test_python_runtime_classifies_iff_ignore_illegal_overlapping_normal_and_default_bins():
    class Packet(SvObject):
        opcode = Bit(8)
        valid = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
                low = bins[0:3]
                even = bins[0, 2, 4]
                masked = ignore_bins[1]
                reserved = illegal_bins[3]
                other = default_bins

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 2
    packet.valid.value = 1
    packet.cg.sample()
    packet.opcode.value = 1
    packet.cg.sample()
    packet.opcode.value = 3
    packet.cg.sample()
    packet.opcode.value = 9
    packet.cg.sample()
    packet.valid.value = 0
    packet.cg.sample()

    result = packet.cg.instance.snapshot()["opcode_cp"]
    assert result["samples"] == 4
    assert result["hits"] == {"even": 1, "low": 1, "other": 1}
    assert result["illegal_hits"] == {"reserved": 1}


def test_freeze_generates_deterministic_automatic_bins_from_a_bit_field_domain():
    class Packet(SvObject):
        opcode = Bit(4)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                pass

    bins_ir = Packet.cg.freeze().points[0].bins

    assert {item.name for item in bins_ir} == {f"auto[{index}]" for index in range(16)}
