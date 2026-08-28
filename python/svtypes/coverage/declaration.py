"""Embedded-covergroup declaration descriptors and instance lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Generic, TypeVar, overload

from ..errors import CoverageError
from .canonical import semantic_digest
from .ir import CoverageIR


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


class CoverInput(Generic[T]):
    """Type-only marker for an embedded covergroup constructor input."""


class CoverRef(Generic[T]):
    """Type-only marker for an embedded covergroup reference input."""


@dataclass(slots=True)
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
    option: CoverageInstanceOption | None = None
    logical_instance_key: str | None = None

    def __post_init__(self) -> None:
        from .evaluator import CoverageRuntime

        self.runtime = CoverageRuntime(self.declaration.ir)
        self.option = CoverageInstanceOption(self.declaration.ir.options)

    def sample(self, *args: Any, **kwargs: Any) -> None:
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
        self.runtime.sample(values)

    @property
    def instance_layout_digest(self) -> str:
        """Digest constructor bindings that can alter an instance's bin layout."""
        values = {name: _layout_value(value) for name, value in self.constructor_named_actuals}
        for formal, value in zip(self.declaration.ir.constructor_parameters, self.constructor_actuals):
            values.setdefault(formal.name, _layout_value(value))
        return semantic_digest({"constructor_actuals": values})

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
        return self.runtime.coverage()

    def get_inst_coverage(self) -> float:
        return self.get_coverage()

    def start(self) -> None:
        self.runtime.start()

    def stop(self) -> None:
        self.runtime.stop()

    def set_inst_name(self, name: str) -> None:
        assert self.option is not None
        self.option.name = name

    def snapshot_document(self) -> dict[str, Any]:
        return {
            "covergroup_type_id": self.declaration.ir.covergroup_type_id,
            "declaration_semantic_digest": self.declaration.ir.declaration_semantic_digest,
            "instance_layout_digest": self.instance_layout_digest,
            "instance_name": self.option.name if self.option is not None else None,
            "comment": self.option.comment if self.option is not None else "",
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

    def start(self) -> None:
        self.instance.start()

    def stop(self) -> None:
        self.instance.stop()

    def set_inst_name(self, name: str) -> None:
        self.instance.set_inst_name(name)

    def bind_logical_instance(self, key: str) -> None:
        self.instance.bind_logical_instance(key)

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
        bound.instantiate()


def _layout_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if hasattr(value, "value"):
        return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value": _layout_value(value.value)}
    if isinstance(value, tuple):
        return [_layout_value(item) for item in value]
    raise CoverageError(f"coverage constructor binding {type(value).__name__} cannot form a stable instance layout")
