"""Stable, provenance-free coverage declaration identities."""

from __future__ import annotations

from .canonical import canonical_json_bytes
from .ir import CoverageBinIR, CoverageIR, CoveragePointIR


def point_id(coverage: CoverageIR, point: CoveragePointIR) -> str:
    """Return the stable identity of a declared coverage point.

    A point's explicit name is its declaration-slot identity.  Its expression
    and bins belong to the declaration semantic digest rather than changing
    this ID across compatible diagnostics.
    """
    return f"{coverage.covergroup_type_id}::point::{point.name}"


def bin_id(coverage: CoverageIR, point: CoveragePointIR, bin_declaration: CoverageBinIR) -> str:
    """Return a canonical bin identity without source or runtime state.

    The readable prefix keeps diagnostics useful; the canonical definition
    makes a same-named bin with changed semantics a distinct bin identity.
    """
    definition = canonical_json_bytes(bin_declaration.stable_dict()).decode("utf-8")
    return f"{point_id(coverage, point)}::bin::{definition}"
