"""Frozen functional-coverage declaration resource limits."""

from __future__ import annotations

from ..errors import CoverageDeclarationError


MAX_CROSS_MEMBERS = 8
MAX_NORMAL_CROSS_BINS = 65_536
MAX_NORMAL_CROSS_BINS_PER_COVERGROUP = 1_048_576
MAX_CROSS_QUEUE_TUPLES = 65_536


def validate_cross_normal_bin_count(
    cross_name: str,
    normal_bin_count: int,
    *,
    existing_covergroup_normal_bins: int = 0,
) -> int:
    """Validate one cross against the frozen 1.7 normal-bin budgets.

    The caller supplies the count after all declaration-time selectors and
    automatic-bin retention rules have been applied.  The returned total is
    the value to pass into the next cross validation.
    """
    if not isinstance(normal_bin_count, int) or isinstance(normal_bin_count, bool) or normal_bin_count < 0:
        raise CoverageDeclarationError(
            "SVT-COV-CROSS-LIMIT", "normal cross-bin count must be a non-negative integer"
        )
    if (
        not isinstance(existing_covergroup_normal_bins, int)
        or isinstance(existing_covergroup_normal_bins, bool)
        or existing_covergroup_normal_bins < 0
    ):
        raise CoverageDeclarationError(
            "SVT-COV-CROSS-LIMIT", "existing covergroup normal-bin count must be a non-negative integer"
        )
    if normal_bin_count > MAX_NORMAL_CROSS_BINS:
        raise CoverageDeclarationError(
            "SVT-COV-CROSS-LIMIT",
            f"cross {cross_name!r} has {normal_bin_count} normal bins; "
            f"limit is {MAX_NORMAL_CROSS_BINS}",
        )
    total = existing_covergroup_normal_bins + normal_bin_count
    if total > MAX_NORMAL_CROSS_BINS_PER_COVERGROUP:
        raise CoverageDeclarationError(
            "SVT-COV-CROSS-LIMIT",
            f"covergroup reaches {total} normal cross bins after {cross_name!r}; "
            f"limit is {MAX_NORMAL_CROSS_BINS_PER_COVERGROUP}",
        )
    return total
