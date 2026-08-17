from __future__ import annotations

from .base import BuiltInType
from .errors import DeclarationError, EncodeError, ResourceLimitError
from .limits import DEFAULT_MAX_DYNAMIC_LENGTH


class String(BuiltInType):
    _default_plusarg = True
    _default_cov = False

    def __init__(self, value: str | None=None, rand=None, plusarg=None, dump=None, cov=None, intelli=None, pack_bytes=True, *, max_bytes: int = DEFAULT_MAX_DYNAMIC_LENGTH) -> None:
        super().__init__(rand, plusarg, dump, cov, intelli, pack_bytes)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise DeclarationError("String max_bytes must be a positive integer")
        self._max_bytes = max_bytes

        if value is None:
            self._value = ""
            self._init_value = None
        else:
            self._value = self._normalize(value)
            self._init_value = self._value

    def _normalize(self, value):
        if isinstance(value, String):
            return value.value
        if not isinstance(value, str):
            raise TypeError(f"Expected str, got {type(value)}")
        return value

    def __call__(self, data: String | str=""):
        self.value = data
        return self

    @property
    def byte_num(self):
        return len(self.value.encode())

    def pack(self, value):
        val = self._normalize(value)
        encoded = val.encode()
        if len(encoded) > self._max_bytes:
            raise EncodeError(
                f"String length {len(encoded)} exceeds encoder limit {self._max_bytes}"
            )
        return len(encoded).to_bytes(self.DYN_INFO_BYTES, byteorder='little') + encoded

    def unpack(self, bytes_: bytes):
        in_byte_num = len(bytes_)
        if in_byte_num < self.DYN_INFO_BYTES:
            raise ValueError(
                f"Not enough bytes to unpack String length: "
                f"need {self.DYN_INFO_BYTES}, got {in_byte_num}"
            )
        byte_num = int.from_bytes(bytes_[0:self.DYN_INFO_BYTES], byteorder='little')
        if byte_num > self._max_bytes:
            raise ResourceLimitError(
                f"String length {byte_num} exceeds decoder limit {self._max_bytes}"
            )

        expected_total_bytes = byte_num + self.DYN_INFO_BYTES
        if in_byte_num < expected_total_bytes:
            raise ValueError(
                f"Not enough bytes to unpack String data: "
                f"need {expected_total_bytes}, got {in_byte_num}"
            )
        if len(bytes_) > expected_total_bytes:
            useful_bytes = bytes_[self.DYN_INFO_BYTES:expected_total_bytes]
        else:
            useful_bytes = bytes_[self.DYN_INFO_BYTES:]

        return useful_bytes.decode(), expected_total_bytes


    def sv_repr(self):
        return f'"{self.value}"'

    def cpp_decl(self, name: str):
        return f"std::string {name}"

    def sv_decl(self, name: str):
        return f"string {name}"

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        if self._init_value is None:
            return f'{ind_str}string {name};'
        else:
            return f'{ind_str}string {name} = "{self._init_value}";'

    def to_cpp_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"
