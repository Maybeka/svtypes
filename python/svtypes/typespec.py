"""Runtime type specifications used by the bracket-style public API.

``Bit[8]`` is intentionally a small immutable value, not a dynamic Python
subclass.  Calling it creates the ordinary descriptor/value used everywhere
else in SvTypes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from .errors import UnsupportedTypeError


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
    # Declaration spelling is semantic for ``Logic`` versus ``Reg`` even
    # though both materialize the same Python runtime class.
    declaration_style: str | None = None

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

    return TypeSpec(factory, public_args, construct, declaration_style)


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


def is_type_spec(annotation: Any) -> bool:
    """Return whether *annotation* is a bracket-style :class:`TypeSpec`.

    This deliberately does not treat ordinary concrete SvTypes classes as a
    ``TypeSpec``.  Consumers that accept the complete cross-language boundary
    surface should use :func:`is_materializable_type` instead.
    """

    return isinstance(annotation, TypeSpec)


def _boundary_error(annotation: Any, location: str, reason: str) -> UnsupportedTypeError:
    return UnsupportedTypeError(
        f"{location}: {reason}; got {getattr(annotation, '__name__', repr(annotation))}"
    )


def _require_concrete_object_type(annotation: type, location: str) -> None:
    """Reject an SvObject template whose parameters have not been bound."""

    from .parameter import Parameter

    claimed: set[str] = set()
    for owner in annotation.__mro__:
        for name, value in owner.__dict__.items():
            if name in claimed or not isinstance(value, Parameter):
                continue
            claimed.add(name)
            if value.value is None and value.expression is None:
                raise _boundary_error(annotation, location, "unbound parameterized type is not a boundary type")


def _identity_argument(argument: Any, location: str) -> Any:
    """Return a JSON-compatible, process-independent type argument identity."""

    if argument is Signed:
        return {"kind": "signedness", "value": "signed"}
    if argument is Unsigned:
        return {"kind": "signedness", "value": "unsigned"}
    if argument is None or isinstance(argument, (bool, int, float, str)):
        return argument
    if isinstance(argument, tuple):
        return [_identity_argument(item, location) for item in argument]
    if isinstance(argument, TypeSpec) or isinstance(argument, type):
        return type_spec_identity(argument, location=location)
    raise _boundary_error(argument, location, "non-canonical type argument is not supported at a cross-language boundary")


def _class_identity(
    annotation: type,
    location: str,
    *,
    allow_packed_factory: bool = False,
) -> dict[str, Any]:
    """Identify one permitted concrete non-bracket type."""

    from .base import TypeBase
    from .enum import Enum
    from .object import SvObject, SvStruct

    # These scalar classes have a language-level identity independent of the
    # Python module that happened to import them.
    scalar_names = {
        "svtypes.int.Int": "Int",
        "svtypes.int.LongInt": "LongInt",
        "svtypes.real.Real": "Real",
        "svtypes.real.ShortReal": "ShortReal",
        "svtypes.real.RealTime": "RealTime",
        "svtypes.string.String": "String",
    }
    qualified = f"{annotation.__module__}.{annotation.__qualname__}"
    scalar = scalar_names.get(qualified)
    if scalar is not None:
        return {"kind": "scalar", "name": scalar}
    packed_names = {
        "svtypes.bit.Bit": "Bit",
        "svtypes.logic.Logic": "Logic",
        "svtypes.logic.Reg": "Reg",
    }
    if qualified in packed_names and allow_packed_factory:
        return {"kind": "packed", "name": packed_names[qualified]}
    if qualified in packed_names:
        raise _boundary_error(annotation, location, "packed type requires a concrete bracket specification")
    if issubclass(annotation, (SvObject, SvStruct)):
        _require_concrete_object_type(annotation, location)
        return {
            "kind": "struct" if issubclass(annotation, SvStruct) else "object",
            "module": annotation.__module__,
            "qualname": annotation.__qualname__,
        }
    if issubclass(annotation, Enum):
        return {
            "kind": "enum",
            "module": annotation.__module__,
            "qualname": annotation.__qualname__,
            "width": annotation.width,
            "signed": annotation.signed,
        }
    if issubclass(annotation, TypeBase):
        raise _boundary_error(annotation, location, "SvTypes descriptor class is not an approved boundary type")
    raise _boundary_error(annotation, location, "type is not an approved SvTypes boundary type")


def type_spec_identity(annotation: Any, *, location: str = "type") -> dict[str, Any]:
    """Return the canonical JSON-compatible identity of a boundary type.

    The result intentionally contains no callable, object address, repr, or
    process-local cache key, so it is suitable for an SVX manifest.  Symbolic
    layouts and unbound parameterized objects are rejected rather than being
    assigned a misleading stable identity.
    """

    if isinstance(annotation, type):
        return _class_identity(annotation, location)
    if not isinstance(annotation, TypeSpec):
        raise _boundary_error(annotation, location, "annotation is not a SvTypes boundary type")

    from .collection import Array, AssocArray, DynArray, Queue
    from .object import Object
    from .parameter import Parameter
    from .remote_ref import RemoteRef
    from .symbolic import has_symbolic_layout

    if has_symbolic_layout(annotation.parameters):
        raise _boundary_error(annotation, location, "symbolic type layout is not concrete")
    if annotation.base is Object:
        return {"kind": "object_handle", "target": _identity_argument(annotation.parameters[0], location)}
    if annotation.base is RemoteRef:
        return {"kind": "remote_ref", "target": _identity_argument(annotation.parameters[0], location)}
    if annotation.base in (Array, DynArray, Queue, AssocArray):
        return {
            "kind": "collection",
            "name": annotation.base.__name__,
            "args": [_identity_argument(arg, location) for arg in annotation.parameters],
        }
    if annotation.base is Parameter:
        raise _boundary_error(annotation, location, "Parameter declarations are not value boundary types")
    if isinstance(annotation.base, type):
        identity = {
            "kind": "specialization",
            "base": _class_identity(annotation.base, location, allow_packed_factory=True),
            "args": [_identity_argument(arg, location) for arg in annotation.parameters],
        }
        if annotation.declaration_style is not None:
            identity["declaration_style"] = annotation.declaration_style
        return identity
    raise _boundary_error(annotation, location, "type specification factory is not supported")


def is_materializable_type(annotation: Any) -> bool:
    """Return whether *annotation* is a supported concrete boundary type."""

    try:
        type_spec_identity(annotation)
    except (TypeError, ValueError):
        return False
    return True


def materialize_type_spec(annotation: Any, *, location: str) -> Any:
    """Materialize one validated cross-language type into its codec descriptor.

    Unlike the legacy internal :func:`materialize_type`, this function first
    validates the supported public boundary surface and gives callers a stable
    location-bearing error for unsupported annotations.
    """

    type_spec_identity(annotation, location=location)
    try:
        result = materialize_type(annotation, location)
    except (TypeError, ValueError) as error:
        raise UnsupportedTypeError(f"{location}: cannot materialize {annotation!r}: {error}") from error
    return result


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
