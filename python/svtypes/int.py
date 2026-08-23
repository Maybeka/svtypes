from .bit import Bit

class Int(Bit):
    def __init__(self, value=0, radix=Bit.Dec, rand=None, plusarg=None, dump=None, cov=None, intelli=None, pack_bytes=True, randc=False) -> None:
        super().__init__(
            width=32,
            value=value,
            signed=True,
            radix=radix,
            rand=rand,
            plusarg=plusarg,
            dump=dump,
            cov=cov,
            intelli=intelli,
            pack_bytes=pack_bytes,
            randc=randc,
        )

    def sv_decl(self, name: str):
        return f"int {name}"

    def cpp_decl(self, name: str):
        return f"int32_t {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"


class LongInt(Bit):
    def __init__(self, value=0, radix=Bit.Dec, rand=None, plusarg=None, dump=None, cov=None, intelli=None, pack_bytes=True, randc=False) -> None:
        super().__init__(
            width=64,
            value=value,
            signed=True,
            radix=radix,
            rand=rand,
            plusarg=plusarg,
            dump=dump,
            cov=cov,
            intelli=intelli,
            pack_bytes=pack_bytes,
            randc=randc,
        )

    def sv_decl(self, name: str):
        return f"longint {name}"

    def cpp_decl(self, name: str):
        return f"int64_t {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.sv_decl(name)};"

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.cpp_decl(name)};"
