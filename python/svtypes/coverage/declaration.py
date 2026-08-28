"""Embedded-covergroup declaration descriptors and instance lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar, overload

from ..errors import CoverageError
from .ir import CoverageIR


T = TypeVar("T")


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

    def __post_init__(self) -> None:
        from .evaluator import CoverageRuntime

        self.runtime = CoverageRuntime(self.declaration.ir)

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
        self.runtime.sample(values)

    def snapshot(self) -> dict[str, Any]:
        return self.runtime.snapshot()


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
