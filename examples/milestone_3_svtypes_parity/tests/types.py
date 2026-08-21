from svtypes import (
    Array,
    AssocArray,
    Bit,
    DynArray,
    Enum,
    Int,
    LongInt,
    Parameter,
    Queue,
    Real,
    ShortReal,
    String,
    SvObject,
    svobj,
)


class Color(Enum, width=32, signed=False):
    RED = 0
    GREEN = 1
    BLUE = 2


@svobj
class Inner(SvObject):
    a = Int()
    b = Bit(8)


@svobj
class BaseTx(SvObject):
    id = Int()
    data = Array(Int(), 2)
    label = String()


@svobj
class M3Tx(BaseTx):
    addr = Bit(16)
    serial = LongInt()
    color = Color()
    ratio = Real()
    temp = ShortReal()
    dyn = DynArray(Int())
    q = Queue(Int())
    inner = Inner()


@svobj
class LargeMixedTx(SvObject):
    tag = String()
    header = Array(Int(), 4)
    payload = DynArray(Int())
    samples = Queue(Int())
    flags = DynArray(Bit(16))
    marker = Bit(64)


@svobj
class ManyTypesTx(SvObject):
    u1 = Bit(1)
    u7 = Bit(7)
    u9 = Bit(9)
    u33 = Bit(33)
    u65 = Bit(65)
    s5 = Bit(5, signed=True)
    s12 = Bit(12, signed=True)
    i32 = Int()
    i64 = LongInt()
    color = Color()
    text = String()
    fp64 = Real()
    fp32 = ShortReal()
    fixed_bits = Array(Bit(3), 4)
    fixed_signed = Array(Bit(6, signed=True), 3)
    matrix = Array(Array(Int(), 2), 2)
    dyn_bits = DynArray(Bit(10))
    dyn_signed = DynArray(Bit(9, signed=True))
    q_colors = Queue(Color())
    assoc = AssocArray(String(), Int())
    q_inner = Queue(Inner())
    inner_arr = Array(Inner(), 2)
    nested = Inner()


@svobj
class ParamTx(SvObject):
    MODE = Parameter()(7)
    id = Int()
    payload = Array(Bit(12), 3)
    nested = Inner()


ParamTx_MODE_5 = ParamTx.specialize(MODE=5)


@svobj
class ParamMemberTx(SvObject):
    tag = Int()
    param_item = ParamTx_MODE_5()
