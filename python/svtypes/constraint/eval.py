"""2-state evaluation of Typed Constraint IR."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from .ir import BOOL, Expr, IRType, bv


@dataclass
class Value:
    bits: int
    ty: IRType
    undef: bool = False


def eval_bool(expr: Expr, env: Mapping[str, int], vars_width: Mapping[str, tuple[int, bool]]) -> bool:
    value = eval_expr(expr, env, vars_width)
    return (not value.undef) and value.ty.is_bool and bool(value.bits)


def eval_dist_weight(
    expr: Expr,
    env: Mapping[str, int],
    vars_width: Mapping[str, tuple[int, bool]],
) -> tuple[Fraction, Fraction, bool]:
    """Return selected and upper-bound weights for one satisfiable dist Expr.

    The upper bound is the sum of all positive item weights.  It permits exact
    rejection sampling without prematurely evaluating the Python DSL.
    """

    if expr.op != "dist":
        raise ValueError("eval_dist_weight() requires a dist expression")
    value = eval_expr(expr.args[0], env, vars_width)
    selected = Fraction(0)
    bound = Fraction(0)
    undef = value.undef
    for item in expr.args[1]:
        low = eval_expr(item.low, env, vars_width)
        high = eval_expr(item.high, env, vars_width) if item.high is not None else None
        weight = eval_expr(item.weight, env, vars_width)
        undef = undef or low.undef or weight.undef or (high.undef if high is not None else False)
        raw_weight = _integer_value(weight)
        if raw_weight < 0:
            undef = True
            continue
        if raw_weight == 0:
            continue
        count = 1
        if high is not None:
            count = _range_size(low, high)
            if count <= 0:
                continue
        contribution = Fraction(raw_weight, 1 if item.each else count)
        bound += contribution
        if high is None and _equal_values(value, low):
            selected += contribution
        elif high is not None and _in_range(value, low, high):
            selected += contribution
    return selected, bound, undef


def eval_expr(expr: Expr, env: Mapping[str, int], vars_width: Mapping[str, tuple[int, bool]]) -> Value:
    if expr.op == "bool":
        return Value(1 if expr.args[0] else 0, BOOL, False)
    if expr.op == "int":
        ty = expr.ty if expr.ty.width else bv(max(_min_width(int(expr.args[0])), 1), int(expr.args[0]) < 0)
        return Value(_fit(int(expr.args[0]), ty), ty, False)
    if expr.op == "field":
        path = str(expr.args[0])
        width, signed = vars_width[path]
        bits = env[path] & ((1 << width) - 1)
        return _resize(Value(bits, bv(width, signed)), expr.ty)
    if expr.op == "size":
        path = str(expr.args[1])
        width, signed = vars_width[path]
        bits = env[path] & ((1 << width) - 1)
        return _resize(Value(bits, bv(width, signed)), expr.ty)
    if expr.op == "not":
        inner = eval_expr(expr.args[0], env, vars_width)
        return Value(0 if inner.bits else 1, BOOL, inner.undef)
    if expr.op == "inv":
        inner = eval_expr(expr.args[0], env, vars_width)
        mask = (1 << inner.ty.width) - 1
        return Value((~inner.bits) & mask, inner.ty, inner.undef)
    if expr.op == "u+":
        return eval_expr(expr.args[0], env, vars_width)
    if expr.op == "u-":
        inner = eval_expr(expr.args[0], env, vars_width)
        mask = (1 << inner.ty.width) - 1
        return Value((-inner.bits) & mask, inner.ty, inner.undef)
    if expr.op == "ite":
        cond = eval_expr(expr.args[0], env, vars_width)
        then = eval_expr(expr.args[1], env, vars_width)
        els = eval_expr(expr.args[2], env, vars_width)
        chosen = then if cond.bits else els
        return Value(chosen.bits, expr.ty, cond.undef or chosen.undef)
    if expr.op == "inside":
        value = eval_expr(expr.args[0], env, vars_width)
        undef = value.undef
        hit = False
        for item in expr.args[1:]:
            other = eval_expr(item, env, vars_width)
            undef = undef or other.undef
            width = max(value.ty.width or 1, other.ty.width or 1, 1)
            common = bv(width, False)
            left = _resize(value, common)
            right = _resize(other, common)
            if left.bits == right.bits:
                hit = True
        return Value(1 if hit else 0, BOOL, undef)
    if expr.op == "dist":
        value = eval_expr(expr.args[0], env, vars_width)
        undef = value.undef
        hit = False
        for item in expr.args[1]:
            low = eval_expr(item.low, env, vars_width)
            high = eval_expr(item.high, env, vars_width) if item.high is not None else None
            weight = eval_expr(item.weight, env, vars_width)
            undef = undef or low.undef or weight.undef or (high.undef if high is not None else False)
            weight_value = _integer_value(weight)
            if weight_value < 0:
                undef = True
                continue
            if weight_value == 0:
                continue
            if high is None:
                hit = hit or _equal_values(value, low)
            else:
                hit = hit or _in_range(value, low, high)
        return Value(1 if hit else 0, BOOL, undef)
    if expr.op == "unique":
        values = [eval_expr(item, env, vars_width) for item in expr.args]
        undef = any(value.undef for value in values)
        distinct = all(
            not _equal_values(values[left], values[right])
            for left in range(len(values))
            for right in range(left + 1, len(values))
        )
        return Value(1 if distinct else 0, BOOL, undef)
    if expr.op == "land":
        left = eval_expr(expr.args[0], env, vars_width)
        right = eval_expr(expr.args[1], env, vars_width)
        return Value(1 if left.bits and right.bits else 0, BOOL, left.undef or right.undef)
    if expr.op == "lor":
        left = eval_expr(expr.args[0], env, vars_width)
        right = eval_expr(expr.args[1], env, vars_width)
        return Value(1 if left.bits or right.bits else 0, BOOL, left.undef or right.undef)
    left = eval_expr(expr.args[0], env, vars_width)
    right = eval_expr(expr.args[1], env, vars_width)
    left = _resize(left, expr.args[0].ty if expr.args[0].ty.width else left.ty)
    right = _resize(right, expr.args[1].ty if expr.args[1].ty.width else right.ty)
    width = max(left.ty.width, right.ty.width, expr.ty.width or 0, 1)
    signed = expr.ty.signed if expr.ty.is_bv else (left.ty.signed or right.ty.signed)
    common = bv(width, signed)
    left = _resize(left, common)
    right = _resize(right, common)
    undef = left.undef or right.undef
    if expr.op == "mod" and right.bits == 0:
        return Value(0, common if expr.ty.is_bv else expr.ty, True)
    bits = _binop(expr.op, left, right, common)
    if expr.ty.is_bool:
        return Value(bits, BOOL, undef)
    return Value(bits & ((1 << expr.ty.width) - 1) if expr.ty.width else bits, expr.ty if expr.ty.width else common, undef)


def _binop(op: str, left: Value, right: Value, ty: IRType) -> int:
    mask = (1 << ty.width) - 1
    lbits, rbits = left.bits & mask, right.bits & mask
    ls, rs = _signed(lbits, ty), _signed(rbits, ty)
    if op == "add":
        return (lbits + rbits) & mask
    if op == "sub":
        return (lbits - rbits) & mask
    if op == "mul":
        return (lbits * rbits) & mask
    if op == "mod":
        if rbits == 0:
            return 0
        if ty.signed:
            return _fit(ls - (ls / rs).__trunc__() * rs, ty) if rs else 0
        return lbits % rbits
    if op == "and":
        return lbits & rbits
    if op == "or":
        return lbits | rbits
    if op == "xor":
        return lbits ^ rbits
    if op == "shl":
        amount = rbits
        if amount >= ty.width:
            return 0
        return (lbits << amount) & mask
    if op == "shr":
        amount = rbits
        if amount >= ty.width:
            return mask if ty.signed and (lbits >> (ty.width - 1)) else 0
        if ty.signed:
            return _fit(ls >> amount, ty)
        return lbits >> amount
    if op == "eq":
        return int(lbits == rbits)
    if op == "ne":
        return int(lbits != rbits)
    cmp_signed = left.ty.signed and right.ty.signed
    lv = ls if cmp_signed else lbits
    rv = rs if cmp_signed else rbits
    if op == "lt":
        return int(lv < rv)
    if op == "le":
        return int(lv <= rv)
    if op == "gt":
        return int(lv > rv)
    if op == "ge":
        return int(lv >= rv)
    raise ValueError(f"unknown operator {op}")


def _resize(value: Value, ty: IRType) -> Value:
    if ty.is_bool:
        return Value(0 if value.undef else (1 if value.bits else 0), BOOL, value.undef)
    if not ty.width:
        return value
    if value.ty.width == ty.width and value.ty.signed == ty.signed:
        return Value(value.bits & ((1 << ty.width) - 1), ty, value.undef)
    if value.ty.is_bool:
        bits = 1 if value.bits else 0
        return Value(bits, ty, value.undef)
    src_width = value.ty.width or max(_min_width(_signed(value.bits, value.ty) if value.ty.signed else value.bits), 1)
    bits = value.bits & ((1 << src_width) - 1)
    if ty.width <= src_width:
        return Value(bits & ((1 << ty.width) - 1), ty, value.undef)
    if value.ty.signed and (bits >> (src_width - 1)):
        ext = ((1 << ty.width) - 1) ^ ((1 << src_width) - 1)
        bits = bits | ext
    return Value(bits, ty, value.undef)


def _integer_value(value: Value) -> int:
    return _signed(value.bits, value.ty) if value.ty.signed else value.bits


def _equal_values(left: Value, right: Value) -> bool:
    width = max(left.ty.width, right.ty.width, 1)
    common = bv(width, left.ty.signed and right.ty.signed)
    return _resize(left, common).bits == _resize(right, common).bits


def _in_range(value: Value, low: Value, high: Value) -> bool:
    width = max(value.ty.width, low.ty.width, high.ty.width, 1)
    signed = value.ty.signed and low.ty.signed and high.ty.signed
    common = bv(width, signed)
    current = _resize(value, common)
    lo = _resize(low, common)
    hi = _resize(high, common)
    if signed:
        return _signed(lo.bits, common) <= _signed(current.bits, common) <= _signed(hi.bits, common)
    return lo.bits <= current.bits <= hi.bits


def _range_size(low: Value, high: Value) -> int:
    width = max(low.ty.width, high.ty.width, 1)
    signed = low.ty.signed and high.ty.signed
    common = bv(width, signed)
    lo = _resize(low, common)
    hi = _resize(high, common)
    if signed:
        return _signed(hi.bits, common) - _signed(lo.bits, common) + 1
    return hi.bits - lo.bits + 1


def _signed(bits: int, ty: IRType) -> int:
    if not ty.signed or not ty.width:
        return bits
    sign = 1 << (ty.width - 1)
    return bits - (1 << ty.width) if bits & sign else bits


def _fit(value: int, ty: IRType) -> int:
    mask = (1 << ty.width) - 1
    return value & mask


def _min_width(value: int) -> int:
    if value >= 0:
        return max(value.bit_length(), 1)
    return max(value.bit_length() + 1, 2)
