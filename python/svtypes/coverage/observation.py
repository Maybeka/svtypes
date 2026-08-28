"""Simulator-neutral validation for externally normalized observations.

The target adapter is deliberately outside this repository. It writes a JSON
document that names coverage items through the generated manifest; this module
validates that public protocol without parsing a target database or report.
"""

from __future__ import annotations

from typing import Any


def parse_observation(document: Any) -> dict[str, dict[str, dict[str, int]]]:
    """Validate the adapter's named-bin observation document.

    The accepted shape is ``{"items": {label: {"hits": {...},
    "illegal_hits": {...}}}}``. Counts are non-negative integers. Ignore hits
    are intentionally not part of this protocol.
    """
    if not isinstance(document, dict) or set(document) != {"items"}:
        raise ValueError("coverage observation must contain exactly an 'items' mapping")
    items = document["items"]
    if not isinstance(items, dict):
        raise ValueError("coverage observation items must be a mapping")
    result: dict[str, dict[str, dict[str, int]]] = {}
    for name, values in items.items():
        if not isinstance(name, str) or not name:
            raise ValueError("coverage observation item name must be a non-empty string")
        if not isinstance(values, dict) or set(values) != {"hits", "illegal_hits"}:
            raise ValueError(f"coverage observation item {name!r} must contain hits and illegal_hits")
        normalized: dict[str, dict[str, int]] = {}
        for kind in ("hits", "illegal_hits"):
            counts = values[kind]
            if not isinstance(counts, dict):
                raise ValueError(f"coverage observation item {name!r} {kind} must be a mapping")
            normalized_counts: dict[str, int] = {}
            for bin_name, count in counts.items():
                if not isinstance(bin_name, str) or not bin_name:
                    raise ValueError(f"coverage observation item {name!r} has invalid bin name")
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    raise ValueError(f"coverage observation item {name!r} bin {bin_name!r} has invalid count")
                normalized_counts[bin_name] = count
            normalized[kind] = normalized_counts
        result[name] = normalized
    return dict(sorted(result.items()))


def compare_manifest_hits(
    manifest: dict[str, Any], observation: dict[str, dict[str, dict[str, int]]], expected: dict[str, dict[str, dict[str, int]]]
) -> None:
    """Raise a focused assertion error for manifest-directed mismatches."""
    observable = {
        item["observation_label"]
        for group in manifest["covergroups"]
        for collection in (group["points"], group["crosses"])
        for item in collection
    }
    unexpected = set(observation).difference(observable)
    if unexpected:
        raise AssertionError(f"observation contains items absent from observation manifest: {sorted(unexpected)}")
    for name, expected_counts in expected.items():
        actual = observation.get(name)
        if actual is None:
            raise AssertionError(f"observation omits manifest item {name!r}")
        for kind in ("hits", "illegal_hits"):
            if actual.get(kind, {}) != expected_counts.get(kind, {}):
                raise AssertionError(
                    f"observation {name!r} {kind} mismatch: expected {expected_counts.get(kind, {})}, "
                    f"got {actual.get(kind, {})}"
                )
