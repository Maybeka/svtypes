from __future__ import annotations

from functools import cached_property
from math import prod
from .base import BuiltInType
from .errors import DeclarationError
from .limits import DEFAULT_MAX_PACKED_BITS


class Bit(BuiltInType):
    _default_rand = True
    _default_plusarg = True
    Hex = 'h'
    Dec = 'd'
    Oct = 'o'
    Bin = 'b'

    def __init__(
        self,
        width: int | tuple[int, ...] = 1,
        value: int | None=None,
        signed=False,
        radix=Hex,
        rand: bool | None = None,
        plusarg: bool | None = None,
        dump: bool | None = None,
        cov: bool | None = None,
        intelli: bool | None = None,
        pack_bytes: bool | None = True,
    ) -> None:
        super().__init__(rand, plusarg, dump, cov, intelli, pack_bytes)

        if isinstance(width, tuple):
            if not width:
                raise ValueError("Bit shape cannot be empty")
            if any(not isinstance(size, int) or isinstance(size, bool) for size in width):
                raise TypeError("Every Bit shape dimension must be an integer")
            if any(size <= 0 for size in width):
                raise ValueError("Every Bit shape dimension must be positive")
            self._shape = None if len(width) == 1 else tuple(width)
            self._width = prod(width)
        else:
            if not isinstance(width, int) or isinstance(width, bool):
                raise TypeError("Bit width must be an integer or a tuple of integers")
            if width <= 0:
                raise ValueError(f'The width of a bit vector must be positive, while {width} provided.')
            self._shape = None
            self._width = width

        if self._width > DEFAULT_MAX_PACKED_BITS:
            raise DeclarationError(
                f"Bit width {self._width} exceeds declaration limit {DEFAULT_MAX_PACKED_BITS}"
            )

        self._signed = bool(signed)

        if radix not in [self.Hex, self.Dec, self.Oct, self.Bin]:
            raise ValueError(f'Illegal radix initial value "{radix}", while it must be in ["h", "d", "o", "b"]')
        self._radix = radix

        if value is None:
            self._value = 0  # it's always ok to assign 0 directly
            self._init_value = None
        else:
            self._value = self._normalize(value)
            self._init_value = self._value

    @property
    def width(self):
        return self._width

    @property
    def shape(self) -> tuple[int, ...] | None:
        return self._shape

    @cached_property
    def _internal_max(self):
        if self.signed:
            return 2 ** (self.width - 1) - 1
        else:
            return 2 ** self.width - 1

    @cached_property
    def _internal_min(self):
        if self.signed:
            return - 2 ** (self.width - 1)
        else:
            return 0

    @property
    def signed(self):
        return self._signed

    @property
    def state_domain(self):
        """Public signal-domain metadata for consumers of the SvTypes codec."""
        return "2state"

    @property
    def radix(self):
        return self._radix

    @cached_property
    def _width_mask(self):
        return (1 << self.width) - 1

    @cached_property
    def _msb_mask(self):
        return 1 << (self.width - 1)

    @cached_property
    def _width_range(self):
        '''full value range of the width'''
        return 1 << self.width

    def _normalize(self, value: int):
        if isinstance(value, Bit):
            value = value.value
        if not isinstance(value, int):
             raise TypeError(f"Expected int or Bit, got {type(value)}")

        # 1. 模拟硬件截断 (无论输入多大，只看目标位宽)
        truncated = value & self._width_mask

        if not self.signed:
            return truncated
        else:
            # 2. 模拟有符号解释
            if truncated & self._msb_mask:
                return truncated - self._width_range
            else:
                return truncated

    def __call__(self, data: Bit | int):
        self.value = data
        return self

    @cached_property
    def byte_num(self):
        return (self.width + 7) // 8

    def pack(self, value):
        val = self._normalize(value)
        truncated = val & self._width_mask

        return truncated.to_bytes(self.byte_num, byteorder='little', signed=False)

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

        raw_val = int.from_bytes(useful_bytes, byteorder='little', signed=False)
        val = self._normalize(raw_val)

        return val, self.byte_num


    # def __getitem__(self, index):
    #     if not isinstance(index, slice):
    #         raise TypeError(f'bit range must be of tyep "slice", but got "{type(index)}"')
    #     return Bit(index.stop - index.start + 1)


    @staticmethod
    def _gen_sv_repr(value: int | Bit, width=0, radix=Hex) -> str:
        if isinstance(value, Bit):
            return Bit._gen_sv_repr(value.value, value.width, value.radix)
        else:
            type_str = f'{width}\'{radix}'
            if radix == 'h':
                return hex(value).replace("0x", type_str)  # replace处理同时支持负数和正数
            elif radix == 'd':
                return f'{type_str}{value}' if value >= 0 else f'-{type_str}{-value}'
            elif radix == 'o':
                return oct(value).replace("0o", type_str)  # replace处理同时支持负数和正数
            elif radix == 'b':
                return bin(value).replace("0b", type_str)  # replace处理同时支持负数和正数
            else:
                raise ValueError(f'Illegal radix "{radix}", while it must be in ["h", "d", "o", "b"]')

    def sv_repr(self):
        return Bit._gen_sv_repr(self)

    def sv_decl(self, name: str):
        ranges = (
            "".join(f" [{size - 1}:0]" for size in self.shape)
            if self.shape is not None
            else f" [{self.width - 1}:0]"
        )
        return f'bit{" signed" if self.signed else ""}{ranges} {name}'

    def cpp_decl(self, name: str):
        if self.width in (8, 16, 32, 64):
            if self.width == 8:
                t = "int8_t" if self.signed else "uint8_t"
            elif self.width == 16:
                t = "int16_t" if self.signed else "uint16_t"
            elif self.width == 32:
                t = "int32_t" if self.signed else "uint32_t"
            else:
                t = "int64_t" if self.signed else "uint64_t"
        else:
            signed = "true" if self.signed else "false"
            t = f"svtypes::BitValue<{self.width}, {signed}>"
        return f"{t} {name}"

    def _legacy_cpp_decl(self, name: str):
        if self.byte_num == 1:
            t = "int8_t" if self.signed else "uint8_t"
        elif self.byte_num == 2:
            t = "int16_t" if self.signed else "uint16_t"
        elif self.byte_num == 4:
            t = "int32_t" if self.signed else "uint32_t"
        elif self.byte_num == 8:
            t = "int64_t" if self.signed else "uint64_t"
        else:
            t = f"std::array<uint8_t, {self.byte_num}>"
        return f"{t} {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        sv_decl = self.sv_decl(name)
        if self._init_value is None:
            return f'{ind_str}{sv_decl};'
        else:
            return f'{ind_str}{sv_decl} = {Bit._gen_sv_repr(self._init_value, self.width, self.radix)};'

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"


if __name__ == '__main__':
    bit = Bit(1)
    bit.value = 1
    print(bit)
