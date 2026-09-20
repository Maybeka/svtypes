"""Embedded-covergroup declaration descriptors and instance lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import functools
import weakref
from collections.abc import Mapping
from typing import Any, Callable, Generic, TypeVar, get_args, get_type_hints, overload

from ..errors import CoverageError
from .canonical import semantic_digest
from .context import coverage_case_name
from .ir import CoverageBinIR, CoverageCrossIR, CoverageIR, CoveragePointIR, CrossQueueFunctionIR
from .limits import MAX_CROSS_QUEUE_TUPLES


T = TypeVar("T")


_GROUP_OPTION_DEFAULTS = {
    "name": None,
    "comment": "",
    "per_instance": 0,
    "get_inst_coverage": 0,
    "weight": 1,
    "goal": 100,
    "at_least": 1,
    "auto_bin_max": 64,
    "detect_overlap": 0,
    "cross_num_print_missing": 0,
}


class CoverageInstanceOption:
    """Runtime covergroup ``option`` view with SVTypes' frozen-field boundary."""

    def __init__(self, declaration_options: tuple[tuple[str, Any], ...]) -> None:
        object.__setattr__(self, "_values", {**_GROUP_OPTION_DEFAULTS, **dict(declaration_options)})

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._values:
            raise AttributeError(f"unknown coverage option {name!r}")
        if name == "name":
            if value is not None and (not isinstance(value, str) or not value):
                raise CoverageError("coverage option.name must be a non-empty string or None")
        elif name == "comment":
            if not isinstance(value, str):
                raise CoverageError("coverage option.comment must be a string")
        else:
            raise CoverageError(f"SVT-COV-OPTION-FROZEN: option.{name} is frozen after instantiate")
        self._values[name] = value


@dataclass(slots=True)
class CoverageSampleLog:
    max_records: int
    max_bytes: int
    records: list[dict[str, Any]]
    used_bytes: int = 0

    def append(self, record: dict[str, Any]) -> None:
        if len(self.records) >= self.max_records:
            return
        size = len(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if self.used_bytes + size > self.max_bytes:
            return
        self.records.append(record)
        self.used_bytes += size


class CoverInput(Generic[T]):
    """Type-only marker for an embedded covergroup constructor input."""


class CoverRef(Generic[T]):
    """Type-only marker for an embedded covergroup reference input."""


@dataclass(slots=True, weakref_slot=True, eq=False)
class CoverGroupInstance:
    """One instantiated embedded covergroup.

    The 1.8 evaluator owns sample classification and counters; this lifecycle
    object deliberately contains only frozen constructor bindings for now.
    """

    declaration: "CoverGroupDeclaration"
    host: Any
    constructor_actuals: tuple[Any, ...]
    constructor_named_actuals: tuple[tuple[str, Any], ...]
    runtime: Any = None
    instance_ir: CoverageIR | None = None
    option: CoverageInstanceOption | None = None
    logical_instance_key: str | None = None
    sample_log: CoverageSampleLog | None = None

    def __post_init__(self) -> None:
        from .evaluator import CoverageRuntime

        bindings = {name: value for name, value in self.constructor_named_actuals}
        for formal, value in zip(self.declaration.ir.constructor_parameters, self.constructor_actuals):
            bindings.setdefault(formal.name, value)
        self.instance_ir = _materialize_ir(
            self.declaration.ir,
            bindings,
            _parameter_bindings(type(self.host)),
        )
        self.runtime = CoverageRuntime(self.instance_ir)
        self.option = CoverageInstanceOption(self.declaration.ir.options)
        instances = getattr(self.declaration, "_instances", None)
        if instances is None:
            instances = weakref.WeakSet()
            setattr(self.declaration, "_instances", instances)
        instances.add(self)

    def sample(self, *args: Any, **kwargs: Any) -> None:
        if "case_id" in kwargs or "case_name" in kwargs:
            raise CoverageError("coverage case name is process-wide; use set_coverage_case_name() before sampling")
        case_id = coverage_case_name()
        formals = self.declaration.ir.sample_parameters
        if len(args) > len(formals):
            raise CoverageError(f"coverage sample {self.declaration.qualified_name} has too many positional arguments")
        values = {name: value for name, value in self.constructor_named_actuals}
        for formal, value in zip(self.declaration.ir.constructor_parameters, self.constructor_actuals):
            if formal.name in values:
                raise CoverageError(f"coverage constructor {self.declaration.qualified_name} binds {formal.name!r} twice")
            values[formal.name] = value
        for formal in self.declaration.ir.reference_parameters:
            values[formal.name] = getattr(self.host, formal.name)
        for formal, value in zip(formals, args):
            values[formal.name] = value
        for name, value in kwargs.items():
            if name not in {formal.name for formal in formals} or name in values:
                raise CoverageError(f"coverage sample {self.declaration.qualified_name} has invalid argument {name!r}")
            values[name] = value
        missing = [formal.name for formal in formals if formal.name not in values]
        if missing:
            raise CoverageError(f"coverage sample {self.declaration.qualified_name} is missing {missing[0]!r}")
        values["self"] = self.host
        values["item"] = self.host
        if self.sample_log is not None:
            self.sample_log.append({
                "case_id": case_id,
                "values": {name: _layout_value(value) for name, value in values.items() if name not in {"self", "item"}},
            })
        self.runtime.sample(values, case_id=case_id)

    def enable_sample_log(self, *, max_records: int = 1024, max_bytes: int = 1 << 20) -> None:
        if any(counter.samples for counter in self.runtime.counters.values()):
            raise CoverageError("coverage sample log must be enabled before first sample")
        if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records <= 0:
            raise CoverageError("coverage sample log max_records must be a positive integer")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise CoverageError("coverage sample log max_bytes must be a positive integer")
        self.sample_log = CoverageSampleLog(max_records, max_bytes, [])

    def configure_case_sources(self, limit: int) -> None:
        if any(counter.samples for counter in self.runtime.counters.values()):
            raise CoverageError("coverage case source limit must be configured before first sample")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise CoverageError("coverage case source limit must be a positive integer")
        self.runtime.source_limit = limit

    def sample_log_snapshot(self) -> list[dict[str, Any]]:
        return [] if self.sample_log is None else json.loads(json.dumps(self.sample_log.records))

    @property
    def instance_layout_digest(self) -> str:
        """Digest this instance's concrete materialized coverage layout.

        Constructor actuals are intentionally not hashed on their own: two
        actual sets that yield the same bins have the same instance layout.
        """
        instance_ir = self.instance_ir or self.declaration.ir
        return semantic_digest({"instance_layout": instance_ir.definition_snapshot()})

    def bind_logical_instance(self, key: str) -> None:
        if not isinstance(key, str) or not key:
            raise CoverageError("logical instance key must be a non-empty string")
        if any(counter.samples for counter in self.runtime.counters.values()):
            raise CoverageError("logical instance key must be bound before first coverage sample")
        if self.logical_instance_key is not None and self.logical_instance_key != key:
            raise CoverageError("logical instance key is already bound")
        self.logical_instance_key = key

    def snapshot(self) -> dict[str, Any]:
        return self.runtime.snapshot()

    def get_coverage(self) -> float:
        """Return cumulative coverage for this covergroup declaration."""
        type_coverage = getattr(self.declaration, "type_coverage", None)
        return self.runtime.coverage() if type_coverage is None else type_coverage()

    def get_inst_coverage(self) -> float:
        """Return coverage from this instance's own bins and counters."""
        return self.runtime.coverage()

    def has_illegal_hits(self) -> bool:
        """Return whether this covergroup instance observed an illegal bin."""
        return self.runtime.has_illegal_hits()

    def cross_local_illegal_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return diagnostics for cross-private member ``illegal_bins`` hits."""
        return self.runtime.cross_local_illegal_snapshot()

    @property
    def sample_count(self) -> int:
        """Number of enabled public ``sample()`` calls on this instance."""
        return self.runtime.sample_count

    def start(self) -> None:
        self.runtime.start()

    def stop(self) -> None:
        self.runtime.stop()

    def set_inst_name(self, name: str) -> None:
        assert self.option is not None
        self.option.name = name

    def snapshot_document(self) -> dict[str, Any]:
        point_definitions = {}
        assert self.instance_ir is not None
        for point in self.instance_ir.points:
            options = dict(point.options)
            point_definitions[point.name] = {
                "at_least": int(options.get("at_least", 1)),
                "goal": int(options.get("goal", 100)),
                "weight": int(options.get("weight", 1)),
                "normal_bins": [
                    bin_.name for bin_ in point.bins
                    if bin_.kind in {"normal", "default"}
                    and not (isinstance(bin_.selector, dict) and bin_.selector.get("kind") == "values" and not bin_.selector.get("items"))
                ],
            }
        cross_definitions = {}
        for cross in self.instance_ir.crosses:
            options = dict(cross.options)
            cross_definitions[cross.name] = {
                "at_least": int(options.get("at_least", 1)),
                "goal": int(options.get("goal", 100)),
                "weight": int(options.get("weight", 1)),
                "normal_bins": [bin_.name for bin_ in cross.bins if bin_.kind == "normal"],
            }
        return {
            "covergroup_type_id": self.declaration.ir.covergroup_type_id,
            "declaration_semantic_digest": self.declaration.ir.declaration_semantic_digest,
            "definition": self.declaration.ir.definition_snapshot(),
            "instance_layout_digest": self.instance_layout_digest,
            "instance_name": self.option.name if self.option is not None else None,
            "comment": self.option.comment if self.option is not None else "",
            "options": dict(self.declaration.ir.options),
            "type_options": dict(self.declaration.ir.type_options),
            "source_limit": self.runtime.source_limit,
            "sample_count": self.runtime.sample_count,
            "point_definitions": point_definitions,
            "cross_definitions": cross_definitions,
            "cross_local_illegal_hits": self.runtime.cross_local_illegal_snapshot(),
            "points": self.snapshot(),
        }

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BoundCoverGroup:
    """A declaration slot bound to exactly one host object."""

    __slots__ = ("_declaration", "_host", "_instance")

    def __init__(self, declaration: "CoverGroupDeclaration", host: Any) -> None:
        self._declaration = declaration
        self._host = host
        self._instance: CoverGroupInstance | None = None

    @property
    def declaration(self) -> "CoverGroupDeclaration":
        return self._declaration

    @property
    def instantiated(self) -> bool:
        return self._instance is not None

    @property
    def instance(self) -> CoverGroupInstance:
        if self._instance is None:
            raise CoverageError(
                f"coverage group {self._declaration.qualified_name} is not instantiated"
            )
        return self._instance

    def instantiate(self, *actuals: Any, **named_actuals: Any) -> CoverGroupInstance:
        if not getattr(self._host, "_svtypes_coverage_construction_depth", 0):
            raise CoverageError(
                f"coverage group {self._declaration.qualified_name} can only be instantiated "
                "from its host __init__"
            )
        if _has_coverage_init(type(self._host)) and not getattr(self._host, "_svtypes_coverage_init_depth", 0):
            raise CoverageError(
                f"coverage group {self._declaration.qualified_name} can only be instantiated "
                "from a @coverage_init method"
            )
        if self._instance is not None:
            raise CoverageError(
                f"coverage group {self._declaration.qualified_name} is already instantiated"
            )
        ir = self._declaration.freeze()
        formals = ir.constructor_parameters
        if len(actuals) > len(formals):
            raise CoverageError(
                f"coverage constructor {self._declaration.qualified_name} has too many positional arguments"
            )
        names = {formal.name for formal in formals}
        unknown = next((name for name in named_actuals if name not in names), None)
        if unknown is not None:
            raise CoverageError(
                f"coverage constructor {self._declaration.qualified_name} has unknown argument {unknown!r}"
            )
        positional_names = {formal.name for formal in formals[:len(actuals)]}
        duplicate = next((name for name in named_actuals if name in positional_names), None)
        if duplicate is not None:
            raise CoverageError(
                f"coverage constructor {self._declaration.qualified_name} binds {duplicate!r} twice"
            )
        missing = next((formal.name for formal in formals if formal.name not in positional_names and formal.name not in named_actuals), None)
        if missing is not None:
            raise CoverageError(
                f"coverage constructor {self._declaration.qualified_name} is missing {missing!r}"
            )
        resolved_actuals = {
            formal.name: actuals[index] if index < len(actuals) else named_actuals[formal.name]
            for index, formal in enumerate(formals)
        }
        for formal in formals:
            _validate_cover_input_actual(self._declaration, formal.name, formal.type_name, resolved_actuals[formal.name])
        self._instance = CoverGroupInstance(
            declaration=self._declaration,
            host=self._host,
            constructor_actuals=tuple(actuals),
            constructor_named_actuals=tuple(sorted(named_actuals.items())),
        )
        return self._instance

    def sample(self, *args: Any, **kwargs: Any) -> None:
        self.instance.sample(*args, **kwargs)

    def get_coverage(self) -> float:
        return self.instance.get_coverage()

    def get_inst_coverage(self) -> float:
        return self.instance.get_inst_coverage()

    def has_illegal_hits(self) -> bool:
        return self.instance.has_illegal_hits()

    def cross_local_illegal_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self.instance.cross_local_illegal_snapshot()

    @property
    def sample_count(self) -> int:
        """Number of enabled public ``sample()`` calls on this instance."""
        return self.instance.sample_count

    def start(self) -> None:
        self.instance.start()

    def stop(self) -> None:
        self.instance.stop()

    def set_inst_name(self, name: str) -> None:
        self.instance.set_inst_name(name)

    def bind_logical_instance(self, key: str) -> None:
        self.instance.bind_logical_instance(key)

    def enable_sample_log(self, *, max_records: int = 1024, max_bytes: int = 1 << 20) -> None:
        self.instance.enable_sample_log(max_records=max_records, max_bytes=max_bytes)

    def sample_log_snapshot(self) -> list[dict[str, Any]]:
        return self.instance.sample_log_snapshot()

    def configure_case_sources(self, limit: int) -> None:
        self.instance.configure_case_sources(limit)

    @property
    def option(self) -> CoverageInstanceOption:
        return self.instance.option

    def snapshot_document(self) -> dict[str, Any]:
        return self.instance.snapshot_document()

    def snapshot_json(self) -> str:
        return self.instance.snapshot_json()


class CoverGroupDeclaration:
    """A class-level embedded-covergroup declaration descriptor.

    The decorated function is never called by descriptor access.  It is kept
    as source for the restricted declaration compiler implemented by the next
    1.8 slice.
    """

    def __init__(self, function: Callable[..., Any]) -> None:
        if not callable(function):
            raise TypeError("@covergroup must decorate an instance function")
        self.function = function
        self.name = function.__name__
        self.owner: type[Any] | None = None
        self._storage_key = f"_svtypes_coverage_{self.name}"
        self._ir: CoverageIR | None = None
        self._instances: weakref.WeakSet[CoverGroupInstance] = weakref.WeakSet()

    def __set_name__(self, owner: type[Any], name: str) -> None:
        self.owner = owner
        self.name = name
        self._storage_key = f"_svtypes_coverage_{name}"

    @property
    def qualified_name(self) -> str:
        owner_name = self.owner.__name__ if self.owner is not None else "<unbound>"
        return f"{owner_name}.{self.name}"

    @property
    def ir(self) -> CoverageIR:
        if self._ir is None:
            raise CoverageError(f"coverage declaration {self.qualified_name} is not frozen")
        return self._ir

    def freeze(self) -> CoverageIR:
        if self._ir is None:
            from .frontend import compile_declaration

            self._ir = compile_declaration(self)
        return self._ir

    def type_coverage(self) -> float:
        """Compute the LRM-shaped cumulative coverage across live instances."""
        from .database import CoverageDatabase

        database = CoverageDatabase()
        for instance in self._instances:
            database.record(instance)
        if not self._instances:
            return 100.0
        return database.type_summary(self.ir.covergroup_type_id)["coverage"]

    @overload
    def __get__(self, host: None, owner: type[Any] | None = None) -> "CoverGroupDeclaration": ...

    @overload
    def __get__(self, host: Any, owner: type[Any] | None = None) -> BoundCoverGroup: ...

    def __get__(self, host: Any, owner: type[Any] | None = None) -> "CoverGroupDeclaration | BoundCoverGroup":
        if host is None:
            return self
        try:
            return host.__dict__[self._storage_key]
        except KeyError as error:
            raise CoverageError(
                f"coverage group {self.qualified_name} is unavailable before SvObject initialization"
            ) from error

    def __set__(self, host: Any, value: Any) -> None:
        raise AttributeError(
            f"coverage group {self.qualified_name} is read-only; use .instantiate()"
        )


def covergroup(function: Callable[..., Any]) -> CoverGroupDeclaration:
    """Declare an embedded covergroup on an :class:`SvObject` subclass."""
    return CoverGroupDeclaration(function)


def coverage_init(function: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a restricted class method as an embedded-coverage initializer.

    The frontend reads this method's source to produce the corresponding SV
    initializer.  At Python runtime the wrapper supplies an explicit token so
    a class that opts into this API cannot instantiate its covergroups from an
    arbitrary helper during construction.
    """
    if not callable(function):
        raise TypeError("@coverage_init must decorate an instance function")

    @functools.wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        depth = getattr(self, "_svtypes_coverage_init_depth", 0)
        object.__setattr__(self, "_svtypes_coverage_init_depth", depth + 1)
        try:
            return function(self, *args, **kwargs)
        finally:
            object.__setattr__(self, "_svtypes_coverage_init_depth", depth)

    setattr(wrapped, "_svtypes_coverage_init", True)
    return wrapped


def _has_coverage_init(cls: type[Any]) -> bool:
    return any(
        getattr(value, "_svtypes_coverage_init", False)
        for base in cls.mro()
        for value in base.__dict__.values()
    )


def coverage_initializers(owner: type[Any]) -> tuple[Any, ...]:
    """Return frozen source-only initializer mappings owned by *owner*."""
    from .frontend import compile_coverage_initializers

    return compile_coverage_initializers(owner)


def preview_coverage_layout(
    owner: type[Any],
    covergroup_name: str,
    /,
    *,
    parameter_actuals: Mapping[str, Any] | None = None,
    **arguments: Any,
) -> CoverageIR:
    """Materialize one covergroup directly from its ``CoverInput`` signature.

    This pure design-tool API deliberately does not inspect ``@coverage_init``:
    initializer methods define Python runtime and generated-SV construction,
    while this API accepts exactly the selected covergroup's CoverInput values.
    CoverRef bindings remain declaration-owned.
    """
    declarations = {
        name: value for base in reversed(owner.mro())
        for name, value in base.__dict__.items() if isinstance(value, CoverGroupDeclaration)
    }
    declaration = declarations.get(covergroup_name)
    if declaration is None:
        raise CoverageError(f"unknown covergroup {owner.__name__}.{covergroup_name}")
    ir = declaration.freeze()
    expected = {parameter.name for parameter in ir.constructor_parameters}
    if set(arguments) != expected:
        missing = sorted(expected.difference(arguments))
        unknown = sorted(set(arguments).difference(expected))
        detail = f"missing {missing[0]!r}" if missing else f"unknown {unknown[0]!r}"
        raise CoverageError(f"covergroup {owner.__name__}.{covergroup_name} has {detail}")
    for formal in ir.constructor_parameters:
        _validate_cover_input_actual(declaration, formal.name, formal.type_name, arguments[formal.name])
    return _materialize_ir(
        ir,
        dict(arguments),
        _parameter_bindings(owner, parameter_actuals),
    )


def bind_covergroups(host: Any) -> None:
    """Bind every inherited declaration to *host* without instantiating it."""
    declarations: dict[str, CoverGroupDeclaration] = {}
    for base in reversed(type(host).mro()):
        for name, value in base.__dict__.items():
            if isinstance(value, CoverGroupDeclaration):
                declarations[name] = value
    for declaration in declarations.values():
        host.__dict__[declaration._storage_key] = BoundCoverGroup(declaration, host)
    from .auto import AUTO_COVERGROUP_NAME, AutoCoverageDeclaration, auto_coverage_ir

    automatic_ir = auto_coverage_ir(type(host))
    if automatic_ir is not None:
        declaration = AutoCoverageDeclaration(type(host), automatic_ir)
        bound = BoundCoverGroup(declaration, host)
        host.__dict__[AUTO_COVERGROUP_NAME] = bound
        depth = getattr(host, "_svtypes_coverage_init_depth", 0)
        object.__setattr__(host, "_svtypes_coverage_init_depth", depth + 1)
        try:
            bound.instantiate()
        finally:
            object.__setattr__(host, "_svtypes_coverage_init_depth", depth)


def _layout_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if hasattr(value, "value"):
        return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value": _layout_value(value.value)}
    if isinstance(value, tuple):
        return [_layout_value(item) for item in value]
    raise CoverageError(f"coverage constructor binding {type(value).__name__} cannot form a stable instance layout")


def _validate_cover_input_actual(declaration: Any, name: str, type_name: str, value: Any) -> None:
    """Validate the builtin type forms that have stable Python counterparts."""
    if not type_name.startswith("CoverInput[") or not type_name.endswith("]"):
        return
    target = type_name[len("CoverInput["):-1].strip()
    expected = {"int": int, "str": str, "bool": bool, "float": float}.get(target)
    if expected is None:
        try:
            annotation = get_type_hints(declaration.function).get(name)
            resolved = get_args(annotation)
            candidate = resolved[0] if len(resolved) == 1 else None
            if isinstance(candidate, type):
                expected = candidate
        except (NameError, TypeError):
            pass
    if expected is None:
        return
    valid = isinstance(value, expected) and not (expected is int and isinstance(value, bool))
    if not valid:
        raise CoverageError(
            f"coverage constructor argument {name!r} expects {target}, got {type(value).__name__}"
        )


def _materialize_value(
    value: Any,
    bindings: dict[str, Any],
    parameter_bindings: Mapping[str, Any],
) -> Any:
    if isinstance(value, dict):
        if value.get("kind") == "parameter_ref":
            name = value["name"]
            if name not in parameter_bindings:
                raise CoverageError(
                    f"coverage parameter {name!r} is unbound for this instance layout"
                )
            return {"kind": "constant", "value": _layout_value(parameter_bindings[name])}
        if value.get("kind") == "name" and value.get("name") in bindings:
            return {"kind": "constant", "value": _layout_value(bindings[value["name"]])}
        return {
            key: _materialize_value(item, bindings, parameter_bindings)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_materialize_value(item, bindings, parameter_bindings) for item in value]
    if isinstance(value, tuple):
        return tuple(_materialize_value(item, bindings, parameter_bindings) for item in value)
    return value


def _parameter_bindings(
    owner: type[Any], overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve bound class Parameters only when materializing a concrete layout."""
    from ..parameter import Parameter

    result: dict[str, Any] = {}
    for base in reversed(owner.mro()):
        for name, value in base.__dict__.items():
            if isinstance(value, Parameter) and value.is_bound and not value.is_type_parameter:
                result[name] = value.value
    if overrides:
        unknown = sorted(set(overrides).difference(result))
        if unknown:
            raise CoverageError(f"unknown or unbound coverage Parameter {unknown[0]!r}")
        result.update(overrides)
    return result


def _materialize_ir(
    template: CoverageIR,
    bindings: dict[str, Any],
    parameter_bindings: Mapping[str, Any],
) -> CoverageIR:
    """Freeze constructor actuals into the per-instance evaluator template."""
    points = tuple(
        replace(
            point,
            expression=_materialize_value(point.expression, bindings, parameter_bindings),
            iff=_materialize_value(point.iff, bindings, parameter_bindings),
            bins=_materialize_point_bins(point, bindings, parameter_bindings),
        )
        for point in template.points
    )
    domains = {
        point.name: dict(point.options).get("comparison_domain")
        for point in points
    }
    crosses = tuple(
        _materialize_cross(cross, bindings, domains, parameter_bindings)
        for cross in template.crosses
    )
    return replace(template, points=points, crosses=crosses)


def _materialize_point_bins(
    point: Any, bindings: dict[str, Any], parameter_bindings: Mapping[str, Any]
) -> tuple[Any, ...]:
    from .evaluator import eval_expr
    from .frontend import _fixed_split_bins, _normalize_literal

    domain = dict(point.options).get("comparison_domain")
    comparison_domain = None
    if isinstance(domain, Mapping):
        comparison_domain = (
            domain["width"],
            domain["signed"],
            domain["four_state"],
        )
    result = []
    for bin_ in point.bins:
        selector = _materialize_value(bin_.selector, bindings, parameter_bindings)
        if comparison_domain is not None:
            selector = _normalize_literal(selector, comparison_domain, point.name)
        if not isinstance(selector, dict) or selector.get("kind") != "array_split":
            result.append(replace(bin_, selector=selector))
            continue
        range_selector = selector["selector"]
        lower = eval_expr(range_selector.get("lower"), bindings) if range_selector.get("lower") is not None else None
        upper = eval_expr(range_selector.get("upper"), bindings) if range_selector.get("upper") is not None else None
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (lower, upper)):
            raise CoverageError(f"coverage array bin {point.name!r}.{bin_.name} requires integer range endpoints")
        if lower > upper:
            raise CoverageError(f"coverage array bin {point.name!r}.{bin_.name} range lower endpoint exceeds upper endpoint")
        count = selector.get("count")
        if count is not None:
            result.extend(_fixed_split_bins(bin_.name, list(range(lower, upper + 1)), count))
            continue
        for value in range(lower, upper + 1):
            result.append(replace(
                bin_, name=f"{bin_.name}[{value}]",
                selector={"kind": "constant", "value": value, "array_split_base": bin_.name},
            ))
    return tuple(result)


def _materialize_cross(
    cross: CoverageCrossIR,
    bindings: dict[str, Any],
    domains: dict[str, Any],
    parameter_bindings: Mapping[str, Any],
) -> CoverageCrossIR:
    member_views = tuple(
        replace(
            view,
            point=replace(
                view.point,
                expression=_materialize_value(view.point.expression, bindings, parameter_bindings),
                iff=_materialize_value(view.point.iff, bindings, parameter_bindings),
                bins=_materialize_point_bins(view.point, bindings, parameter_bindings),
            ),
        )
        for view in cross.member_views
    )
    effective_domains = dict(domains)
    effective_domains.update({
        view.name: dict(view.point.options).get("comparison_domain")
        for view in member_views
    })
    functions = {function.name: function for function in cross.queue_functions}
    bins: list[CoverageBinIR] = []
    for bin_ in cross.bins:
        selector = _materialize_value(bin_.selector, bindings, parameter_bindings)
        if isinstance(selector, dict) and selector.get("kind") == "cross_queue_call":
            function = functions.get(selector["function"])
            if function is None:
                raise CoverageError(f"coverage cross {cross.name!r} references unknown queue function {selector['function']!r}")
            values = _execute_cross_queue_function(function, selector["args"], bindings, len(cross.members))
            normalized_values = []
            for value in values:
                normalized = []
                for member, item in zip(cross.members, value):
                    domain = effective_domains.get(member)
                    if domain is None:
                        raise CoverageError(f"coverage cross {cross.name!r} member {member!r} has no comparison domain")
                    from .frontend import _normalize_literal
                    normalized.append(_normalize_literal({"kind": "constant", "value": item}, (domain["width"], domain["signed"], domain["four_state"]), cross.name)["value"])
                normalized_values.append(tuple(normalized))
            selector = {"kind": "cross_queue_values", "items": [list(value) for value in normalized_values]}
        bins.append(replace(bin_, selector=selector))
    return replace(
        cross,
        iff=_materialize_value(cross.iff, bindings, parameter_bindings),
        bins=tuple(bins),
        member_views=member_views,
    )


def _execute_cross_queue_function(
    function: CrossQueueFunctionIR,
    arguments: list[Any],
    bindings: dict[str, Any],
    arity: int,
) -> tuple[tuple[Any, ...], ...]:
    """Interpret the finite frozen queue-function subset at instantiation."""
    from .evaluator import eval_expr

    if len(arguments) != len(function.parameters):
        raise CoverageError(f"cross queue function {function.name!r} has invalid argument count")
    values = dict(bindings)
    for parameter, argument in zip(function.parameters, arguments):
        values[parameter.name] = eval_expr(argument, values)
    queues: dict[str, list[tuple[Any, ...]]] = {}

    def run(statements: tuple[Any, ...] | list[Any]) -> str | None:
        for statement in statements:
            kind = statement["kind"]
            if kind == "queue_new":
                queues[statement["target"]] = []
            elif kind == "assign":
                values[statement["target"]] = eval_expr(statement["value"], values)
            elif kind == "push":
                queue = queues.get(statement["target"])
                if queue is None:
                    raise CoverageError(f"cross queue function {function.name!r} pushes to a non-queue")
                item = tuple(eval_expr(value, values) for value in statement["items"])
                if len(item) != arity:
                    raise CoverageError(f"cross queue function {function.name!r} emits a tuple with {len(item)} members; expected {arity}")
                if len(queue) >= MAX_CROSS_QUEUE_TUPLES:
                    raise CoverageError(
                        f"SVT-COV-CROSS-QUEUE-LIMIT: cross queue function {function.name!r} "
                        f"exceeds {MAX_CROSS_QUEUE_TUPLES} tuples"
                    )
                queue.append(item)
            elif kind == "for":
                bounds = [eval_expr(value, values) for value in statement["range"]]
                if not all(isinstance(value, int) and not isinstance(value, bool) for value in bounds):
                    raise CoverageError(f"cross queue function {function.name!r} range bounds must be integers")
                if len(bounds) == 3 and bounds[2] == 0:
                    raise CoverageError(f"cross queue function {function.name!r} range step cannot be zero")
                for index in range(*bounds):
                    values[statement["target"]] = index
                    returned = run(statement["body"])
                    if returned is not None:
                        return returned
            elif kind == "if":
                branch = statement["then"] if bool(eval_expr(statement["condition"], values)) else statement["else"]
                returned = run(branch)
                if returned is not None:
                    return returned
            elif kind == "return":
                return statement["value"]
            else:
                raise CoverageError(f"cross queue function {function.name!r} has invalid frozen statement")
        return None

    result_name = run(function.body)
    result = queues.get(result_name or "")
    if result is None:
        raise CoverageError(f"cross queue function {function.name!r} must return CrossQueueType")
    return tuple(result)
