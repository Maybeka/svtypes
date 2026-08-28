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
