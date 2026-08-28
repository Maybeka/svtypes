from __future__ import annotations

import pytest

from svtypes.coverage import (
    MAX_NORMAL_CROSS_BINS,
    MAX_NORMAL_CROSS_BINS_PER_COVERGROUP,
    CoverageBinIR,
    CoverageIR,
    CoveragePointIR,
    bin_id,
    point_id,
    validate_cross_normal_bin_count,
)
from svtypes.errors import CoverageDeclarationError


def _coverage() -> tuple[CoverageIR, CoveragePointIR, CoverageBinIR]:
    bin_declaration = CoverageBinIR("read", "normal", (0,))
    point = CoveragePointIR("opcode", {"field": "item.opcode"}, (bin_declaration,))
    return CoverageIR("Packet", "opcode_cg", points=(point,)), point, bin_declaration


def test_point_and_bin_ids_are_stable_and_definition_sensitive():
    coverage, point, bin_declaration = _coverage()
    changed = CoverageBinIR("read", "normal", (1,))

    assert point_id(coverage, point) == "Packet::opcode_cg::point::opcode"
    assert bin_id(coverage, point, bin_declaration) == bin_id(coverage, point, bin_declaration)
    assert bin_id(coverage, point, bin_declaration) != bin_id(coverage, point, changed)
    assert "0x" not in bin_id(coverage, point, bin_declaration)


def test_cross_normal_bin_limits_accept_boundaries_and_reject_overflow():
    total = validate_cross_normal_bin_count("first", MAX_NORMAL_CROSS_BINS)
    assert total == MAX_NORMAL_CROSS_BINS
    assert (
        validate_cross_normal_bin_count(
            "last",
            MAX_NORMAL_CROSS_BINS,
            existing_covergroup_normal_bins=MAX_NORMAL_CROSS_BINS_PER_COVERGROUP - MAX_NORMAL_CROSS_BINS,
        )
        == MAX_NORMAL_CROSS_BINS_PER_COVERGROUP
    )

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-CROSS-LIMIT"):
        validate_cross_normal_bin_count("too_wide", MAX_NORMAL_CROSS_BINS + 1)
    with pytest.raises(CoverageDeclarationError, match="SVT-COV-CROSS-LIMIT"):
        validate_cross_normal_bin_count(
            "too_many",
            1,
            existing_covergroup_normal_bins=MAX_NORMAL_CROSS_BINS_PER_COVERGROUP,
        )
