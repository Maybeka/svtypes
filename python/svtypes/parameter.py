from __future__ import annotations
from typing import Any, TYPE_CHECKING

from .bits import Bits
from .int import Int
from .real import Real
from .string import String

from .base import BuiltInType, TypeBase

class Parameter(BuiltInType):
    def __init__(self, value: Any = None) -> None:
        '''Parameter'''
        super().__init__(rand=False, plusarg=False, dump=False, cov=False, intelli=False, pack_bytes=False)
        self._value: Any = None
        if value is not None:
            self._set_internal_value(value)
        self._immutable = True

    def _set_internal_value(self, value):
        if isinstance(value, int):
            if -2**31 <= value < 2**31:
                self._value = Int(value)
            else:
                n_bits = self._get_signed_bit_width(value)
                signed = value < 0
                self._value = Bits(n_bits, value, signed, Bits.Dec, False, False, False, False, False, False)
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
        return self._value.value if self._value else None

    @value.setter
    def value(self, val):
        if self._value is not None:
             raise AttributeError("Parameter is immutable unless overrided.")
        self._set_internal_value(val)

    def __call__(self, value):
        if self._value is not None:
             raise AttributeError("Parameter is immutable once set.")
        self._set_internal_value(value)
        return self

    def sv_repr(self):
        return self._value.sv_repr()

    def sv_decl(self, name: str | None = None):
        name = name or self._attr_name
        if self._value is None:
            return f"parameter {name}"
        return f"parameter {self._value.sv_decl(name)}"

    def cpp_decl(self, name: str | None = None):
        name = name or self._attr_name
        if self._value is None:
            return f"static constexpr auto {name}"
        decl = self._value.cpp_decl(name)
        return f"static constexpr {decl}"

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        if self._value is None:
             return f"parameter {name};"
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)} = {self.sv_repr()};"
