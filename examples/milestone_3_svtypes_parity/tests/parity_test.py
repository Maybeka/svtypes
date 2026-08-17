import math

import svx

from .types import Color, Inner, LargeMixedTx, M3Tx, ManyTypesTx, ParamMemberTx, ParamTx_MODE_5


PY_TO_SV_TAG_LEN = 256 * 1024
PY_TO_SV_PAYLOAD_LEN = 128 * 1024
PY_TO_SV_SAMPLES_LEN = 32 * 1024
PY_TO_SV_FLAGS_LEN = 64 * 1024

SV_TO_PY_TAG_LEN = 64 * 1024
SV_TO_PY_PAYLOAD_LEN = 32 * 1024
SV_TO_PY_SAMPLES_LEN = 16 * 1024
SV_TO_PY_FLAGS_LEN = 32 * 1024


def _py_payload_value(index: int) -> int:
    return (index * 3 + 0x13579) & 0x7FFFFFFF


def _sv_payload_value(index: int) -> int:
    return (index * 7 + 0x2468) & 0x7FFFFFFF


def _check_indexed(name: str, values: list[int], index: int, expected: int) -> None:
    if values[index] != expected:
        raise AssertionError(f"{name}[{index}]={values[index]} expected {expected}")


def _make_inner(a: int, b: int) -> Inner:
    inner = Inner()
    inner.a.value = a
    inner.b.value = b
    return inner


@svx.export
def python_puts_m3_tx():
    tx = M3Tx()
    tx.id.value = -3
    tx.data.value = [10, -20]
    tx.label.value = "M3"
    tx.addr.value = 0xBEEF
    tx.serial.value = -0x0102030405060708
    tx.color.value = Color.BLUE
    tx.ratio.value = 3.25
    tx.temp.value = -1.5
    tx.dyn.value = [7, 8, -9]
    tx.q.value = [11, -12]
    tx.inner.a.value = 42
    tx.inner.b.value = 0xAA

    svx.channel("m3.py_to_sv").put(tx)
    svx.display(f"python_puts_m3_tx size={len(tx.to_bytes())}")


@svx.export
def python_gets_m3_tx():
    tx = svx.channel("m3.sv_to_py").get(M3Tx)
    svx.display(f"python_gets_m3_tx id={tx.id.value} label={tx.label.value}")

    if tx.id.value != 100:
        raise AssertionError(f"unexpected id: {tx.id.value}")
    if tx.data.value != [1, 2]:
        raise AssertionError(f"unexpected data: {tx.data.value}")
    if tx.label.value != "SV":
        raise AssertionError(f"unexpected label: {tx.label.value!r}")
    if tx.addr.value != 0x1234:
        raise AssertionError(f"unexpected addr: 0x{tx.addr.value:04x}")
    if tx.serial.value != 0x0102030405060708:
        raise AssertionError(f"unexpected serial: {tx.serial.value}")
    if tx.color.value != Color.GREEN:
        raise AssertionError(f"unexpected color: {tx.color.value}")
    if abs(tx.ratio.value - (-2.5)) > 1e-12:
        raise AssertionError(f"unexpected ratio: {tx.ratio.value}")
    if abs(tx.temp.value - 6.25) > 1e-6:
        raise AssertionError(f"unexpected temp: {tx.temp.value}")
    if tx.dyn.value != [3, 4]:
        raise AssertionError(f"unexpected dyn: {tx.dyn.value}")
    if tx.q.value != [5, 6, 7]:
        raise AssertionError(f"unexpected q: {tx.q.value}")
    if tx.inner.a.value != -8:
        raise AssertionError(f"unexpected inner.a: {tx.inner.a.value}")
    if tx.inner.b.value != 0x55:
        raise AssertionError(f"unexpected inner.b: 0x{tx.inner.b.value:02x}")


@svx.export
def python_puts_many_types_tx():
    tx = ManyTypesTx()
    tx.u1.value = 1
    tx.u7.value = 0x7E
    tx.u9.value = 0x1AB
    tx.u33.value = 0x1_2345_6789
    tx.u65.value = 0x1_0123_4567_89AB_CDEF
    tx.s5.value = -7
    tx.s12.value = -1023
    tx.i32.value = -0x1234567
    tx.i64.value = -0x0102030405060708
    tx.color.value = Color.GREEN
    tx.text.value = "many-types"
    tx.fp64.value = -math.pi
    tx.fp32.value = 12.5
    tx.fixed_bits.value = [0, 1, 6, 7]
    tx.fixed_signed.value = [-1, -17, 15]
    tx.matrix.value = [[1, -2], [3, -4]]
    tx.dyn_bits.value = [0, 0x155, 0x3FF]
    tx.dyn_signed.value = [-1, -100, 127]
    tx.q_colors.value = [Color.RED, Color.BLUE, Color.GREEN]
    tx.assoc.value = {"apple": 11, "banana": -22}
    tx.q_inner.value = [_make_inner(10, 0x11), _make_inner(-20, 0x22)]
    tx.inner_arr.value = [_make_inner(30, 0x33), _make_inner(-40, 0x44)]
    tx.nested.a.value = 50
    tx.nested.b.value = 0x55

    svx.channel("m3.many.py_to_sv").put(tx)
    svx.display(f"python_puts_many_types_tx size={len(tx.to_bytes())}")


@svx.export
def python_gets_many_types_tx():
    tx = svx.channel("m3.many.sv_to_py").get(ManyTypesTx)
    svx.display(f"python_gets_many_types_tx size={len(tx.to_bytes())}")

    expected = {
        "u1": 0,
        "u7": 0x55,
        "u9": 0x12A,
        "u33": 0x1_1111_1111,
        "u65": 0x1_FEDC_BA98_7654_3210,
        "s5": -8,
        "s12": 1023,
        "i32": 0x10203040,
        "i64": 0x0102030405060708,
    }
    for name, value in expected.items():
        actual = getattr(tx, name).value
        if actual != value:
            raise AssertionError(f"unexpected {name}: {actual} expected {value}")
    if tx.color.value != Color.BLUE:
        raise AssertionError(f"unexpected color: {tx.color.value}")
    if tx.text.value != "sv-many-types":
        raise AssertionError(f"unexpected text: {tx.text.value!r}")
    if abs(tx.fp64.value - 2.75) > 1e-12:
        raise AssertionError(f"unexpected fp64: {tx.fp64.value}")
    if abs(tx.fp32.value - (-3.5)) > 1e-6:
        raise AssertionError(f"unexpected fp32: {tx.fp32.value}")
    if tx.fixed_bits.value != [7, 6, 1, 0]:
        raise AssertionError(f"unexpected fixed_bits: {tx.fixed_bits.value}")
    if tx.fixed_signed.value != [-16, 0, 15]:
        raise AssertionError(f"unexpected fixed_signed: {tx.fixed_signed.value}")
    if tx.matrix.value != [[9, 8], [7, 6]]:
        raise AssertionError(f"unexpected matrix: {tx.matrix.value}")
    if tx.dyn_bits.value != [1, 2, 0x3FE]:
        raise AssertionError(f"unexpected dyn_bits: {tx.dyn_bits.value}")
    if tx.dyn_signed.value != [-128, -1, 0, 127]:
        raise AssertionError(f"unexpected dyn_signed: {tx.dyn_signed.value}")
    if tx.q_colors.value != [Color.GREEN, Color.RED]:
        raise AssertionError(f"unexpected q_colors: {tx.q_colors.value}")
    if tx.assoc.value != {"cat": 31, "dog": -41}:
        raise AssertionError(f"unexpected assoc: {tx.assoc.value}")
    if [(v.a.value, v.b.value) for v in tx.q_inner] != [(101, 0xA1), (-102, 0xA2)]:
        raise AssertionError("unexpected q_inner")
    if [(v.a.value, v.b.value) for v in tx.inner_arr] != [(201, 0xB1), (-202, 0xB2)]:
        raise AssertionError("unexpected inner_arr")
    if tx.nested.a.value != 303 or tx.nested.b.value != 0xC3:
        raise AssertionError("unexpected nested object")


@svx.export
def python_puts_param_tx():
    tx = ParamTx_MODE_5()
    tx.id.value = 77
    tx.payload.value = [0x001, 0xABC, 0xFFF]
    tx.nested.a.value = -17
    tx.nested.b.value = 0x5A

    svx.channel("m3.param.py_to_sv").put(tx)
    svx.display(f"python_puts_param_tx size={len(tx.to_bytes())}")


@svx.export
def python_gets_param_tx():
    tx = svx.channel("m3.param.sv_to_py").get(ParamTx_MODE_5)
    svx.display(f"python_gets_param_tx id={tx.id.value} mode={ParamTx_MODE_5.MODE.value}")

    if tx.id.value != 88:
        raise AssertionError(f"unexpected param id: {tx.id.value}")
    if tx.payload.value != [0x010, 0x020, 0x030]:
        raise AssertionError(f"unexpected param payload: {tx.payload.value}")
    if tx.nested.a.value != 123 or tx.nested.b.value != 0xC4:
        raise AssertionError("unexpected param nested object")


@svx.export
def python_puts_param_member_tx():
    tx = ParamMemberTx()
    tx.tag.value = 501
    tx.param_item.id.value = 502
    tx.param_item.payload.value = [0x101, 0x202, 0x303]
    tx.param_item.nested.a.value = 503
    tx.param_item.nested.b.value = 0xD5

    svx.channel("m3.param_member.py_to_sv").put(tx)
    svx.display(
        "python_puts_param_member_tx "
        f"size={len(tx.to_bytes())} mode={tx.param_item.MODE.value}"
    )


@svx.export
def python_gets_param_member_tx():
    tx = svx.channel("m3.param_member.sv_to_py").get(ParamMemberTx)
    svx.display(
        "python_gets_param_member_tx "
        f"tag={tx.tag.value} mode={tx.param_item.MODE.value}"
    )

    if tx.tag.value != 601:
        raise AssertionError(f"unexpected param member tag: {tx.tag.value}")
    if tx.param_item.MODE.value != 5:
        raise AssertionError(f"unexpected param member mode: {tx.param_item.MODE.value}")
    if tx.param_item.id.value != 602:
        raise AssertionError(f"unexpected param member id: {tx.param_item.id.value}")
    if tx.param_item.payload.value != [0x111, 0x222, 0x333]:
        raise AssertionError(f"unexpected param member payload: {tx.param_item.payload.value}")
    if tx.param_item.nested.a.value != 603 or tx.param_item.nested.b.value != 0xE6:
        raise AssertionError("unexpected param member nested object")


@svx.export
def python_puts_large_mixed_tx():
    tx = LargeMixedTx()
    tx.tag.value = "PY-LARGE-" + ("p" * (PY_TO_SV_TAG_LEN - len("PY-LARGE-")))
    tx.header.value = [0x12345678, -1, 0x10203040, -0x1234]
    tx.payload.value = [_py_payload_value(i) for i in range(PY_TO_SV_PAYLOAD_LEN)]
    tx.samples.value = [-(i + 1) for i in range(PY_TO_SV_SAMPLES_LEN)]
    tx.flags.value = [(i * 5) & 0xFFFF for i in range(PY_TO_SV_FLAGS_LEN)]
    tx.marker.value = 0x0123456789ABCDEF

    svx.channel("m3.large.py_to_sv").put(tx)
    svx.display(
        "python_puts_large_mixed_tx "
        f"bytes={len(tx.to_bytes())} "
        f"payload={PY_TO_SV_PAYLOAD_LEN} "
        f"samples={PY_TO_SV_SAMPLES_LEN} "
        f"flags={PY_TO_SV_FLAGS_LEN}"
    )


@svx.export
def python_gets_large_mixed_tx():
    tx = svx.channel("m3.large.sv_to_py").get(LargeMixedTx)
    svx.display(
        "python_gets_large_mixed_tx "
        f"bytes={len(tx.to_bytes())} "
        f"payload={len(tx.payload.value)} "
        f"samples={len(tx.samples.value)} "
        f"flags={len(tx.flags.value)}"
    )

    if len(tx.tag.value) != SV_TO_PY_TAG_LEN or not tx.tag.value.startswith("SV-LARGE-"):
        raise AssertionError(f"unexpected tag len/prefix: {len(tx.tag.value)} {tx.tag.value[:16]!r}")
    if tx.header.value != [9, 8, 7, 6]:
        raise AssertionError(f"unexpected header: {tx.header.value}")
    if tx.marker.value != 0x0FEDCBA987654321:
        raise AssertionError(f"unexpected marker: 0x{tx.marker.value:016x}")
    if len(tx.payload.value) != SV_TO_PY_PAYLOAD_LEN:
        raise AssertionError(f"unexpected payload length: {len(tx.payload.value)}")
    if len(tx.samples.value) != SV_TO_PY_SAMPLES_LEN:
        raise AssertionError(f"unexpected samples length: {len(tx.samples.value)}")
    if len(tx.flags.value) != SV_TO_PY_FLAGS_LEN:
        raise AssertionError(f"unexpected flags length: {len(tx.flags.value)}")

    for index in (0, SV_TO_PY_PAYLOAD_LEN // 2, SV_TO_PY_PAYLOAD_LEN - 1):
        _check_indexed("payload", tx.payload.value, index, _sv_payload_value(index))
    for index in (0, SV_TO_PY_SAMPLES_LEN // 2, SV_TO_PY_SAMPLES_LEN - 1):
        _check_indexed("samples", tx.samples.value, index, index + 100)
    for index in (0, SV_TO_PY_FLAGS_LEN // 2, SV_TO_PY_FLAGS_LEN - 1):
        _check_indexed("flags", tx.flags.value, index, (index * 11) & 0xFFFF)
