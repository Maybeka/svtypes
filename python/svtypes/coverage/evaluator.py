"""Pure-Python evaluation of frozen scalar coverage-point IR."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..errors import CoverageError
from ..logic import LogicValue
from .ir import CoverageBinIR, CoverageCrossIR, CoverageIR, CoveragePointIR


def _value(value: Any) -> Any:
    value = value.value if hasattr(value, "value") else value
    if hasattr(value, "value_mask") and not getattr(value, "x_mask", 0) and not getattr(value, "z_mask", 0):
        return value.value_mask
    return value


def eval_expr(expression: Any, context: dict[str, Any]) -> Any:
    """Evaluate the restricted structural CoverageExpr IR."""
    kind = expression["kind"]
    if kind in {"field", "slot", "slot_is_null", "container_values", "container_nullness", "assoc_values", "assoc_nullness", "is_null"}:
        _, field_name = expression["path"].split(".", 1)
        item = context["item"]
        if kind == "is_null":
            descriptor = next((base.__dict__[field_name] for base in type(item).mro() if field_name in base.__dict__), None)
            return int(item.__dict__.get(getattr(descriptor, "_cache_key", "")) is None)
        value = getattr(item, field_name)
        if kind == "field":
            return _value(value)
        if kind == "slot":
            return _value(value[expression["index"]])
        if kind == "slot_is_null":
            return int(value[expression["index"]] is None)
        if kind in {"container_values", "assoc_values"}:
            return value.value
        if kind == "container_nullness":
            return [int(item is None) for item in value.value]
        if kind == "assoc_nullness":
            return [int(item is None) for item in value.value.values()]
    if kind in {"constant", "enum_literal", "parameter_literal"}:
        return expression["value"]
    if kind == "name":
        try:
            return _value(context[expression["name"]])
        except KeyError as error:
            raise CoverageError(f"unknown coverage expression name {expression['name']!r}") from error
    if kind == "attribute":
        return _value(getattr(eval_expr(expression["base"], context), expression["name"]))
    if kind == "subscript":
        base = eval_expr(expression["base"], context)
        index = expression["index"]
        if index.get("kind") == "slice":
            lower = eval_expr(index["lower"], context) if index["lower"] is not None else None
            upper = eval_expr(index["upper"], context) if index["upper"] is not None else None
            return _value(base[slice(lower, upper)])
        return _value(base[eval_expr(index, context)])
    if kind == "unary":
        operand = eval_expr(expression["operand"], context)
        return {"+": lambda: +operand, "-": lambda: -operand, "~": lambda: ~operand, "not": lambda: not operand}[expression["operator"]]()
    if kind == "binary":
        left, right = eval_expr(expression["left"], context), eval_expr(expression["right"], context)
        return {
            "+": lambda: left + right, "-": lambda: left - right, "*": lambda: left * right,
            "/": lambda: left // right, "%": lambda: left % right, "**": lambda: left ** right,
            "&": lambda: left & right, "|": lambda: left | right, "^": lambda: left ^ right,
            "<<": lambda: left << right, ">>": lambda: left >> right,
        }[expression["operator"]]()
    if kind == "boolean":
        values = [bool(eval_expr(item, context)) for item in expression["values"]]
        return all(values) if expression["operator"] == "and" else any(values)
    if kind == "compare":
        left = eval_expr(expression["left"], context)
        for operator, right_node in zip(expression["operators"], expression["comparators"]):
            right = eval_expr(right_node, context)
            matched = {
                "==": left == right, "!=": left != right, "<": left < right, "<=": left <= right,
                ">": left > right, ">=": left >= right,
            }[operator]
            if not matched:
                return False
            left = right
        return True
    if kind == "if":
        return eval_expr(expression["then"] if eval_expr(expression["condition"], context) else expression["else"], context)
    if kind == "tuple":
        return tuple(eval_expr(item, context) for item in expression["items"])
    raise CoverageError(f"unsupported frozen coverage expression kind {kind!r}")


def _matches_selector(value: Any, selector: Any, context: dict[str, Any]) -> bool:
    if selector is None:
        return False
    kind = selector["kind"]
    if kind == "values":
        return any(_matches_selector(value, item, context) for item in selector["items"])
    has_xz = bool(getattr(value, "x_mask", 0) or getattr(value, "z_mask", 0))
    # A range is a 2-state ordering operation.  Explicit singleton/set bins,
    # however, retain four-state equality so ``bins[\"1x\"]`` can describe a
    # Logic value exactly rather than silently becoming unmatchable.
    if has_xz:
        if kind == "range":
            return False
        target = eval_expr(selector, context)
        if isinstance(target, str):
            try:
                target = LogicValue.from_string(target)
            except ValueError:
                return False
        return value == target
    if kind == "range":
        lower = eval_expr(selector["lower"], context) if selector["lower"] is not None else None
        upper = eval_expr(selector["upper"], context) if selector["upper"] is not None else None
        return (lower is None or value >= lower) and (upper is None or value <= upper)
    return value == eval_expr(selector, context)


def _matches_cross_selector(
    selector: Any,
    classified: dict[str, tuple[str, ...] | None],
    values: dict[str, Any],
    queue_values: set[tuple[Any, ...]] | None = None,
) -> bool:
    """Match a frozen static or concrete-queue cross tuple."""
    if not isinstance(selector, dict):
        raise CoverageError("unsupported frozen cross selector")
    if selector.get("kind") == "cross_bin_refs":
        return all(
            reference["bin"] in (classified.get(reference["point"]) or ())
            for reference in selector["items"]
        )
    if selector.get("kind") == "cross_queue_values":
        sample = tuple(_value(values[member]) for member in values)
        if queue_values is None:
            queue_values = {tuple(item) for item in selector["items"]}
        return sample in queue_values
    raise CoverageError("unsupported frozen cross selector")


@dataclass(slots=True)
class PointCounters:
    hits: Counter[str] = field(default_factory=Counter)
    illegal_hits: Counter[str] = field(default_factory=Counter)
    source_ids: dict[str, list[str]] = field(default_factory=dict)
    illegal_source_ids: dict[str, list[str]] = field(default_factory=dict)
    samples: int = 0


@dataclass(slots=True)
class CoverageRuntime:
    """In-memory counters for one frozen covergroup instance."""

    ir: CoverageIR
    counters: dict[str, PointCounters] = field(init=False)
    histories: dict[str, list[Any]] = field(init=False)
    queue_value_sets: dict[tuple[str, str], set[tuple[Any, ...]]] = field(init=False)
    cross_local_illegal_hits: dict[tuple[str, str, str], int] = field(init=False)
    cross_local_illegal_source_ids: dict[tuple[str, str, str], list[str]] = field(init=False)
    enabled: bool = field(init=False, default=True)
    source_limit: int = 3
    sample_count: int = 0

    def __post_init__(self) -> None:
        self.counters = {
            **{point.name: PointCounters() for point in self.ir.points},
            **{cross.name: PointCounters() for cross in self.ir.crosses},
        }
        self.histories = {point.name: [] for point in self.ir.points}
        self.queue_value_sets = {
            (cross.name, bin_.name): {tuple(item) for item in bin_.selector["items"]}
            for cross in self.ir.crosses
            for bin_ in cross.bins
            if isinstance(bin_.selector, dict) and bin_.selector.get("kind") == "cross_queue_values"
        }
        self.cross_local_illegal_hits = {}
        self.cross_local_illegal_source_ids = {}

    def sample(self, context: dict[str, Any], *, case_id: str | None = None) -> None:
        if not self.enabled:
            return
        # This is the covergroup-instance count: one increment per public
        # sample call, independent of how many point slots/container values
        # are classified by that call.
        self.sample_count += 1
        classified: dict[str, tuple[str, ...] | None] = {}
        for point in self.ir.points:
            classified[point.name] = self._sample_point(point, context, case_id)
        for cross in self.ir.crosses:
            self._sample_cross(cross, classified, context, case_id)

    def start(self) -> None:
        self.enabled = True

    def stop(self) -> None:
        self.enabled = False

    def has_illegal_hits(self) -> bool:
        """Whether this runtime has observed any illegal bin."""
        return bool(self.cross_local_illegal_hits) or any(
            counter.illegal_hits for counter in self.counters.values()
        )

    def cross_local_illegal_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return diagnostics for illegal bins belonging only to a cross view."""
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (cross, member, bin_name), hits in sorted(self.cross_local_illegal_hits.items()):
            result.setdefault(cross, {}).setdefault(member, {})[bin_name] = {
                "hits": hits,
                "source_ids": list(self.cross_local_illegal_source_ids.get((cross, member, bin_name), ())),
            }
        return result

    def _sample_point(self, point: CoveragePointIR, context: dict[str, Any], case_id: str | None) -> tuple[str, ...] | None:
        counters = self.counters[point.name]
        if point.iff is not None and not bool(eval_expr(point.iff, context)):
            return None
        try:
            values = eval_expr(point.expression, context)
        except IndexError:
            return None
        if dict(point.options).get("container_value_domain"):
            for value in values:
                self._classify_value(point, counters, _value(value), context, case_id)
            return None
        return self._classify_value(point, counters, values, context, case_id)

    def _classify_value(self, point: CoveragePointIR, counters: PointCounters, value: Any, context: dict[str, Any], case_id: str | None) -> tuple[str, ...] | None:
        counters.samples += 1
        transitions = [bin_ for bin_ in point.bins if bin_.kind == "transition"]
        if transitions:
            history = self.histories[point.name]
            history.append(value)
            history[:] = history[-16:]
            for bin_ in transitions:
                sequences = _transition_sequences(bin_.selector, context)
                if any(len(history) >= len(sequence) and history[-len(sequence):] == sequence for sequence in sequences):
                    counters.hits[bin_.name] += 1
                    _record_source(counters.source_ids, bin_.name, case_id, self.source_limit)
        ignored = [bin_ for bin_ in point.bins if bin_.kind == "ignore" and _matches_selector(value, bin_.selector, context)]
        if ignored:
            return None
        illegal = [bin_ for bin_ in point.bins if bin_.kind == "illegal" and _matches_selector(value, bin_.selector, context)]
        if illegal:
            for bin_ in illegal:
                counters.illegal_hits[bin_.name] += 1
                _record_source(counters.illegal_source_ids, bin_.name, case_id, self.source_limit)
            return None
        normal = [bin_ for bin_ in point.bins if bin_.kind == "normal" and _matches_selector(value, bin_.selector, context)]
        if normal:
            for bin_ in normal:
                counters.hits[bin_.name] += 1
                _record_source(counters.source_ids, bin_.name, case_id, self.source_limit)
            return tuple(bin_.name for bin_ in normal)
        defaults = [bin_ for bin_ in point.bins if bin_.kind == "default"]
        if defaults:
            counters.hits[defaults[0].name] += 1
            _record_source(counters.source_ids, defaults[0].name, case_id, self.source_limit)
            return (defaults[0].name,)
        return ()

    def _sample_cross(
        self,
        cross: CoverageCrossIR,
        classified: dict[str, tuple[str, ...] | None],
        context: dict[str, Any],
        case_id: str | None,
    ) -> None:
        if cross.iff is not None and not bool(eval_expr(cross.iff, context)):
            return
        effective_classified = dict(classified)
        effective_points = {point.name: point for point in self.ir.points}
        for view in cross.member_views:
            effective_points[view.name] = view.point
            effective_classified[view.name] = self._sample_cross_member(
                cross, view.point, context, case_id
            )
        # A cross is eligible only when every member has contributed a normal
        # or default point bin. ``None`` denotes iff/ignore/illegal/transition
        # suppression; an empty tuple denotes an uncovered point value.
        if any(not effective_classified.get(member) for member in cross.members):
            return
        try:
            values = {member: eval_expr(effective_points[member].expression, context) for member in cross.members}
        except IndexError:
            return
        counters = self.counters[cross.name]
        counters.samples += 1
        ignored = [
            bin_ for bin_ in cross.bins
            if bin_.kind == "ignore" and _matches_cross_selector(
                bin_.selector, effective_classified, values, self.queue_value_sets.get((cross.name, bin_.name))
            )
        ]
        if ignored:
            return
        for bin_ in cross.bins:
            if bin_.kind != "normal" or not _matches_cross_selector(
                bin_.selector, effective_classified, values, self.queue_value_sets.get((cross.name, bin_.name))
            ):
                continue
            counters.hits[bin_.name] += 1
            _record_source(counters.source_ids, bin_.name, case_id, self.source_limit)

    def _sample_cross_member(
        self,
        cross: CoverageCrossIR,
        point: CoveragePointIR,
        context: dict[str, Any],
        case_id: str | None,
    ) -> tuple[str, ...] | None:
        """Classify a private cross member without creating public point hits."""
        if point.iff is not None and not bool(eval_expr(point.iff, context)):
            return None
        try:
            value = eval_expr(point.expression, context)
        except IndexError:
            return None
        ignored = [bin_ for bin_ in point.bins if bin_.kind == "ignore" and _matches_selector(value, bin_.selector, context)]
        if ignored:
            return None
        illegal = [bin_ for bin_ in point.bins if bin_.kind == "illegal" and _matches_selector(value, bin_.selector, context)]
        if illegal:
            for bin_ in illegal:
                key = (cross.name, point.name, bin_.name)
                self.cross_local_illegal_hits[key] = self.cross_local_illegal_hits.get(key, 0) + 1
                ids = self.cross_local_illegal_source_ids.setdefault(key, [])
                if case_id is not None and case_id not in ids and len(ids) < self.source_limit:
                    ids.append(case_id)
            return None
        normal = [bin_ for bin_ in point.bins if bin_.kind == "normal" and _matches_selector(value, bin_.selector, context)]
        if normal:
            return tuple(bin_.name for bin_ in normal)
        defaults = [bin_ for bin_ in point.bins if bin_.kind == "default"]
        return (defaults[0].name,) if defaults else ()

    def snapshot(self) -> dict[str, Any]:
        return {
            name: _counter_snapshot(counter)
            for name, counter in sorted(self.counters.items())
        }

    def restore_counter_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Install persisted aggregate counters as a new sampling baseline.

        This deliberately does not restore transition histories, sample logs,
        enabled state, or any object graph.  A persisted database is a
        cumulative statistical starting point, not a suspended simulation.
        """
        if set(snapshot) != set(self.counters):
            raise CoverageError("coverage baseline point layout mismatch")
        restored: dict[str, PointCounters] = {}
        for name, document in snapshot.items():
            if not isinstance(document, dict):
                raise CoverageError(f"coverage baseline counter {name!r} is invalid")
            def counts(field: str) -> Counter[str]:
                value = document.get(field, {})
                if not isinstance(value, dict) or any(
                    not isinstance(key, str) or not isinstance(count, int) or isinstance(count, bool) or count < 0
                    for key, count in value.items()
                ):
                    raise CoverageError(f"coverage baseline {name!r} {field} is invalid")
                return Counter(value)
            def sources(field: str) -> dict[str, list[str]]:
                value = document.get(field, {})
                if not isinstance(value, dict) or any(
                    not isinstance(key, str) or not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
                    for key, ids in value.items()
                ):
                    raise CoverageError(f"coverage baseline {name!r} {field} is invalid")
                return {key: list(ids) for key, ids in value.items()}
            samples = document.get("samples", 0)
            if not isinstance(samples, int) or isinstance(samples, bool) or samples < 0:
                raise CoverageError(f"coverage baseline {name!r} samples is invalid")
            restored[name] = PointCounters(
                hits=counts("hits"), illegal_hits=counts("illegal_hits"),
                source_ids=sources("source_ids"), illegal_source_ids=sources("illegal_source_ids"),
                samples=samples,
            )
        self.counters = restored
        self.histories = {point.name: [] for point in self.ir.points}

    def point_coverage(self, point_name: str) -> float:
        point = next((item for item in self.ir.points if item.name == point_name), None)
        if point is None:
            raise CoverageError(f"unknown coverage point {point_name!r}")
        options = dict(point.options)
        at_least = int(options.get("at_least", 1))
        goal = int(options.get("goal", 100))
        normal = [
            item for item in point.bins
            if item.kind in {"normal", "default", "transition"}
            and not (
                item.kind != "transition"
                and isinstance(item.selector, dict)
                and item.selector.get("kind") == "values"
                and not item.selector.get("items")
            )
        ]
        if not normal:
            return 100.0
        covered = sum(self.counters[point_name].hits[item.name] >= at_least for item in normal)
        return min(100.0, 100.0 * covered / len(normal) * 100.0 / goal)

    def cross_coverage(self, cross_name: str) -> float:
        cross = next((item for item in self.ir.crosses if item.name == cross_name), None)
        if cross is None:
            raise CoverageError(f"unknown coverage cross {cross_name!r}")
        options = dict(cross.options)
        at_least = int(options.get("at_least", 1))
        goal = int(options.get("goal", 100))
        normal = [item for item in cross.bins if item.kind == "normal"]
        if not normal:
            return 100.0
        covered = sum(self.counters[cross_name].hits[item.name] >= at_least for item in normal)
        return min(100.0, 100.0 * covered / len(normal) * 100.0 / goal)

    def coverage(self) -> float:
        weighted: list[tuple[float, int]] = []
        for point in self.ir.points:
            weight = int(dict(point.options).get("weight", 1))
            if weight > 0:
                weighted.append((self.point_coverage(point.name), weight))
        for cross in self.ir.crosses:
            weight = int(dict(cross.options).get("weight", 1))
            if weight > 0:
                weighted.append((self.cross_coverage(cross.name), weight))
        if not weighted:
            return 100.0
        raw = sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)
        goal = int(dict(self.ir.options).get("goal", 100))
        return min(100.0, raw * 100.0 / goal)


def _transition_sequences(selector: Any, context: dict[str, Any]) -> list[list[Any]]:
    if not isinstance(selector, dict) or selector.get("kind") != "values":
        raise CoverageError("transition bin selector must be a finite value sequence")
    sequences: list[list[Any]] = [[]]
    for item in selector["items"]:
        if isinstance(item, dict) and item.get("kind") == "repeat":
            values = _transition_sequences({"kind": "values", "items": [item["term"]]}, context)
            if len(values) != 1 or len(values[0]) != 1:
                raise CoverageError("repeat() term must resolve to one transition value")
            value = values[0][0]
            expansions = [[value] * count for count in range(item["minimum"], item["maximum"] + 1)]
        else:
            expansions = [[eval_expr(item, context)]]
        sequences = [prefix + suffix for prefix in sequences for suffix in expansions]
    return sequences


def _record_source(target: dict[str, list[str]], bin_name: str, case_id: str | None, limit: int) -> None:
    if case_id is None:
        return
    sources = target.setdefault(bin_name, [])
    if case_id not in sources and len(sources) < limit:
        sources.append(case_id)


def _counter_snapshot(counter: PointCounters) -> dict[str, Any]:
    result = {
        "hits": dict(sorted(counter.hits.items())),
        "illegal_hits": dict(sorted(counter.illegal_hits.items())),
        "samples": counter.samples,
    }
    if counter.source_ids:
        result["source_ids"] = {name: list(ids) for name, ids in sorted(counter.source_ids.items())}
    if counter.illegal_source_ids:
        result["illegal_source_ids"] = {name: list(ids) for name, ids in sorted(counter.illegal_source_ids.items())}
    return result
