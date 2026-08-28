"""Small in-memory coverage database for 1.8 runtime records."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..errors import CoverageError
from .persistence import decode_database, encode_database, read_database, write_database


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
        per_instance = bool(getattr(getattr(instance, "option", None), "per_instance", 0))
        if per_instance and logical_instance_key is None:
            raise CoverageError("per-instance coverage requires a logical instance key")
        document = instance.snapshot_document()
        type_id = document["covergroup_type_id"]
        # A non-per-instance result can contribute to type coverage without a
        # user-visible identity.  Keep such live records separate internally;
        # never expose this ephemeral implementation key in a snapshot.
        storage_key = logical_instance_key if logical_instance_key is not None else f"__ephemeral__{id(instance)}"
        key = (type_id, storage_key)
        current = self._records.get(key)
        if current is not None and current.document["declaration_semantic_digest"] != document["declaration_semantic_digest"]:
            raise CoverageError(f"coverage database declaration mismatch for {type_id}")
        if current is not None and current.document.get("definition") != document.get("definition"):
            raise CoverageError(f"coverage database definition mismatch for {type_id}")
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
            if current is not None and current.document.get("definition") != record.document.get("definition"):
                raise CoverageError(f"coverage database definition mismatch for {key[0]}")
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

    def apply_baseline(self, instance: Any, *, logical_instance_key: str | None = None) -> None:
        """Seed a newly created runtime instance from one persisted record.

        The operation restores only accumulated counters and finite source
        evidence.  It intentionally does not reconstruct the old instance,
        constructor actuals, transition history, sample log, or registration.
        """
        bound_key = getattr(instance, "logical_instance_key", None)
        if logical_instance_key is not None and bound_key is not None and logical_instance_key != bound_key:
            raise CoverageError("coverage baseline logical instance key disagrees with instance binding")
        logical_instance_key = bound_key if bound_key is not None else logical_instance_key
        current = instance.snapshot_document()
        type_id = current["covergroup_type_id"]
        candidates = [
            record for (candidate_type, _), record in self._records.items()
            if candidate_type == type_id and record.logical_instance_key == logical_instance_key
        ]
        if not candidates:
            raise CoverageError(f"coverage baseline record not found for {type_id}")
        if len(candidates) != 1:
            raise CoverageError(f"coverage baseline record is ambiguous for {type_id}")
        baseline = candidates[0].document
        for field, message in (
            ("declaration_semantic_digest", "declaration mismatch"),
            ("definition", "definition mismatch"),
            ("instance_layout_digest", "instance layout mismatch"),
        ):
            if current.get(field) != baseline.get(field):
                raise CoverageError(f"coverage baseline {message} for {type_id}")
        if int(current.get("source_limit", 3)) != int(baseline.get("source_limit", 3)):
            raise CoverageError(f"coverage baseline source limit mismatch for {type_id}")
        instance.runtime.restore_counter_snapshot(deepcopy(baseline["points"]))

    def snapshot_document(self) -> dict[str, Any]:
        return {
            "records": [
                {"logical_instance_key": record.logical_instance_key, **deepcopy(record.document)}
                for _, record in sorted(
                    self._records.items(), key=lambda item: (item[0][0], item[0][1])
                )
            ]
        }

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_bytes(self) -> bytes:
        """Export portable aggregate coverage counters for cross-run merge."""
        return encode_database(self.snapshot_document())

    @classmethod
    def from_bytes(cls, data: bytes) -> "CoverageDatabase":
        database = cls()
        database._load_document(decode_database(data))
        return database

    def write(self, path: str) -> None:
        write_database(path, self.snapshot_document())

    @classmethod
    def read(cls, path: str) -> "CoverageDatabase":
        database = cls()
        database._load_document(read_database(path))
        return database

    def _load_document(self, document: dict[str, Any]) -> None:
        for item in document["records"]:
            if not isinstance(item, dict) or "logical_instance_key" not in item:
                raise CoverageError("coverage database record is invalid")
            record_document = {key: deepcopy(value) for key, value in item.items() if key != "logical_instance_key"}
            type_id = record_document.get("covergroup_type_id")
            key = item["logical_instance_key"]
            if not isinstance(type_id, str) or not (key is None or isinstance(key, str)):
                raise CoverageError("coverage database record identity is invalid")
            storage_key = key if key is not None else f"__imported__{len(self._records)}"
            self._records[(type_id, storage_key)] = CoverageRecord(key, record_document)

    def type_summary(self, covergroup_type_id: str) -> dict[str, Any]:
        """Return the LRM-shaped type result for one declaration slot.

        With ``merge_instances=0`` this is a weighted average of independent
        instance percentages.  With ``=1`` counters are first combined by bin
        name, then scored as one type bin universe.
        """
        records = [record.document for (type_id, _), record in self._records.items() if type_id == covergroup_type_id]
        if not records:
            raise CoverageError(f"unknown coverage group type {covergroup_type_id!r}")
        merge_instances = int(records[0].get("type_options", {}).get("merge_instances", 0))
        if any(int(record.get("type_options", {}).get("merge_instances", 0)) != merge_instances for record in records):
            raise CoverageError(f"coverage database type option mismatch for {covergroup_type_id}")
        if not merge_instances:
            values = [_record_coverage(record) for record in records]
            return {"coverage": sum(values) / len(values), "merge_instances": 0}
        merged = deepcopy(records[0])
        for record in records[1:]:
            merged = _merge_record_documents(merged, record)
        return {"coverage": _record_coverage(merged), "merge_instances": 1, "points": merged["points"]}

    def coverage(self) -> float:
        """Return the option-weighted aggregate across covergroup types."""
        type_ids = sorted({type_id for type_id, _ in self._records})
        weighted: list[tuple[float, int]] = []
        for type_id in type_ids:
            records = [record.document for (candidate, _), record in self._records.items() if candidate == type_id]
            weight = int(records[0].get("options", {}).get("weight", 1))
            if weight > 0:
                weighted.append((self.type_summary(type_id)["coverage"], weight))
        if not weighted:
            return 100.0
        return sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)


def _merge_record_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge counters from equivalent, independently sampled instances."""
    result = deepcopy(left)
    source_limit = int(result.get("source_limit", 3))
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
        for field in ("source_ids", "illegal_source_ids"):
            merged_sources = {name: list(ids) for name, ids in left_counter.get(field, {}).items()}
            for bin_name, source_ids in right_counter.get(field, {}).items():
                target = merged_sources.setdefault(bin_name, [])
                for source_id in source_ids:
                    if source_id not in target and len(target) < source_limit:
                        target.append(source_id)
            left_counter[field] = dict(sorted(merged_sources.items()))
        left_counter["samples"] = left_counter.get("samples", 0) + right_counter.get("samples", 0)
    return result


def _record_coverage(document: dict[str, Any]) -> float:
    weighted: list[tuple[float, int]] = []
    definitions = {
        **document.get("point_definitions", {}),
        **document.get("cross_definitions", {}),
    }
    for name, definition in definitions.items():
        normal = definition["normal_bins"]
        if not normal:
            value = 100.0
        else:
            hits = document["points"][name].get("hits", {})
            covered = sum(hits.get(bin_name, 0) >= definition["at_least"] for bin_name in normal)
            value = min(100.0, 100.0 * covered / len(normal) * 100.0 / definition["goal"])
        if definition["weight"] > 0:
            weighted.append((value, definition["weight"]))
    if not weighted:
        return 100.0
    raw = sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)
    goal = int(document.get("options", {}).get("goal", 100))
    return min(100.0, raw * 100.0 / goal)
