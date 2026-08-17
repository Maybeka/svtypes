from svtypes import (
    Array,
    AssocArray,
    Bits,
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
    b = Bits(8)


@svobj
class BaseTx(SvObject):
    id = Int()
    data = Array(Int(), 2)
    label = String()


@svobj
class M3Tx(BaseTx):
    addr = Bits(16)
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
    flags = DynArray(Bits(16))
    marker = Bits(64)


@svobj
class ManyTypesTx(SvObject):
    u1 = Bits(1)
    u7 = Bits(7)
    u9 = Bits(9)
    u33 = Bits(33)
    u65 = Bits(65)
    s5 = Bits(5, signed=True)
    s12 = Bits(12, signed=True)
    i32 = Int()
    i64 = LongInt()
    color = Color()
    text = String()
    fp64 = Real()
    fp32 = ShortReal()
    fixed_bits = Array(Bits(3), 4)
    fixed_signed = Array(Bits(6, signed=True), 3)
    matrix = Array(Array(Int(), 2), 2)
    dyn_bits = DynArray(Bits(10))
    dyn_signed = DynArray(Bits(9, signed=True))
    q_colors = Queue(Color())
    assoc = AssocArray(String(), Int())
    q_inner = Queue(Inner())
    inner_arr = Array(Inner(), 2)
    nested = Inner()


@svobj
class ParamTx(SvObject):
    MODE = Parameter()(7)
    id = Int()
    payload = Array(Bits(12), 3)
    nested = Inner()


ParamTx_MODE_5 = ParamTx.specialize(MODE=5)
ParamTx_MODE_5_MEMBER = ParamTx.specialize(emit_class=False, MODE=5)


@svobj
class ParamMemberTx(SvObject):
    tag = Int()
    param_item = ParamTx_MODE_5_MEMBER()
