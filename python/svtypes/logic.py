"""Four-state packed values and their SystemVerilog declaration forms."""

from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Any

from .base import BuiltInType
from .errors import DeclarationError
from .limits import DEFAULT_MAX_PACKED_BITS


@dataclass(frozen=True, slots=True)
class LogicValue:
    width: int
    value_mask: int = 0
    x_mask: int = 0
    z_mask: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError("LogicValue width must be positive")
        limit = (1 << self.width) - 1
        for name, value in (
            ("value_mask", self.value_mask),
            ("x_mask", self.x_mask),
            ("z_mask", self.z_mask),
        ):
            if value < 0 or value > limit:
                raise ValueError(f"LogicValue {name} exceeds width {self.width}")
        if self.x_mask & self.z_mask:
            raise ValueError("LogicValue X and Z masks must be disjoint")
        object.__setattr__(self, "value_mask", self.value_mask & ~(self.x_mask | self.z_mask))

    @classmethod
    def from_string(cls, text: str) -> "LogicValue":
        normalized = text.replace("_", "")
        if not normalized:
            raise ValueError("four-state literal cannot be empty")
        value = x_mask = z_mask = 0
        for index, character in enumerate(reversed(normalized)):
            bit = 1 << index
            if character == "1":
                value |= bit
            elif character in "xX":
                x_mask |= bit
            elif character in "zZ?":
                z_mask |= bit
            elif character != "0":
                raise ValueError(f"invalid four-state digit: {character!r}")
        return cls(len(normalized), value, x_mask, z_mask)

    def __str__(self) -> str:
        digits = []
        for index in reversed(range(self.width)):
            bit = 1 << index
            if self.x_mask & bit:
                digits.append("x")
            elif self.z_mask & bit:
                digits.append("z")
            else:
                digits.append("1" if self.value_mask & bit else "0")
        return "".join(digits)


class Logic(BuiltInType):
    """Packed SystemVerilog logic preserving 0, 1, X, and Z."""

    _default_rand = True
    _default_plusarg = True

    def __init__(
        self,
        width: int | tuple[int, ...] = 1,
        value: LogicValue | str | int | None = None,
        signed: bool = False,
        *,
        _sv_declaration_style: str = "logic",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not isinstance(signed, bool):
            raise TypeError("Logic signed must be a bool")
        if _sv_declaration_style not in {"logic", "reg"}:
            raise ValueError("Logic declaration style must be 'logic' or 'reg'")
        self._signed = signed
        self._sv_declaration_style = _sv_declaration_style
        if isinstance(width, tuple):
            if not width:
                raise ValueError("Logic shape cannot be empty")
            if any(not isinstance(size, int) or isinstance(size, bool) for size in width):
                raise TypeError("Every Logic shape dimension must be an integer")
            if any(size <= 0 for size in width):
                raise ValueError("Every Logic shape dimension must be positive")
            self._shape = None if len(width) == 1 else tuple(width)
            self._width = prod(width)
        else:
            if not isinstance(width, int) or isinstance(width, bool):
                raise TypeError("Logic width must be an integer or tuple of integers")
            if width <= 0:
                raise ValueError("Logic width must be positive")
            self._shape = None
            self._width = width
        if self._width > DEFAULT_MAX_PACKED_BITS:
            raise DeclarationError(
                f"Logic width {self._width} exceeds declaration limit {DEFAULT_MAX_PACKED_BITS}"
            )
        self._value = self._normalize(0 if value is None else value)

    @property
    def width(self) -> int:
        return self._width

    @property
    def shape(self) -> tuple[int, ...] | None:
        return self._shape

    @property
    def byte_num(self) -> int:
        return (self.width + 7) // 8

    @property
    def signed(self) -> bool:
        return self._signed

    @property
    def state_domain(self) -> str:
        return "4state"

    @property
    def sv_declaration_style(self) -> str:
        """The emitted SystemVerilog declaration keyword (``logic`` or ``reg``)."""
        return self._sv_declaration_style

    def _normalize(self, value: LogicValue | str | int) -> LogicValue:
        if isinstance(value, int):
            return LogicValue(self.width, value & ((1 << self.width) - 1))
        if isinstance(value, str):
            value = LogicValue.from_string(value)
        if not isinstance(value, LogicValue):
            raise TypeError(f"Expected LogicValue, str, or int, got {type(value)}")
        if value.width != self.width:
            raise ValueError(f"LogicValue width mismatch: expected {self.width}, got {value.width}")
        return value

    def pack(self, value: LogicValue | str | int) -> bytes:
        normalized = self._normalize(value)
        return b"".join(
            plane.to_bytes(self.byte_num, "little")
            for plane in (normalized.value_mask, normalized.x_mask, normalized.z_mask)
        )

    def unpack(self, bytes_: bytes) -> tuple[LogicValue, int]:
        required = self.byte_num * 3
        if len(bytes_) < required:
            raise ValueError(f"Not enough bytes to unpack Logic: need {required}, got {len(bytes_)}")
        planes = [
            int.from_bytes(bytes_[offset:offset + self.byte_num], "little")
            for offset in (0, self.byte_num, self.byte_num * 2)
        ]
        return LogicValue(self.width, *planes), required

    def sv_decl(self, name: str) -> str:
        ranges = (
            "".join(f" [{size - 1}:0]" for size in self.shape)
            if self.shape is not None
            else f" [{self.width - 1}:0]"
        )
        return f"{self.sv_declaration_style}{' signed' if self.signed else ''}{ranges} {name}"

    def cpp_decl(self, name: str) -> str:
        signed = "true" if self.signed else "false"
        return f"svtypes::LogicValue<{self.width}, {signed}> {name}"

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.sv_decl(name or self._attr_name)};"

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"


class _RegMeta(type):
    """Virtual type facade for the legacy ``reg`` declaration form."""

    def __call__(cls, *args: Any, **kwargs: Any) -> Logic:
        if "_sv_declaration_style" in kwargs:
            raise TypeError("Reg does not accept a declaration-style override")
        return Logic(*args, _sv_declaration_style="reg", **kwargs)

    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, Logic)

    def __subclasscheck__(cls, subclass: type) -> bool:
        return issubclass(subclass, Logic)


class Reg(metaclass=_RegMeta):
    """Create a :class:`Logic` value emitted as SystemVerilog ``reg``.

    ``Reg`` and ``Logic`` have one Python runtime data type and one binary
    representation.  Their only difference is the source declaration spelling.
    Therefore ``type(Reg(...)) is type(Logic(...))`` and either value satisfies
    ``isinstance(value, Reg)``.
    """

    pass
