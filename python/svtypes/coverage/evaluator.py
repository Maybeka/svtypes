"""Pure-Python evaluation of frozen scalar coverage-point IR."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..errors import CoverageError
from .ir import CoverageBinIR, CoverageIR, CoveragePointIR


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def eval_expr(expression: Any, context: dict[str, Any]) -> Any:
    """Evaluate the restricted structural CoverageExpr IR."""
    kind = expression["kind"]
    if kind == "constant":
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
    if kind == "range":
        lower = eval_expr(selector["lower"], context) if selector["lower"] is not None else None
        upper = eval_expr(selector["upper"], context) if selector["upper"] is not None else None
        return (lower is None or value >= lower) and (upper is None or value <= upper)
    return value == eval_expr(selector, context)


@dataclass(slots=True)
class PointCounters:
    hits: Counter[str] = field(default_factory=Counter)
    illegal_hits: Counter[str] = field(default_factory=Counter)
    samples: int = 0


@dataclass(slots=True)
class CoverageRuntime:
    """In-memory counters for one frozen covergroup instance."""

    ir: CoverageIR
    counters: dict[str, PointCounters] = field(init=False)

    def __post_init__(self) -> None:
        self.counters = {point.name: PointCounters() for point in self.ir.points}

    def sample(self, context: dict[str, Any]) -> None:
        for point in self.ir.points:
            self._sample_point(point, context)

    def _sample_point(self, point: CoveragePointIR, context: dict[str, Any]) -> None:
        counters = self.counters[point.name]
        if point.iff is not None and not bool(eval_expr(point.iff, context)):
            return
        value = eval_expr(point.expression, context)
        counters.samples += 1
        ignored = [bin_ for bin_ in point.bins if bin_.kind == "ignore" and _matches_selector(value, bin_.selector, context)]
        if ignored:
            return
        illegal = [bin_ for bin_ in point.bins if bin_.kind == "illegal" and _matches_selector(value, bin_.selector, context)]
        if illegal:
            for bin_ in illegal:
                counters.illegal_hits[bin_.name] += 1
            return
        normal = [bin_ for bin_ in point.bins if bin_.kind == "normal" and _matches_selector(value, bin_.selector, context)]
        if normal:
            for bin_ in normal:
                counters.hits[bin_.name] += 1
            return
        defaults = [bin_ for bin_ in point.bins if bin_.kind == "default"]
        if defaults:
            counters.hits[defaults[0].name] += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            name: {
                "hits": dict(sorted(counter.hits.items())),
                "illegal_hits": dict(sorted(counter.illegal_hits.items())),
                "samples": counter.samples,
            }
            for name, counter in sorted(self.counters.items())
        }
