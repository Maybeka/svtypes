"""Simulator-neutral validation for externally normalized observations.

The target adapter is deliberately outside this repository. It writes a JSON
document that names coverage items through the generated manifest; this module
validates that public protocol without parsing a target database or report.
"""

from __future__ import annotations

from typing import Any


def parse_observation(document: Any) -> dict[str, Any]:
    """Validate the adapter's named-bin observation document.

    Single-instance documents are ``{"items": {...}, "summary": {...}}``.
    ``CoverGroupOption.per_instance = 1`` documents are
    ``{"instances": {logical_instance_key: {"items", "summary"}}}``.  Summary
    carries the target's coverage percentage and a test-only count of public
    ``sample()`` calls. Ignore hits are intentionally not part of this protocol.
    """
    if not isinstance(document, dict):
        raise ValueError("coverage observation must be a mapping")
    if set(document) == {"instances"}:
        instances = document["instances"]
        if not isinstance(instances, dict) or not instances:
            raise ValueError("coverage observation instances must be a non-empty mapping")
        parsed: dict[str, Any] = {}
        for key, value in instances.items():
            if not isinstance(key, str) or not key:
                raise ValueError("coverage observation instance key must be a non-empty string")
            parsed[key] = _parse_instance_observation(value)
        return {"instances": dict(sorted(parsed.items()))}
    return _parse_instance_observation(document)


def _parse_instance_observation(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != {"items", "summary"}:
        raise ValueError("coverage observation must contain 'items' and 'summary' mappings")
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
    summary = document["summary"]
    if not isinstance(summary, dict) or set(summary) != {"coverage", "sample_count"}:
        raise ValueError("coverage observation summary must contain coverage and sample_count")
    coverage, sample_count = summary["coverage"], summary["sample_count"]
    if not isinstance(coverage, (int, float)) or isinstance(coverage, bool) or not 0 <= float(coverage) <= 100:
        raise ValueError("coverage observation summary coverage must be a percentage")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        raise ValueError("coverage observation summary sample_count must be a non-negative integer")
    return {"items": dict(sorted(result.items())), "summary": {"coverage": float(coverage), "sample_count": sample_count}}


def compare_manifest_hits(
    manifest: dict[str, Any], observation: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Raise a focused assertion error for manifest-directed mismatches."""
    if "instances" in observation:
        expected_instances = expected.get("instances")
        if not isinstance(expected_instances, dict):
            raise AssertionError("per-instance observation requires expected instance records")
        observed_instances = observation["instances"]
        manifest_keys = {item["logical_instance_key"] for item in manifest.get("instances", [])}
        if set(observed_instances) != manifest_keys:
            raise AssertionError(
                f"observation instance keys {sorted(observed_instances)} do not match manifest {sorted(manifest_keys)}"
            )
        if set(expected_instances) != manifest_keys:
            raise AssertionError(
                f"expected instance keys {sorted(expected_instances)} do not match manifest {sorted(manifest_keys)}"
            )
        for key in sorted(manifest_keys):
            _compare_items(manifest, observed_instances[key], expected_instances[key], prefix=f"instance {key!r} ")
        return
    _compare_items(manifest, observation, expected)


def _compare_items(
    manifest: dict[str, Any], observation: dict[str, Any], expected: dict[str, Any], *, prefix: str = ""
) -> None:
    observable = {
        item["observation_label"]
        for group in manifest["covergroups"]
        for collection in (group["points"], group["crosses"])
        for item in collection
    }
    observed_items = observation["items"]
    unexpected = set(observed_items).difference(observable)
    if unexpected:
        raise AssertionError(f"{prefix}observation contains items absent from observation manifest: {sorted(unexpected)}")
    missing = observable.difference(observed_items)
    if missing:
        raise AssertionError(f"{prefix}observation omits manifest items: {sorted(missing)}")
    expected_items = expected["items"] if "items" in expected else {
        name: counts for name, counts in expected.items() if name != "summary"
    }
    for name, expected_counts in expected_items.items():
        actual = observed_items.get(name)
        if actual is None:
            raise AssertionError(f"{prefix}observation omits manifest item {name!r}")
        for kind in ("hits", "illegal_hits"):
            actual_counts = {bin_name: count for bin_name, count in actual.get(kind, {}).items() if count}
            expected_nonzero = {bin_name: count for bin_name, count in expected_counts.get(kind, {}).items() if count}
            if actual_counts != expected_nonzero:
                raise AssertionError(
                    f"{prefix}observation {name!r} {kind} mismatch: expected {expected_nonzero}, "
                    f"got {actual_counts}"
                )
    expected_summary = expected.get("summary")
    if expected_summary is not None:
        observed_summary = observation["summary"]
        if observed_summary["sample_count"] != expected_summary["sample_count"]:
            raise AssertionError(
                f"{prefix}observation sample_count mismatch: expected {expected_summary['sample_count']}, "
                f"got {observed_summary['sample_count']}"
            )
        if abs(float(observed_summary["coverage"]) - float(expected_summary["coverage"])) > 1e-4:
            raise AssertionError(
                f"{prefix}observation coverage mismatch: expected {expected_summary['coverage']}, "
                f"got {observed_summary['coverage']}"
            )
