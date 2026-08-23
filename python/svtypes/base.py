from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FieldOptions:
    rand: bool | None = None
    randc: bool = False
    plusarg: bool | None = None
    dump: bool | None = None
    cov: bool | None = None
    intelli: bool | None = None
    pack_bytes: bool | None = True


class TypeBase:
    DYN_INFO_BYTES = 4

    INDENT_STEP = 2
    IND = ' ' * INDENT_STEP

    _default_rand = False
    _default_plusarg = False
    _default_dump = True
    _default_cov = True
    _default_intelli = False
    _default_pack_bytes = True

    def __init__(
        self,
        rand: bool | None = None,
        plusarg: bool | None = None,
        dump: bool | None = None,
        cov: bool | None = None,
        intelli: bool | None = None,
        pack_bytes: bool | None = True,
        randc: bool = False,
    ) -> None:
        '''
        :param rand: 该变量在SystemVerilog侧是否有rand修饰符
        :param plusarg: 是否自动生成针对该变量的plusargs的覆盖语句
        :param dump: 是否在print相关函数中打印该变量
        :param cov: 是否自动生成该变量相关的单点覆盖率
        :param intelli: 是否在智能操作中考虑该变量
        :param pack_bytes: 在将对象打包成byte流或从byte流解包时，是否包含该变量
        '''
        for name, value in {
            "rand": rand,
            "plusarg": plusarg,
            "dump": dump,
            "cov": cov,
            "intelli": intelli,
            "pack_bytes": pack_bytes,
        }.items():
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"Field policy {name} must be True, False, or None")
        if not isinstance(randc, bool):
            raise TypeError("Field policy randc must be True or False")
        if randc and rand is not None:
            raise ValueError("randc=True cannot be combined with an explicit rand policy")
        self._field_options = FieldOptions(
            rand=rand,
            randc=randc,
            plusarg=plusarg,
            dump=dump,
            cov=cov,
            intelli=intelli,
            pack_bytes=pack_bytes,
        )

        self._attr_name = ''

    def __set_name__(self, owner, name):
        self._attr_name = name
        self._storage_key = f'_svtypes_{name}'

    def _normalize(self, value):
        """Normalize the value according to the type rules (truncation, etc.)."""
        return value

    @property
    def rand(self):
        if self.randc:
            return True
        return self._default_rand if self._field_options.rand is None else self._field_options.rand

    @property
    def randc(self) -> bool:
        return self._field_options.randc

    @property
    def plusarg(self):
        return self._default_plusarg if self._field_options.plusarg is None else self._field_options.plusarg

    @property
    def dump(self):
        return self._default_dump if self._field_options.dump is None else self._field_options.dump

    @property
    def cov(self):
        return self._default_cov if self._field_options.cov is None else self._field_options.cov

    @property
    def intelli(self):
        return self._default_intelli if self._field_options.intelli is None else self._field_options.intelli

    @property
    def pack_bytes(self):
        return self._default_pack_bytes if self._field_options.pack_bytes is None else self._field_options.pack_bytes

    @property
    def field_options(self) -> FieldOptions:
        return self._field_options

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, val):
        self._value = self._normalize(val)


    @staticmethod
    def _get_signed_bit_width(n):
        if n >= 0:
            return n.bit_length() + 1
        else:
            # 对于负数，例如 -3 (110，支持-2**k~2**k-1)，bit_length结果是2，但需要3位才能表示
            # 特殊情况：如果是 -2^k，bit_length和实际需要的位宽一致
            return (n + 1).bit_length() + 1


    def pack(self, value) -> bytes:
        raise NotImplementedError

    def unpack(self, bytes_: bytes) -> tuple[Any, int]:
        raise NotImplementedError

    def to_bytes(self):
        return self.pack(self.value)

    def from_bytes(self, bytes_: bytes):
        val, count = self.unpack(bytes_)
        self.value = val
        return count

    def sv_repr(self) -> str:
        raise AttributeError(f'sv_repr function of {self.__class__.__name__} shall not be accessed directly!')

    def sv_decl(self, name: str) -> str:
        raise AttributeError(f'sv_decl function of {self.__class__.__name__} shall not be accessed directly!')

    def cpp_decl(self, name: str) -> str:
        raise AttributeError(f'cpp_decl function of {self.__class__.__name__} shall not be accessed directly!')

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        raise AttributeError(f'to_sv_code function of {self.__class__.__name__} shall not be accessed directly!')


class BuiltInType(TypeBase):
    pass

class UserDefinedType(TypeBase):
    pass
