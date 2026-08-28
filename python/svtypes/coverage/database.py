"""Small in-memory coverage database for 1.8 runtime records."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..errors import CoverageError


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    logical_instance_key: str | None
    document: dict[str, Any]


class CoverageDatabase:
    """Collect frozen instance snapshots and merge matching declarations."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str | None], CoverageRecord] = {}
        self._live_instance_ids: dict[tuple[str, str | None], int] = {}

    def record(self, instance: Any, *, logical_instance_key: str | None = None) -> None:
        """Store the current snapshot for one logical coverage instance.

        ``record`` is a refresh operation, unlike :meth:`merge`: recording an
        already-known live instance replaces its previous snapshot instead of
        counting the same samples twice.
        """
        bound_key = getattr(instance, "logical_instance_key", None)
        if logical_instance_key is not None and bound_key is not None and logical_instance_key != bound_key:
            raise CoverageError("coverage database logical instance key disagrees with instance binding")
        logical_instance_key = bound_key if bound_key is not None else logical_instance_key
        if getattr(getattr(instance, "option", None), "per_instance", 0) and logical_instance_key is None:
            raise CoverageError("per-instance coverage requires a logical instance key")
        document = instance.snapshot_document()
        type_id = document["covergroup_type_id"]
        key = (type_id, logical_instance_key)
        current = self._records.get(key)
        if current is not None and current.document["declaration_semantic_digest"] != document["declaration_semantic_digest"]:
            raise CoverageError(f"coverage database declaration mismatch for {type_id}")
        if current is not None and current.document.get("instance_layout_digest") != document.get("instance_layout_digest"):
            raise CoverageError(f"coverage database instance layout mismatch for {type_id}")
        current_id = self._live_instance_ids.get(key)
        if current_id is not None and current_id != id(instance):
            raise CoverageError(f"coverage database logical instance key is already registered for {type_id}")
        self._records[key] = CoverageRecord(logical_instance_key, deepcopy(document))
        self._live_instance_ids[key] = id(instance)

    def merge(self, other: "CoverageDatabase") -> None:
        for key, record in other._records.items():
            current = self._records.get(key)
            if current is not None and current.document["declaration_semantic_digest"] != record.document["declaration_semantic_digest"]:
                raise CoverageError(f"coverage database declaration mismatch for {key[0]}")
            if current is not None and current.document.get("instance_layout_digest") != record.document.get("instance_layout_digest"):
                raise CoverageError(f"coverage database instance layout mismatch for {key[0]}")
            if current is None:
                self._records[key] = CoverageRecord(
                    record.logical_instance_key, deepcopy(record.document)
                )
                continue
            self._records[key] = CoverageRecord(
                record.logical_instance_key,
                _merge_record_documents(current.document, record.document),
            )

    def snapshot_document(self) -> dict[str, Any]:
        return {
            "records": [
                {"logical_instance_key": record.logical_instance_key, **deepcopy(record.document)}
                for _, record in sorted(
                    self._records.items(), key=lambda item: (item[0][0], item[0][1] or "")
                )
            ]
        }

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_record_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge counters from equivalent, independently sampled instances."""
    result = deepcopy(left)
    left_points = result.get("points", {})
    right_points = right.get("points", {})
    if set(left_points) != set(right_points):
        raise CoverageError("coverage database point layout mismatch")
    for name, right_counter in right_points.items():
        left_counter = left_points[name]
        for field in ("hits", "illegal_hits"):
            merged = dict(left_counter.get(field, {}))
            for bin_name, count in right_counter.get(field, {}).items():
                merged[bin_name] = merged.get(bin_name, 0) + count
            left_counter[field] = dict(sorted(merged.items()))
        left_counter["samples"] = left_counter.get("samples", 0) + right_counter.get("samples", 0)
    return result
