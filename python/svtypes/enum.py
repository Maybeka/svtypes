import enum
from math import prod
from typing import cast, Any, Optional
from functools import cached_property

from .base import UserDefinedType
from .errors import DeclarationError, DecodeError


class _EnumBaseSpec:
    """PEP 560 adapter used only while creating an ``Enum[...]`` subclass."""

    def __init__(self, enum_base: type, width: int, signed: bool, sv_base_kind: str) -> None:
        self._enum_base = enum_base
        self._width = width
        self._signed = signed
        self._sv_base_kind = sv_base_kind

    def __mro_entries__(self, bases: tuple[Any, ...]) -> tuple[type, ...]:
        adapter = type(
            "_SvTypesEnumBase",
            (self._enum_base,),
            {
                "_svtypes_enum_base_adapter": True,
                "_svtypes_enum_base_spec": (self._width, self._signed, self._sv_base_kind),
            },
        )
        return (adapter,)


class Enum(UserDefinedType):
    _default_rand = True
    _default_plusarg = True
    """
    SystemVerilog Enum 模拟基类
    1. 子类通过定义 Enum 定义常量
    2. 支持实例化作为变量使用
    3. 支持生成 SV 代码
    """

    @classmethod
    def __class_getitem__(cls, base: Any):
        """Return a class-base adapter for ``class E(Enum[Bit[8]])``."""
        from .bit import Bit
        from .int import Int, LongInt
        from .typespec import Signed, TypeSpec, Unsigned

        if base is Int:
            width, signed, sv_base_kind = 32, True, "int"
        elif base is LongInt:
            width, signed, sv_base_kind = 64, True, "longint"
        elif isinstance(base, TypeSpec) and base.base is Bit:
            shape, marker = base.parameters
            width = prod(shape) if isinstance(shape, tuple) else shape
            signed = marker is Signed
            sv_base_kind = "bit"
            if marker not in (Signed, Unsigned):
                raise TypeError("Enum bit base has an invalid signedness marker")
        else:
            raise TypeError("Enum[...] accepts Int, LongInt, or a Bit[...] specification")
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            raise DeclarationError("Enum base width must be a positive integer")
        return _EnumBaseSpec(cls, width, signed, sv_base_kind)

    def __init_subclass__(
        cls,
        *,
        width: int | None = None,
        signed: bool | None = None,
        **kwargs,
    ):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("_svtypes_enum_base_adapter", False):
            return
        if width is not None or signed is not None:
            raise TypeError(
                "Enum(width=..., signed=...) is no longer supported; "
                "use Enum[Bit[width]], Enum[Bit[width, Signed]], Enum[Int], "
                "or bare Enum"
            )
        inherited_spec = getattr(cls, "_svtypes_enum_base_spec", None)
        if inherited_spec is not None:
            width, signed, sv_base_kind = inherited_spec
        else:
            sv_base_kind = None
        # IEEE 1800's omitted enum base type is ``int``.  Keep the public
        # Python spelling equally direct: ``class Op(Enum): ...``.
        if width is None and signed is None:
            width, signed = 32, True
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            raise DeclarationError(f"{cls.__name__} must declare a positive integer width")
        if not isinstance(signed, bool):
            raise DeclarationError(f"{cls.__name__} must declare signed=True or False")
        cls._width = width
        cls._signed = signed
        cls._sv_base_kind = sv_base_kind

        # 使用 cast 告诉检查器这是一个 IntEnum，避免“不可调用”报错
        cls._enum_map: dict[str, enum.IntEnum] = {}
        cls._enum_cls: Any = None  # enum.IntEnum
        cls._enum_items: list[enum.IntEnum] = []
        cls._enum_item_num: int
        cls._max_item_width: int


        # 1. 提取成员
        members = {k: v for k, v in cls.__dict__.items()
                   if not k.startswith('_') and isinstance(v, int)}

        # 2. 动态创建 Enum 类
        # 使用 cast(Any, ...) 绕过“IntEnum 对象不可调用”的静态检查报错
        if not members:
            raise DeclarationError(f"{cls.__name__} must declare at least one enum member")
        numeric_values = list(members.values())
        if len(set(numeric_values)) != len(numeric_values):
            raise DeclarationError(f"{cls.__name__} contains duplicate numeric enum values")
        minimum = -(1 << (width - 1)) if signed else 0
        maximum = (1 << (width - 1)) - 1 if signed else (1 << width) - 1
        for name, value in members.items():
            if value < minimum or value > maximum:
                raise DeclarationError(
                    f"{cls.__name__}.{name}={value} does not fit "
                    f"{'signed' if signed else 'unsigned'} {width}-bit storage"
                )

        inner_enum = cast(Any, enum.IntEnum)(cls.__name__ + "Internal", members)
        cls._enum_cls = inner_enum

        # 3. 建立映射并写回类属性
        cls._enum_map = {name: member for name, member in inner_enum.__members__.items()}
        cls._enum_items = list(cls._enum_map.values())
        cls._enum_item_num = len(cls._enum_items)
        cls._max_item_width = max(cls._get_signed_bit_width(item.value) for item in cls._enum_items)
        for name, member in cls._enum_map.items():
            setattr(cls, name, member)

    def __init__(self, initial_value: Optional[Any] = None, rand=None, plusarg=None, dump=None, cov=None, intelli=None, pack_bytes=True, randc=False):
        super().__init__(
            rand=rand,
            plusarg=plusarg,
            dump=dump,
            cov=cov,
            intelli=intelli,
            pack_bytes=pack_bytes,
            randc=randc,
        )

        if initial_value is None:
            # 默认取第一个成员
            self._value = self._enum_items[0]
            self._init_value = None
        else:
            self._value = self._normalize(initial_value)
            self._init_value = self._value

    def _normalize(self, val: Any):
        """支持数值赋值 op.value = 1 或 名字赋值 op.value = "ADD" """
        if isinstance(val, str):
            # 1. 尝试从名字映射中获取
            if val in self._enum_map:
                return self._enum_map[val]
            else:
                raise ValueError(f"'{val}' 不是有效的 {self.__class__.__name__} 成员名称")
        elif isinstance(val, int):
            # 2. 尝试从数值转换（IntEnum 本身支持传入 value 返回成员）
            try:
                return self._enum_cls(val)
            except ValueError:
                raise ValueError(f"数值 {val} 不在枚举 {self.__class__.__name__} 的合法范围内")
        else:
            # 3. 如果是直接赋值枚举成员对象
            if isinstance(val, self._enum_cls):
                return val
            else:
                raise TypeError(f"不支持的赋值类型: {type(val)}")

    def name(self):
        return self._value.name

    @property
    def width(self) -> int:
        return self.__class__._width

    @property
    def signed(self) -> bool:
        return self.__class__._signed

    @property
    def state_domain(self) -> str:
        return "2state"

    def first(self):
        self.value = self._enum_items[0]
        return self

    def next(self):
        next_index = (self._enum_items.index(self._value) + 1) % self._enum_item_num
        self.value = self._enum_items[next_index]
        return self

    def prev(self):
        prev_index = (self._enum_items.index(self._value) - 1) % self._enum_item_num
        self.value = self._enum_items[prev_index]
        return self

    def last(self):
        self.value = self._enum_items[-1]
        return self

    @classmethod
    def to_sv_enum(cls, level=0) -> str:
        if not cls._enum_map:
            return ""
        ind_str = cls.IND * level
        if cls._sv_base_kind is None:
            header = f"{ind_str}typedef enum {{"
        elif cls._sv_base_kind == "int":
            header = f"{ind_str}typedef enum int {{"
        elif cls._sv_base_kind == "longint":
            header = f"{ind_str}typedef enum longint {{"
        else:
            sv_type = f"bit{' signed' if cls._signed else ''} [{cls._width - 1}:0]"
            header = f"{ind_str}typedef enum {sv_type} {{"
        lines = [header]
        items = [f"{ind_str}{cls.IND}{name} = {m.value}" for name, m in cls._enum_map.items()]
        lines.append(",\n".join(items))
        lines.append(f"{ind_str}}} {cls.__name__};")
        return "\n".join(lines)

    @classmethod
    def to_cpp_enum(cls, level=0) -> str:
        if not cls._enum_map:
            return ""
        ind_str = cls.IND * level
        if cls._width in (8, 16, 32, 64):
            prefix = "int" if cls._signed else "uint"
            cpp_type = f"{prefix}{cls._width}_t"
            lines = [f"{ind_str}enum class {cls.__name__} : {cpp_type} {{"]
            items = [f"{ind_str}{cls.IND}{name} = {m.value}" for name, m in cls._enum_map.items()]
            lines.append(",\n".join(items))
            lines.append(f"{ind_str}}};")
            return "\n".join(lines)

        # C++ has no integral types at arbitrary bit widths.  Preserve the
        # generated API (``State::READY`` constants, copy assignment and
        # comparison) with a thin wrapper over the shared packed-value type.
        signed = "true" if cls._signed else "false"
        lines = [f"{ind_str}struct {cls.__name__} {{"]
        lines.append(f"{ind_str}{cls.IND}using svtypes_enum_wrapper_tag = void;")
        lines.append(f"{ind_str}{cls.IND}using storage_type = svtypes::BitValue<{cls._width}, {signed}>;")
        lines.append(f"{ind_str}{cls.IND}storage_type value{{}};")
        lines.append(f"{ind_str}{cls.IND}{cls.__name__}() = default;")
        lines.append(f"{ind_str}{cls.IND}explicit {cls.__name__}(storage_type raw) : value(raw) {{}}")
        lines.append(
            f"{ind_str}{cls.IND}{cls.__name__}(std::initializer_list<uint8_t> raw) : value(raw) {{}}"
        )
        lines.append(f"{ind_str}{cls.IND}bool operator==(const {cls.__name__}&) const = default;")
        lines.append(f"{ind_str}{cls.IND}bool operator!=(const {cls.__name__}&) const = default;")
        for name in cls._enum_map:
            lines.append(f"{ind_str}{cls.IND}static const {cls.__name__} {name};")
        lines.append(f"{ind_str}}};")
        width_mask = (1 << cls._width) - 1
        byte_count = cls._item_byte_num()
        for name, member in cls._enum_map.items():
            raw = member.value & width_mask
            bytes_literal = ", ".join(f"0x{(raw >> (8 * index)) & 0xff:02x}" for index in range(byte_count))
            lines.append(
                f"{ind_str}inline const {cls.__name__} {cls.__name__}::{name}{{"
                f"std::initializer_list<uint8_t>{{{bytes_literal}}}}};"
            )
        return "\n".join(lines)

    def sv_decl(self, name: str):
        return f"{self.__class__.__name__} {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        if self._init_value is None:
            return f'{ind_str}{self.__class__.__name__} {name};'
        else:
            return f'{ind_str}{self.__class__.__name__} {name} = {self._init_value.name};'

    def to_cpp_code(self, level=0, name: str | None = None) -> str:
        name = name or self._attr_name
        ind_str = self.IND * level
        return f"{ind_str}{self.__class__.__name__} {name};"

    def cpp_decl(self, name: str):
        return f"{self.__class__.__name__} {name}"

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self._value.name}({self._value.value})>"

    @classmethod
    def _item_byte_num(cls):
        return (cls._width + 7) // 8

    @cached_property
    def byte_num(self):
        return self._item_byte_num()

    def pack(self, value):
        val = self._normalize(value)
        unsigned = val.value & ((1 << self.width) - 1)
        return unsigned.to_bytes(self.byte_num, byteorder='little', signed=False)

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
        raw = int.from_bytes(useful_bytes, byteorder='little', signed=False)
        raw &= (1 << self.width) - 1
        if self.signed and raw & (1 << (self.width - 1)):
            raw -= 1 << self.width
        val_int = raw
        # Enum items are usually small ints.
        # But we should normalize to get the Enum member.
        try:
            val = self._normalize(val_int)
        except ValueError as exc:
            raise DecodeError(
                f"Invalid {self.__class__.__name__} encoded value {val_int}"
            ) from exc
        return val, self.byte_num

if __name__ == '__main__':
    class e_op_code(Enum[Bit[8]]):
        ADD = 0
        SUB = 1
        MUL = 4

    # 生成 SV 代码
    print(e_op_code.to_sv_enum())

    # 方式 A：默认实例化
    op = e_op_code()
    print(op) # 输出默认值 ADD(0)
    print(op.first())
    print(op.next())
    print(op.prev())
    print(op.last())

    # 方式 B：通过属性修改值
    op.value = e_op_code.SUB
    print(op.value) # 输出 1

    # 方式 C：实例化时直接传参
    op2 = e_op_code(e_op_code.MUL)
    print(op2) # 输出 MUL(4)
