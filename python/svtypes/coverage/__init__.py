"""Private functional-coverage implementation package.

Only the frozen semantic IR is exposed here during the 1.7 implementation
phase.  The public coverage DSL is introduced by a later milestone.
"""

from .ir import (
    COVERAGE_IR_VERSION,
    CoverageBinIR,
    CoverageCrossIR,
    CoverageIR,
    CoveragePointIR,
    CoverageProvenance,
    SampleParameterIR,
)
from .auto import AUTO_COVERGROUP_NAME, auto_coverage_ir
from .declaration import (
    BoundCoverGroup,
    CoverGroupDeclaration,
    CoverGroupInstance,
    CoverInput,
    CoverRef,
    covergroup,
)
from .identity import bin_id, point_id
from .limits import (
    MAX_CROSS_MEMBERS,
    MAX_NORMAL_CROSS_BINS,
    MAX_NORMAL_CROSS_BINS_PER_COVERGROUP,
    validate_cross_normal_bin_count,
)

__all__ = [
    "COVERAGE_IR_VERSION",
    "AUTO_COVERGROUP_NAME",
    "BoundCoverGroup",
    "CoverGroupDeclaration",
    "CoverGroupInstance",
    "CoverInput",
    "CoverRef",
    "CoverageBinIR",
    "CoverageCrossIR",
    "CoverageIR",
    "CoveragePointIR",
    "CoverageProvenance",
    "MAX_CROSS_MEMBERS",
    "MAX_NORMAL_CROSS_BINS",
    "MAX_NORMAL_CROSS_BINS_PER_COVERGROUP",
    "SampleParameterIR",
    "bin_id",
    "point_id",
    "validate_cross_normal_bin_count",
    "auto_coverage_ir",
    "covergroup",
]
