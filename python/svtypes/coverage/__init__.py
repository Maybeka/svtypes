"""Functional-coverage declarations, runtime evaluation, and rendering.

The package exposes frozen declaration IR, the source-only DSL, embedded
runtime instances, evaluation, deterministic observation metadata, and
SystemVerilog rendering.
"""

from .ir import (
    COVERAGE_IR_VERSION,
    CoverageBinIR,
    CoverageInitCallIR,
    CoverageInitIR,
    CoverageCrossIR,
    CrossMemberViewIR,
    CrossQueueFunctionIR,
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
    coverage_init,
    coverage_initializers,
    preview_coverage_layout,
)
from .dsl import (
    CovPoint,
    CovPointArray,
    CovPointOption,
    CoverGroupOption,
    CoverGroupTypeOption,
    Cross,
    CrossQueueType,
    CrossOption,
    bins,
    default_bins,
    ignore_bins,
    illegal_bins,
    repeat,
    transition_bins,
)
from .identity import bin_id, point_id
from .evaluator import CoverageRuntime, eval_expr
from .context import coverage_case_name, set_coverage_case_name
from .database import CoverageDatabase, CoverageRecord
from .persistence import FORMAT_VERSION as COVERAGE_DATABASE_FORMAT_VERSION
from .ucis import export_ucis, export_ucis_file, import_ucis, import_ucis_file
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
    "CrossQueueType",
    "CrossOption",
    "CoverageBinIR",
    "CoverageInitCallIR",
    "CoverageInitIR",
    "CoverageCrossIR",
    "CrossMemberViewIR",
    "CrossQueueFunctionIR",
    "CoverageIR",
    "CoveragePointIR",
    "CoverageProvenance",
    "CoverageRuntime",
    "coverage_case_name",
    "CoverageDatabase",
    "COVERAGE_DATABASE_FORMAT_VERSION",
    "export_ucis",
    "export_ucis_file",
    "import_ucis",
    "import_ucis_file",
    "CoverageRecord",
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
    "coverage_init",
    "coverage_initializers",
    "preview_coverage_layout",
    "default_bins",
    "eval_expr",
    "ignore_bins",
    "illegal_bins",
    "repeat",
    "set_coverage_case_name",
    "transition_bins",
]
