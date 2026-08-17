from __future__ import annotations

import struct

from .base import BuiltInType


class Real(BuiltInType):
    _default_plusarg = True
    _default_cov = False
    def __init__(
        self,
        value: float | None=None,
        rand=None,
        plusarg=None,
        dump=None,
        cov=None,
        intelli=None,
        pack_bytes=True,
    ) -> None:
        super().__init__(rand, plusarg, dump, cov, intelli, pack_bytes)

        if value is None:
            self._value = 0.0
            self._init_value = None
        else:
            self._value = self._normalize(value)
            self._init_value = self._value


    def __call__(self, data: Real | int=0):
        self.value = data
        return self

    def _normalize(self, value):
        if isinstance(value, Real):
            value = value.value
        if isinstance(value, int):
            value = float(value)
        if not isinstance(value, float):
             raise TypeError(f"Expected float, got {type(value)}")
        return value

    @property
    def byte_num(self):
        return 8

    def pack(self, value):
        val = self._normalize(value)
        return struct.pack('<d', val)

    def unpack(self, bytes_: bytes):
        if len(bytes_) < self.byte_num:
            raise ValueError(
                f"Not enough bytes to unpack {self.__class__.__name__}: "
                f"need {self.byte_num}, got {len(bytes_)}"
            )
        if len(bytes_) > self.byte_num:
            useful_bytes = bytes_[0:self.byte_num]
        else:
            useful_bytes = bytes_
        val = struct.unpack('<d', useful_bytes)[0]
        return val, self.byte_num

    def sv_repr(self):
        return f'{self.value}'

    def sv_decl(self, name: str):
        return f"real {name}"

    def cpp_decl(self, name: str):
        return f"double {name}"

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        if self._init_value is None:
            return f'{ind_str}real {name};'
        else:
            return f'{ind_str}real {name} = {self._init_value};'

    def to_cpp_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"


class ShortReal(Real):
    @property
    def byte_num(self):
        return 4

    def pack(self, value):
        val = self._normalize(value)
        return struct.pack('<f', val)

    def unpack(self, bytes_: bytes):
        if len(bytes_) < self.byte_num:
            raise ValueError(
                f"Not enough bytes to unpack {self.__class__.__name__}: "
                f"need {self.byte_num}, got {len(bytes_)}"
            )
        if len(bytes_) > self.byte_num:
            useful_bytes = bytes_[0:self.byte_num]
        else:
            useful_bytes = bytes_
        val = struct.unpack('<f', useful_bytes)[0]
        return val, self.byte_num

    def cpp_decl(self, name: str):
        return f"float {name}"

    def sv_decl(self, name: str):
        return f"shortreal {name}"

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        if self._init_value is None:
            return f'{ind_str}shortreal {name};'
        else:
            return f'{ind_str}shortreal {name} = {self._init_value};'

    def to_cpp_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"

class RealTime(Real):
    pass
