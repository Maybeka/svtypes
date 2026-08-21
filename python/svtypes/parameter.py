from __future__ import annotations
from typing import Any

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
        "ShortReal) or a value; type parameters use Parameter(type)"
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


class Parameter(BuiltInType):
    def __init__(self, value: Any = None) -> None:
        '''Parameter'''
        super().__init__(rand=False, plusarg=False, dump=False, cov=False, intelli=False, pack_bytes=False)
        self._value: Any = None
        self._dtype: str | None = None
        if value is not None:
            if isinstance(value, type):
                # Type declaration of an unbound parameter: Parameter(Int),
                # Parameter(String), Parameter(type), ...
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

    def _set_internal_value(self, value):
        if self._dtype == "type":
            # A declared type parameter can only be bound to a class/type.
            if not isinstance(value, type):
                raise TypeError(
                    f"a type parameter can only be bound to a class/type, not {type(value).__name__}"
                )
            self._value = value
            return
        if isinstance(value, type):
            # Binding a type parameter: Parameter(type)(SomeClass)
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
                self._value = Bit(n_bits, value, signed, Bit.Dec, False, False, False, False, False, False)
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

    def __call__(self, value):
        if getattr(self, "_declared_in_class", False):
            raise TypeError(
                "a parameter already declared in a class cannot be bound in place; "
                "use specialize() to bind it"
            )
        if self._value is not None:
            raise AttributeError("Parameter is immutable once set.")
        self._set_internal_value(value)
        return self

    def _require_declared(self, name: str) -> str:
        if self._value is None and self._dtype is None:
            raise DeclarationError(
                f"Parameter {name!r} must declare a type (e.g. Parameter(Int) or Parameter(type)) "
                "or carry a value before code generation"
            )
        assert self._dtype is not None
        return self._dtype

    def sv_type_value(self) -> str:
        """Generated-name of a bound type parameter value."""
        if self._value is None:
            raise DeclarationError("type parameter has no bound type")
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
                return f"typename {name} = {self.sv_type_value()}"
            return f"static constexpr {self._value.cpp_decl(name)}"
        dtype = self._require_declared(name)
        if dtype == "type":
            return f"typename {name}"
        cpp_type = _CPP_PARAM_TYPES.get(dtype)
        if cpp_type is None:
            raise DeclarationError(
                f"Parameter {name!r} of type {dtype!r} cannot be an unbound C++ template parameter; "
                "bind a value or declare Parameter(Int)/Parameter(type)"
            )
        return f"static constexpr {cpp_type} {name}"

    def sv_repr(self):
        if self._dtype == "type":
            return self.sv_type_value()
        return self._value.sv_repr()

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        if self._value is not None and self._dtype != "type":
            return f"{ind_str}{self.sv_decl(name)} = {self.sv_repr()};"
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"
