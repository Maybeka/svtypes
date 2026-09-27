"""Runtime type specifications used by the bracket-style public API.

``Bit[8]`` is intentionally a small immutable value, not a dynamic Python
subclass.  Calling it creates the ordinary descriptor/value used everywhere
else in SvTypes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable


class Signed:
    """Marker selecting a signed packed type specialization."""


class Unsigned:
    """Marker selecting an unsigned packed type specialization."""


@dataclass(frozen=True, slots=True)
class TypeSpec:
    """One immutable, callable specialization of a SvTypes factory."""

    factory: Any
    args: tuple[Any, ...]
    constructor: Callable[..., Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.constructor(*args, **kwargs)

    @property
    def base(self) -> Any:
        return self.factory

    @property
    def parameters(self) -> tuple[Any, ...]:
        return self.args

    def __repr__(self) -> str:
        name = getattr(self.factory, "__name__", type(self.factory).__name__)
        return f"{name}[{', '.join(_format_arg(arg) for arg in self.args)}]"


def _format_arg(arg: Any) -> str:
    if isinstance(arg, tuple):
        return "(" + ", ".join(_format_arg(item) for item in arg) + ")"
    return getattr(arg, "__name__", repr(arg))


def _subscript_args(item: Any) -> tuple[Any, ...]:
    return item if isinstance(item, tuple) else (item,)


def packed_spec(factory: type, item: Any, *, declaration_style: str | None = None) -> TypeSpec:
    """Create a packed scalar specialization with an optional signed marker."""

    # Python passes ``Bit[()]`` as the empty tuple.  It is a supplied (but
    # invalid) packed shape, not a missing subscript, and must preserve the
    # scalar constructor's ``ValueError`` contract.
    args = ((),) if item == () else _subscript_args(item)
    signed = False
    if args and args[-1] in (Signed, Unsigned):
        signed = args[-1] is Signed
        args = args[:-1]
    if not args:
        raise TypeError(f"{factory.__name__}[...] requires a width or shape")
    if len(args) == 1 and isinstance(args[0], tuple):
        shape: Any = args[0]
    elif len(args) == 1:
        shape = args[0]
    else:
        shape = tuple(args)

    key = (factory, shape, signed, declaration_style)
    return _packed_spec_cached(key)


@lru_cache(maxsize=None)
def _packed_spec_cached(key: tuple[Any, Any, bool, str | None]) -> TypeSpec:
    factory, shape, signed, declaration_style = key
    public_args = (shape, Signed if signed else Unsigned)

    def construct(*values: Any, **kwargs: Any) -> Any:
        if "signed" in kwargs:
            raise TypeError("signed is fixed by the bracket specialization")
        if len(values) > 1:
            raise TypeError("a packed type specialization accepts at most one positional value")
        missing = object()
        keyword_value = kwargs.pop("value", missing)
        if values and keyword_value is not missing:
            raise TypeError("a packed type specialization accepts its value either positionally or as value=, not both")
        value = values[0] if values else (None if keyword_value is missing else keyword_value)
        if declaration_style is not None:
            if "declaration_style" in kwargs or "_sv_declaration_style" in kwargs:
                raise TypeError("declaration style is fixed by the bracket specialization")
            kwargs["declaration_style"] = declaration_style
        from .symbolic import has_symbolic_layout, SymbolicPackedField

        if has_symbolic_layout(shape):
            return SymbolicPackedField(
                factory,
                shape,
                signed=signed,
                declaration_style=declaration_style,
            values=(value,) if value is not None else (),
                kwargs=kwargs,
            )
        return factory._from_layout(
            shape,
            value,
            signed=signed,
            **kwargs,
        )

    return TypeSpec(factory, public_args, construct)


class SignedFamily:
    """Bracket-only signed spelling such as ``BitSigned[8]``."""

    def __init__(self, factory: type, *, declaration_style: str | None = None) -> None:
        self._factory = factory
        self._declaration_style = declaration_style
        self.__name__ = f"{factory.__name__}Signed"

    def __getitem__(self, item: Any) -> TypeSpec:
        args = _subscript_args(item)
        return packed_spec(self._factory, (*args, Signed), declaration_style=self._declaration_style)


def materialize_type(template: Any, location: str) -> Any:
    """Turn a TypeSpec into the descriptor expected by existing codecs."""

    if isinstance(template, TypeSpec):
        return template()
    # Enum classes and object/struct classes retain their existing constructor
    # behavior.  Plain descriptor instances remain accepted internally during
    # the migration, but public bracket APIs never create them as type args.
    if isinstance(template, type):
        return template()
    return template


def collection_spec(factory: type, item: Any, arity: int, *, key_value: bool = False) -> TypeSpec:
    args = _subscript_args(item)
    if len(args) != arity:
        raise TypeError(f"{factory.__name__}[...] requires {arity} type argument(s)")
    try:
        return _collection_spec_cached(factory, args, key_value)
    except TypeError:
        # A symbolic parameter expression may intentionally be unhashable;
        # it remains valid but does not participate in identity caching.
        return _make_collection_spec(factory, args, key_value)


@lru_cache(maxsize=None)
def _collection_spec_cached(factory: type, args: tuple[Any, ...], key_value: bool) -> TypeSpec:
    return _make_collection_spec(factory, args, key_value)


def _make_collection_spec(factory: type, args: tuple[Any, ...], key_value: bool) -> TypeSpec:
    def construct(*values: Any, **kwargs: Any) -> Any:
        if values:
            raise TypeError(f"{factory.__name__}[...] accepts only keyword value/policy arguments")
        from .symbolic import has_symbolic_layout, SymbolicArrayField, SymbolicCollectionField

        if factory.__name__ == "Array" and has_symbolic_layout(args):
            return SymbolicArrayField(args[0], args[1], kwargs=kwargs)
        if factory.__name__ != "Array" and has_symbolic_layout(args):
            return SymbolicCollectionField(factory, args, kwargs=kwargs)
        if len(args) == 1:
            return factory._from_layout(
                materialize_type(args[0], f"{factory.__name__} element"),
                **kwargs,
            )
        if len(args) == 2 and not key_value:
            return factory._from_layout(
                materialize_type(args[0], f"{factory.__name__} element"),
                args[1],
                **kwargs,
            )
        return factory._from_layout(
            materialize_type(args[0], f"{factory.__name__} key"),
            materialize_type(args[1], f"{factory.__name__} value"),
            **kwargs,
        )

    return TypeSpec(factory, args, construct)
