"""Coverage snapshots, transactional cross-run merge and portable persistence."""

from __future__ import annotations

import json
import weakref
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import CoverageError
from .persistence import decode_database, encode_database, read_database, write_database

_DOCUMENT_ATOMS = frozenset((str, int, float, bool, bytes, type(None)))


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    logical_instance_key: str | None
    document: dict[str, Any]


def _copy_document(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Clone JSON document containers without deepcopy's scalar bookkeeping.

    Retain memoized aliases/cycles and the general deepcopy fallback for values
    outside the persistence data model, rather than changing snapshot semantics.
    """
    kind = type(value)
    if kind in _DOCUMENT_ATOMS:
        return value
    if memo is None:
        memo = {}
    if id(value) in memo:
        return memo[id(value)]
    if kind is dict:
        copied: dict[Any, Any] = {}
        memo[id(value)] = copied
        for key, item in value.items():
            copied[key if type(key) is str else deepcopy(key, memo)] = _copy_document(item, memo)
        return copied
    if kind is list:
        items: list[Any] = []
        memo[id(value)] = items
        items.extend(_copy_document(item, memo) for item in value)
        return items
    return deepcopy(value, memo)


class CoverageDatabase:
    """Collect frozen instance snapshots and merge matching declarations."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, object], CoverageRecord] = {}
        self._live_instances: dict[tuple[str, object], Any] = {}
        self._anonymous_instances: dict[int, tuple[Any, object]] = {}
        self._merge_reports: list[dict[str, Any]] = []

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
        if logical_instance_key is not None and (not isinstance(logical_instance_key, str) or not logical_instance_key):
            raise CoverageError("logical instance key must be a non-empty string")
        per_instance = bool(getattr(getattr(instance, "option", None), "per_instance", 0))
        if per_instance and logical_instance_key is None:
            raise CoverageError("per-instance coverage requires a logical instance key")
        document = instance.snapshot_document()
        type_id = document["covergroup_type_id"]
        # A non-per-instance result can contribute to type coverage without a
        # user-visible identity.  Keep such live records separate internally;
        # never expose this ephemeral implementation key in a snapshot.
        storage_key: object = logical_instance_key
        if logical_instance_key is None:
            prior = self._anonymous_instances.get(id(instance))
            storage_key = prior[1] if prior is not None and prior[0]() is instance else object()
        key = (type_id, storage_key)
        _validate_type(self._records, document)
        current = self._records.get(key)
        if current is not None and current.document["declaration_semantic_digest"] != document["declaration_semantic_digest"]:
            raise CoverageError(f"coverage database declaration mismatch for {type_id}")
        if current is not None and current.document.get("definition") != document.get("definition"):
            raise CoverageError(f"coverage database definition mismatch for {type_id}")
        if current is not None and current.document.get("instance_layout_digest") != document.get("instance_layout_digest"):
            raise CoverageError(f"coverage database instance layout mismatch for {type_id}")
        current_instance = self._live_instances.get(key)
        if current_instance is not None and current_instance() is not instance:
            raise CoverageError(f"coverage database logical instance key is already registered for {type_id}")
        self._records[key] = CoverageRecord(logical_instance_key, deepcopy(document))
        try:
            reference = weakref.ref(instance)
        except TypeError:
            reference = lambda: instance
        self._live_instances[key] = reference
        if logical_instance_key is None:
            self._anonymous_instances[id(instance)] = (reference, storage_key)

    def merge(self, other: "CoverageDatabase", *, mode: str = "strict",
              reject_conflicts: bool = False) -> dict[str, Any]:
        """Atomically merge another run; ``mode='master'`` keeps this definition.

        Anonymous contributors are always independent. Master projection keeps
        all master bins and reports incompatible points and dependent crosses.
        ``reject_conflicts`` makes projection conflicts an atomic failure.
        """
        if mode not in {"strict", "master"}:
            raise CoverageError("coverage merge mode must be 'strict' or 'master'")
        pending = dict(self._records)
        report: dict[str, Any] = {"format": "svtypes.coverage.merge-report/v1",
                                 "mode": "strict" if mode == "strict" else "master-projection",
                                 "conflicts": [], "merged_items": []}
        for (type_id, _), record in other._records.items():
            key = (type_id, record.logical_instance_key if record.logical_instance_key is not None else object())
            current = pending.get(key)
            if mode == "strict":
                _validate_type(pending, record.document)
                if current is not None and current.document.get("instance_layout_digest") != record.document.get("instance_layout_digest"):
                    raise CoverageError(f"coverage database instance layout mismatch for {type_id}")
                merged = deepcopy(record.document) if current is None else _merge_record_documents(current.document, record.document)
            else:
                if current is None:
                    candidates = [value for (candidate, _), value in self._records.items() if candidate == type_id]
                    if record.logical_instance_key is not None or not candidates:
                        _conflict(report, record, None, "instance absent from master" if candidates else "type absent from master")
                        continue
                    matching = [value for value in candidates if value.document.get("instance_layout_digest") == record.document.get("instance_layout_digest")]
                    if not matching and len({value.document.get("instance_layout_digest") for value in candidates}) > 1:
                        _conflict(report, record, None, "ambiguous master instance layout")
                        continue
                    current = CoverageRecord(None, _empty_counters((matching or candidates)[0].document))
                merged, items = _project_record(current.document, record, report)
                if not items:
                    continue
                report["merged_items"].append({"covergroup_type_id": type_id,
                                               "logical_instance_key": record.logical_instance_key,
                                               "items": sorted(items)})
            pending[key] = CoverageRecord(record.logical_instance_key, merged)
        if reject_conflicts and report["conflicts"]:
            raise CoverageError(f"coverage master projection has {len(report['conflicts'])} conflict(s)")
        self._records = pending
        self._merge_reports.extend(deepcopy(other._merge_reports))
        if mode == "master":
            self._merge_reports.append(deepcopy(report))
        return report

    @property
    def merge_reports(self) -> tuple[dict[str, Any], ...]:
        """Detached, persisted evidence for explicit master projections."""
        return tuple(deepcopy(self._merge_reports))

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
        instance.runtime.restore_counter_snapshot(
            deepcopy(baseline["points"]),
            cross_local_illegal=deepcopy(baseline.get("cross_local_illegal_hits", {})),
        )
        instance.runtime.sample_count = int(baseline.get("sample_count", 0))

    def import_ucis(self, xml: str, *, bindings: Mapping[str, Any], defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        """Import losslessly bindable UCIS records into this database.

        UCIS does not carry SvTypes' declaration digest or instance-layout
        digest.  The caller must therefore provide the live frozen instance
        for each UCIS logical-instance key.  Any missing or incompatible item
        fails instead of guessing an identity or silently creating a partial
        baseline.
        """
        from .ucis import import_ucis, validate_binding

        records, report = import_ucis(xml, defaults=defaults)
        pending = dict(self._records)
        for external in records:
            key = external.get("logical_instance_key")
            if not isinstance(key, str) or not key:
                raise CoverageError("UCIS record has no logical instance key")
            target = bindings.get(key)
            if target is None:
                raise CoverageError(f"UCIS record {key!r} has no declared instance binding")
            instance = getattr(target, "instance", target)
            document = deepcopy(instance.snapshot_document())
            if external.get("covergroup_type_id") != document["covergroup_type_id"]:
                raise CoverageError(f"UCIS record {key!r} covergroup type mismatch")
            losses = validate_binding(external, document)
            report["losses"].extend(losses)
            imported = {**external["points"], **external["crosses"]}
            expected = {**document["point_definitions"], **document["cross_definitions"]}
            extra_items = set(imported).difference(expected)
            if extra_items:
                raise CoverageError(
                    f"UCIS record {key!r} has coverage items absent from the bound instance: {sorted(extra_items)}"
                )
            bin_kinds = {
                item["name"]: {bin_["name"]: bin_["kind"] for bin_ in item.get("bins", [])}
                for collection in ("points", "crosses")
                for item in document.get("instance_definition", document["definition"]).get(collection, [])
            }
            for name, definition in expected.items():
                kinds = bin_kinds.get(name, {})
                bins = imported.get(name, {})
                extra = set(bins).difference(kinds)
                if extra:
                    raise CoverageError(f"UCIS record {key!r} has unknown bins for {name!r}: {sorted(extra)}")
                hits = {bin_name: 0 for bin_name in definition["normal_bins"]}
                illegal: dict[str, int] = {}
                for bin_name, count in bins.items():
                    kind = kinds.get(bin_name)
                    if kind in {"normal", "default"}:
                        hits[bin_name] = count
                    elif kind == "illegal":
                        illegal[bin_name] = count
                counter = document["points"][name]
                counter["hits"] = dict(sorted(hits.items()))
                counter["illegal_hits"] = dict(sorted(illegal.items()))
                counter["source_ids"] = {}
                counter["illegal_source_ids"] = {}
                counter["samples"] = 0
            document["sample_count"] = 0
            document["cross_local_illegal_hits"] = {}
            document["import_provenance"] = [{"format": "UCIS 1.0",
                "covergroup_type_id": external["covergroup_type_id"],
                "logical_instance_key": key,
                "losses": deepcopy([*external.get("import_losses", []), *losses])}]
            _validate_type(pending, document)
            storage_key = (document["covergroup_type_id"], key)
            if storage_key in pending:
                raise CoverageError(f"UCIS instance {key!r} already exists; import into a separate database before merge")
            pending[storage_key] = CoverageRecord(key, document)
        self._records = pending
        return report

    def snapshot_document(self) -> dict[str, Any]:
        return _copy_document(self._serialization_document())

    def snapshot_json(self) -> str:
        return json.dumps(self._serialization_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _serialization_document(self) -> dict[str, Any]:
        """Borrow owned record data only for synchronous read-only encoding.

        Public snapshots remain detached. Encoders accept plain JSON data and
        neither mutate it nor return references to its nested structures.
        """
        result = {"records": [
            {"logical_instance_key": record.logical_instance_key, **record.document}
            for _, record in sorted(self._records.items(), key=lambda item: (item[0][0], item[1].logical_instance_key or ""))
        ]}
        if self._merge_reports:
            result["merge_reports"] = self._merge_reports
        return result

    def to_bytes(self) -> bytes:
        """Export portable aggregate coverage counters for cross-run merge."""
        return encode_database(self._serialization_document())

    @classmethod
    def from_bytes(cls, data: bytes) -> "CoverageDatabase":
        database = cls()
        database._load_document(decode_database(data))
        return database

    def write(self, path: str) -> None:
        write_database(path, self._serialization_document())

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
            if not isinstance(type_id, str) or not type_id or not (key is None or isinstance(key, str) and key):
                raise CoverageError("coverage database record identity is invalid")
            _validate_type(self._records, record_document)
            storage_key = key if key is not None else object()
            if (type_id, storage_key) in self._records:
                raise CoverageError("coverage database contains a duplicate logical instance")
            self._records[(type_id, storage_key)] = CoverageRecord(key, record_document)
        self._merge_reports = deepcopy(document.get("merge_reports", []))

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

    def has_illegal_hits(self) -> bool:
        """Return whether any recorded coverage item has an illegal hit."""
        return any(record.document.get("cross_local_illegal_hits") for record in self._records.values()) or any(
            bool(counter.get("illegal_hits", {}))
            for record in self._records.values()
            for counter in record.document.get("points", {}).values()
        )


def _validate_type(records, document):
    type_id = document["covergroup_type_id"]
    for (candidate, _), record in records.items():
        if candidate != type_id:
            continue
        for field, label in (("declaration_semantic_digest", "declaration"), ("definition", "definition")):
            if record.document.get(field) != document.get(field):
                raise CoverageError(f"coverage database {label} mismatch for {type_id}")


def _conflict(report, record, item, reason, *, master=None, incoming=None, dependencies=()):
    report["conflicts"].append({
        "covergroup_type_id": record.document["covergroup_type_id"],
        "logical_instance_key": record.logical_instance_key,
        "item": item, "reason": reason, "dependencies": list(dependencies),
        "master": deepcopy(master), "incoming": deepcopy(incoming),
    })


def _empty_counters(document):
    result = deepcopy(document)
    result.pop("import_provenance", None)
    result["sample_count"] = 0
    result["cross_local_illegal_hits"] = {}
    for counter in result["points"].values():
        counter.update(hits={}, illegal_hits={}, source_ids={}, illegal_source_ids={}, samples=0)
    return result


def _project_record(master, record, report):
    incoming = record.document
    left, right = master["definition"], incoming["definition"]
    # Group-level semantics cannot be resolved by dropping individual points.
    left_group = {k: v for k, v in left.items() if k not in {"points", "crosses"}}
    right_group = {k: v for k, v in right.items() if k not in {"points", "crosses"}}
    if left_group != right_group:
        _conflict(report, record, None, "covergroup semantics differ", master=left_group, incoming=right_group)
        return master, set()
    if (master["declaration_semantic_digest"] == incoming["declaration_semantic_digest"]
            and master.get("instance_layout_digest") != incoming.get("instance_layout_digest")):
        _conflict(report, record, None, "instance layout mismatch",
                  master=master.get("instance_definition", left), incoming=incoming.get("instance_definition", right))
        return master, set()
    # Old static records can use the template; parameter-dependent records need
    # concrete definitions, never guesses based on names/counts alone.
    def concrete(document):
        if "instance_definition" in document:
            return document["instance_definition"]
        definition = document["definition"]
        if definition.get("constructor_parameters"):
            return None
        return definition
    left_layout, right_layout = concrete(master), concrete(incoming)
    if left_layout is None or right_layout is None:
        _conflict(report, record, None, "concrete instance definition is unavailable")
        return master, set()
    changed = set()
    accepted = set()
    for category in ("points", "crosses"):
        ls = {item["name"]: item for item in left.get(category, [])}
        rs = {item["name"]: item for item in right.get(category, [])}
        lc = {item["name"]: item for item in left_layout.get(category, [])}
        rc = {item["name"]: item for item in right_layout.get(category, [])}
        for name in sorted(ls.keys() | rs.keys()):
            dependencies = sorted(set((ls.get(name) or rs[name]).get("members", ())) & changed)
            if ls.get(name) != rs.get(name) or lc.get(name) != rc.get(name) or dependencies:
                changed.add(name)
                _conflict(report, record, name, "dependent point changed" if dependencies else "coverage item differs",
                          master=lc.get(name), incoming=rc.get(name), dependencies=dependencies)
            else:
                accepted.add(name)
    if not accepted:
        return master, accepted
    projected = _empty_counters(master)
    for name in accepted:
        projected["points"][name] = deepcopy(incoming["points"][name])
    projected["sample_count"] = incoming.get("sample_count", 0)
    if incoming.get("import_provenance"):
        projected["import_provenance"] = deepcopy(incoming["import_provenance"])
    projected["cross_local_illegal_hits"] = {
        name: deepcopy(value) for name, value in incoming.get("cross_local_illegal_hits", {}).items() if name in accepted
    }
    merged = _merge_record_documents(master, projected)
    for name in set(master["points"]) - accepted:
        merged["points"][name] = deepcopy(master["points"][name])
    return merged, accepted


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
    result["sample_count"] = int(left.get("sample_count", 0)) + int(right.get("sample_count", 0))
    for cross, members in right.get("cross_local_illegal_hits", {}).items():
        target_members = result.setdefault("cross_local_illegal_hits", {}).setdefault(cross, {})
        for member, bins in members.items():
            target_bins = target_members.setdefault(member, {})
            for name, counter in bins.items():
                target = target_bins.setdefault(name, {"hits": 0, "source_ids": []})
                target["hits"] += counter["hits"]
                for source in counter.get("source_ids", []):
                    if source not in target["source_ids"] and len(target["source_ids"]) < source_limit:
                        target["source_ids"].append(source)
    if right.get("import_provenance"):
        result.setdefault("import_provenance", []).extend(deepcopy(right["import_provenance"]))
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
