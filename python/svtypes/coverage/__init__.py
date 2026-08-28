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
from .dsl import (
    CovPoint,
    CovPointArray,
    CovPointOption,
    CoverGroupOption,
    CoverGroupTypeOption,
    Cross,
    CrossOption,
    bins,
    default_bins,
    ignore_bins,
    illegal_bins,
    transition_bins,
)
from .identity import bin_id, point_id
from .evaluator import CoverageRuntime, eval_expr
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
    "CoverGroupOption",
    "CoverGroupTypeOption",
    "CoverInput",
    "CoverRef",
    "CovPoint",
    "CovPointArray",
    "CovPointOption",
    "Cross",
    "CrossOption",
    "CoverageBinIR",
    "CoverageCrossIR",
    "CoverageIR",
    "CoveragePointIR",
    "CoverageProvenance",
    "CoverageRuntime",
    "MAX_CROSS_MEMBERS",
    "MAX_NORMAL_CROSS_BINS",
    "MAX_NORMAL_CROSS_BINS_PER_COVERGROUP",
    "SampleParameterIR",
    "bin_id",
    "point_id",
    "validate_cross_normal_bin_count",
    "auto_coverage_ir",
    "bins",
    "covergroup",
    "default_bins",
    "eval_expr",
    "ignore_bins",
    "illegal_bins",
    "transition_bins",
]
