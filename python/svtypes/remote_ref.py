"""Opaque foreign-object reference values."""

from __future__ import annotations

from dataclasses import dataclass
import json
import struct
from typing import Any

from .base import BuiltInType


@dataclass(frozen=True, slots=True)
class RemoteRefValue:
    target_type_name: str
    object_number: int = 0

    def __post_init__(self) -> None:
        if not self.target_type_name:
            raise ValueError("RemoteRef target_type_name cannot be empty")
        if not 0 <= self.object_number <= (1 << 64) - 1:
            raise ValueError(f"RemoteRef object_number is outside uint64: {self.object_number}")

    @property
    def is_null(self) -> bool:
        return self.object_number == 0


class RemoteRef(BuiltInType):
    """Codec for one immutable, nullable, foreign-owned object identifier."""
    _default_cov = False

    def __init__(self, target_type_name: str, value: RemoteRefValue | int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not isinstance(target_type_name, str) or not target_type_name:
            raise ValueError("RemoteRef target_type_name must be a nonempty string")
        self._target_type_name = target_type_name
        self._value = self._normalize(value)

    @property
    def target_type_name(self) -> str:
        return self._target_type_name

    @property
    def width(self) -> int:
        return 64

    @property
    def byte_num(self) -> int:
        return 8

    @property
    def state_domain(self) -> str:
        return "2state"

    def _normalize(self, value: RemoteRefValue | int | None) -> RemoteRefValue:
        if value is None:
            return RemoteRefValue(self.target_type_name, 0)
        if isinstance(value, int):
            return RemoteRefValue(self.target_type_name, value)
        if not isinstance(value, RemoteRefValue):
            raise TypeError(f"Expected RemoteRefValue, int, or None, got {type(value)}")
        if value.target_type_name != self.target_type_name:
            raise TypeError(
                "RemoteRef target type mismatch: "
                f"expected {self.target_type_name}, got {value.target_type_name}"
            )
        return value

    def pack(self, value: RemoteRefValue | int | None) -> bytes:
        normalized = self._normalize(value)
        return struct.pack("<Q", normalized.object_number)

    def unpack(self, bytes_: bytes) -> tuple[RemoteRefValue, int]:
        if len(bytes_) < 8:
            raise ValueError(f"Not enough bytes to unpack RemoteRef: need 8, got {len(bytes_)}")
        return RemoteRefValue(self.target_type_name, struct.unpack("<Q", bytes_[:8])[0]), 8

    def sv_decl(self, name: str) -> str:
        return f"svtypes_pkg::remote_ref {name}"

    def cpp_decl(self, name: str) -> str:
        target = json.dumps(self.target_type_name)
        initializer = f"{{{target}, 0}}"
        return f"svtypes::RemoteRefValue {name}{initializer}"

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        field_name = name or self._attr_name
        target = self.target_type_name.replace("\\", "\\\\").replace('"', '\\"')
        return f'{self.IND * level}{self.sv_decl(field_name)} = new("{target}");'

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"
