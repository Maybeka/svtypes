from __future__ import annotations
from typing import Any
from types import FunctionType

from .base import BuiltInType, TypeBase
from .bit import Bit
from .errors import DeclarationError
from .int import Int, LongInt
from .real import Real, ShortReal
from .string import String

_SV_PARAM_TYPES = {
    "int": "int",
    "longint": "longint",
    "str": "string",
    "float": "real",
    "shortreal": "shortreal",
    "type": "type",
}

_CPP_PARAM_TYPES = {
    "int": "int32_t",
    "longint": "int64_t",
    "type": "typename",
    # "str" and "float" are not legal C++ non-type template parameters.
}

# Declared dtype -> declaration class, so a specialization can rebuild a
# Parameter with the same dtype instead of re-inferring from the bound value.
DTYPE_CLASSES: dict[str, type] = {
    "int": Int,
    "longint": LongInt,
    "str": String,
    "float": Real,
    "shortreal": ShortReal,
    "type": type,
}


class ParamRef:
    """Named reference to a parameter declared in the subclass body.

    Used as a specialize() value to forward a base-template parameter to the
    subclass' own parameter: ``Base.specialize(A=ParamRef())`` refers to the
    subclass parameter named ``A``; ``ParamRef("WIDTH")`` refers to ``WIDTH``.
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"ParamRef({self.name!r})"


class TypeParameterField(TypeBase):
    """A field whose concrete descriptor is supplied by ``Parameter[type]``.

    It exists only on an unbound template.  Class construction replaces it
    with the bound descriptor as soon as the enclosing type has a concrete
    type argument, while code generators can still render the symbolic
    ``T field`` declaration for the template itself.
    """

    _default_rand = False
    _default_plusarg = False
    _default_dump = True
    _default_cov = False

    def __init__(self, parameter: "Parameter", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.parameter = parameter
        self.parameter_name: str | None = None

    def bind_parameter_name(self, name: str) -> None:
        self.parameter_name = name

    def _name(self) -> str:
        if self.parameter_name is None:
            raise DeclarationError("type-parameter field is not attached to a Parameter")
        return self.parameter_name

    def sv_decl(self, name: str) -> str:
        return f"{self._name()} {name}"

    def cpp_decl(self, name: str) -> str:
        return f"{self._name()} {name}"

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.sv_decl(name or self._attr_name)};"

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"

    def pack(self, value: Any) -> bytes:
        raise DeclarationError("unbound type-parameter fields cannot be encoded in Python")

    def unpack(self, bytes_: bytes) -> tuple[Any, int]:
        raise DeclarationError("unbound type-parameter fields cannot be decoded in Python")


def _normalize_dtype(dtype: type) -> str:
    if dtype is Int:
        return "int"
    if dtype is LongInt:
        return "longint"
    if dtype is String:
        return "str"
    if dtype is Real:
        return "float"
    if dtype is ShortReal:
        return "shortreal"
    if dtype is type:
        return "type"
    raise TypeError(
        f"unsupported Parameter type {getattr(dtype, '__name__', dtype)!r}; "
        "value parameters declare an svtypes scalar type (Int, LongInt, String, Real, "
        "ShortReal) or a value; type parameters use Parameter[type](...)"
    )


def _infer_dtype(value: Any) -> str | None:
    if isinstance(value, LongInt):
        return "longint"
    if isinstance(value, Int):
        return "int"
    if isinstance(value, String):
        return "str"
    if isinstance(value, ShortReal):
        return "shortreal"
    if isinstance(value, Real):
        return "float"
    if isinstance(value, Bit):
        return "int"
    return None


def cpp_param_literal(value: Any) -> str:
    """Render a bound parameter value as a C++ template argument."""
    return str(value)


def _construct_type_parameter_field(template: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Instantiate a concrete type-parameter argument as a field descriptor."""
    from .typespec import TypeSpec

    if isinstance(template, TypeSpec):
        return template(*args, **kwargs)
    if isinstance(template, type):
        return template(*args, **kwargs)
    raise DeclarationError("type parameter is not bound to a constructible SvTypes type")


class Parameter(BuiltInType):
    _MISSING = object()

    @classmethod
    def __class_getitem__(cls, dtype: type):
        """Return ``Parameter[dtype]``'s declaration specification."""
        from .typespec import TypeSpec

        _normalize_dtype(dtype)
        missing = object()

        def construct(*values: Any, **kwargs: Any) -> "Parameter":
            if kwargs:
                raise TypeError("Parameter[...] does not accept keyword arguments")
            if len(values) > 1:
                raise TypeError("Parameter[...] accepts at most one default value")
            value = values[0] if values else missing
            parameter = cls._from_dtype(dtype)
            if value is not missing:
                if isinstance(value, FunctionType):
                    parameter._set_expression(value)
                else:
                    parameter._set_internal_value(value)
            return parameter

        return TypeSpec(cls, (dtype,), construct)

    @classmethod
    def _from_dtype(cls, dtype: type) -> "Parameter":
        """Internal constructor used after ``Parameter[...]`` has bound dtype."""

        return cls(dtype, _svtypes_internal=True)

    @classmethod
    def _from_value(cls, value: Any) -> "Parameter":
        """Internal specialization helper for a value with no declared dtype."""

        from .typespec import TypeSpec

        if isinstance(value, (type, TypeSpec)):
            return cls[type](value)
        if isinstance(value, bool) or isinstance(value, int):
            return cls[Int](value)
        if isinstance(value, float):
            return cls[Real](value)
        if isinstance(value, str):
            return cls[String](value)
        if isinstance(value, LongInt):
            return cls[LongInt](value.value)
        if isinstance(value, ShortReal):
            return cls[ShortReal](value.value)
        if isinstance(value, TypeBase):
            dtype_name = _infer_dtype(value)
            dtype_classes = {
                "int": Int,
                "longint": LongInt,
                "str": String,
                "float": Real,
                "shortreal": ShortReal,
            }
            dtype = dtype_classes.get(dtype_name or "")
            if dtype is not None:
                return cls._from_dtype(dtype)._set_and_return(value)
            # Preserve the former internal fallback for uncommon TypeBase
            # values.  Public source declarations are always typed through
            # Parameter[...], so this only supports legacy specialization
            # metadata while it is being materialized.
            return cls(None, _svtypes_internal=True)._set_and_return(value)
        raise TypeError(f"Assign a value of type {type(value).__name__} is not allowed.")

    def _set_and_return(self, value: Any) -> "Parameter":
        self._set_internal_value(value)
        return self

    def __init__(self, value: Any = None, *, _svtypes_internal: bool = False) -> None:
        if not _svtypes_internal:
            raise TypeError("Parameter(...) no longer declares a type; use Parameter[Type](default) instead")
        '''Parameter'''
        super().__init__(rand=False, plusarg=False, dump=False, cov=False, intelli=False, pack_bytes=False)
        self._value: Any = None
        self._expression = None
        self._dtype: str | None = None
        if value is not None:
            if isinstance(value, type):
                # Internal construction of an unbound typed parameter.
                self._dtype = _normalize_dtype(value)
            else:
                self._set_internal_value(value)
        self._immutable = True

    @property
    def dtype(self) -> str | None:
        """Declared/derived parameter type: int/str/float/... or 'type'."""
        if self._dtype is not None:
            return self._dtype
        if self._value is not None:
            return _infer_dtype(self._value)
        return None

    @property
    def is_type_parameter(self) -> bool:
        return self.dtype == "type"

    @property
    def is_bound(self) -> bool:
        return self._value is not None

    @property
    def expression(self):
        """Validated symbolic default, if this is a dependent numeric parameter."""
        return self._expression

    def _set_expression(self, fn: Any) -> None:
        from .parameter_expr import ParameterExpr

        if self._dtype not in {"int", "longint"}:
            raise TypeError("parameter expressions are supported only for Parameter[Int] and Parameter[LongInt]")
        if self._value is not None or self._expression is not None:
            raise AttributeError("Parameter is immutable once set.")
        self._expression = ParameterExpr.parse(fn)

    def _set_internal_value(self, value):
        if self._dtype == "type":
            # A declared type parameter can only be bound to a class/type.
            from .typespec import TypeSpec

            if not isinstance(value, (type, TypeSpec)):
                raise TypeError(
                    f"a type parameter can only be bound to a class/type specification, not {type(value).__name__}"
                )
            self._value = value
            return
        if isinstance(value, type):
            # Internal construction of a bound type parameter.
            if self._dtype not in (None, "type"):
                raise TypeError("a value parameter cannot be bound to a type")
            self._dtype = "type"
            self._value = value
            return
        if isinstance(value, int):
            if -2**31 <= value < 2**31:
                self._value = Int(value)
            else:
                n_bits = self._get_signed_bit_width(value)
                signed = value < 0
                from .typespec import Signed, Unsigned

                self._value = Bit[n_bits, Signed if signed else Unsigned](
                    value,
                    radix=Bit.Dec,
                    rand=False,
                    plusarg=False,
                    dump=False,
                    cov=False,
                    intelli=False,
                    pack_bytes=False,
                )
        elif isinstance(value, float):
            self._value = Real(value, False, False, False, False, False, False)
        elif isinstance(value, str):
            self._value = String(value, False, False, False, False, False, False)
        elif isinstance(value, TypeBase):
            self._value = value
        else:
            raise TypeError(f'Assign a value of type {type(value).__name__} is not allowed.')

    @property
    def value(self):
        if self._dtype == "type":
            return self._value
        return self._value.value if self._value else None

    @value.setter
    def value(self, val):
        if self._value is not None:
            raise AttributeError("Parameter is immutable unless overrided.")
        self._set_internal_value(val)

    def __call__(self, *args: Any, **kwargs: Any):
        # ``T()`` in a class body means a field with the type selected by a
        # Parameter[type].  It is intentionally distinct from value-parameter
        # binding and accepts the ordinary descriptor declaration keywords.
        if self._dtype == "type":
            if self._value is None:
                if args:
                    raise TypeError(
                        "type parameter binding belongs in Parameter[type](T) or specialize(); "
                        "only T() field construction is supported on an unbound Parameter[type]"
                    )
                return TypeParameterField(self, **kwargs)
            return _construct_type_parameter_field(self._value, args, kwargs)
        raise TypeError(
            "Parameter instances are not bindable; use Parameter[Type](value) "
            "or specialize()"
        )

    def _require_declared(self, name: str) -> str:
        if self._value is None and self._dtype is None:
            raise DeclarationError(
                f"Parameter {name!r} must declare a type (e.g. Parameter[Int]() or Parameter[type]()) "
                "or carry a value before code generation"
            )
        assert self._dtype is not None
        return self._dtype

    def sv_type_value(self) -> str:
        """Generated-name of a bound type parameter value."""
        if self._value is None:
            raise DeclarationError("type parameter has no bound type")
        from .typespec import TypeSpec

        if isinstance(self._value, TypeSpec):
            descriptor = self._value()
            marker = "__svtypes_type_parameter"
            declaration = descriptor.sv_decl(marker)
            if not declaration.endswith(marker):
                raise DeclarationError("type parameter does not produce a SystemVerilog field type")
            return declaration[: -len(marker)].rstrip()
        return getattr(self._value, "__name__", str(self._value))

    def cpp_type_value(self) -> str:
        """C++ type expression for a bound ``Parameter[type]`` default."""
        if self._value is None:
            raise DeclarationError("type parameter has no bound type")
        from .typespec import TypeSpec

        if isinstance(self._value, TypeSpec):
            descriptor = self._value()
            marker = "__svtypes_type_parameter"
            declaration = descriptor.cpp_decl(marker)
            if marker not in declaration:
                raise DeclarationError("type parameter does not produce a C++ field type")
            return declaration.split(marker, 1)[0].rstrip()
        return getattr(self._value, "__name__", str(self._value))

    def sv_decl(self, name: str | None = None):
        name = name or self._attr_name
        if self._value is not None:
            if self._dtype == "type":
                return f"parameter type {name} = {self.sv_type_value()}"
            return f"parameter {self._value.sv_decl(name)}"
        dtype = self._require_declared(name)
        return f"parameter {_SV_PARAM_TYPES[dtype]} {name}"

    def cpp_decl(self, name: str | None = None):
        name = name or self._attr_name
        if self._value is not None:
            if self._dtype == "type":
                return f"typename {name} = {self.cpp_type_value()}"
            return f"static constexpr {self._value.cpp_decl(name)}"
        dtype = self._require_declared(name)
        if dtype == "type":
            return f"typename {name}"
        cpp_type = _CPP_PARAM_TYPES.get(dtype)
        if cpp_type is None:
            raise DeclarationError(
                f"Parameter {name!r} of type {dtype!r} cannot be an unbound C++ template parameter; "
                "bind a value or declare Parameter[Int]()/Parameter[type]()"
            )
        return f"static constexpr {cpp_type} {name}"

    def sv_repr(self):
        if self._dtype == "type":
            return self.sv_type_value()
        if self._expression is not None:
            return self._expression.render()
        return self._value.sv_repr()

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        if (self._value is not None or self._expression is not None) and self._dtype != "type":
            return f"{ind_str}{self.sv_decl(name)} = {self.sv_repr()};"
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"
