"""Process-wide simulation metadata for functional coverage."""

from __future__ import annotations

from ..errors import CoverageError

_case_name: str | None = None


def set_coverage_case_name(name: str) -> None:
    """Set the one stable case name for the current simulation process."""
    global _case_name
    if not isinstance(name, str) or not name:
        raise CoverageError("coverage case name must be a non-empty string")
    if _case_name is not None:
        raise CoverageError("coverage case name is already set for this simulation")
    _case_name = name


def coverage_case_name() -> str | None:
    return _case_name


def _reset_coverage_case_name_for_testing() -> None:
    global _case_name
    _case_name = None
