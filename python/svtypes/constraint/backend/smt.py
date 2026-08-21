"""SMT-LIB bit-vector backend (Z3)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...errors import ConstraintBackendError
from ..ir import ConstraintIR, Expr, VarDecl


def _z3():
    try:
        import z3
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ConstraintBackendError(
            "SMT randomize requires the optional z3-solver package"
        ) from exc
    return z3


def minimum_model(
    irs: Sequence[ConstraintIR],
    *,
    random_paths: Sequence[str],
    state: Mapping[str, int],
    var_index: Mapping[str, VarDecl],
) -> dict[str, int] | None:
    z3 = _z3()
    solver = z3.Solver()
    terms: dict[str, Any] = {}
    widths: dict[str, int] = {}
    for path, decl in var_index.items():
        terms[path] = z3.BitVec(path, decl.width)
        widths[path] = decl.width
        if decl.enum_name and decl.descriptor is not None:
            members = [int(item) for item in decl.descriptor.__class__._enum_items]
            solver.add(z3.Or([terms[path] == member for member in members]))
    for path, bits in state.items():
        if path in terms:
            solver.add(terms[path] == bits)
    for ir in irs:
        for pred in ir.predicates:
            value, undef = _encode(z3, pred, terms, widths)
            solver.add(value)
            solver.add(z3.Not(undef))
    constrained = [path for path in random_paths if path in terms]
    if solver.check() != z3.sat:
        return None
    if not constrained:
        model = solver.model()
        return {path: _as_long(model, terms[path]) for path in random_paths if path in terms}
    concat = terms[constrained[0]]
    for path in constrained[1:]:
        concat = z3.Concat(concat, terms[path])
    width = concat.size()
    for bit_index in range(width - 1, -1, -1):
        bit = z3.Extract(bit_index, bit_index, concat)
        solver.push()
        solver.add(bit == 0)
        if solver.check() == z3.sat:
            solver.pop()
            solver.add(bit == 0)
        else:
            solver.pop()
            solver.add(bit == 1)
    if solver.check() != z3.sat:
        raise ConstraintBackendError("minimum model search lost a previously SAT assignment")
    model = solver.model()
    return {path: _as_long(model, terms[path]) for path in random_paths if path in terms}


def _as_long(model: Any, term: Any) -> int:
    return int(model.eval(term, model_completion=True).as_long())


def _encode(z3: Any, expr: Expr, terms: dict[str, Any], widths: dict[str, int]) -> tuple[Any, Any]:
    if expr.op == "bool":
        return (z3.BoolVal(bool(expr.args[0])), z3.BoolVal(False))
    if expr.op == "int":
        width = expr.ty.width or max(_min_width(int(expr.args[0])), 1)
        return (z3.BitVecVal(int(expr.args[0]), width), z3.BoolVal(False))
    if expr.op == "field":
        path = str(expr.args[0])
        term = terms[path]
        return (_cast_bv(z3, term, widths[path], expr.ty.width or widths[path], expr.ty.signed), z3.BoolVal(False))
    if expr.op == "not":
        value, undef = _encode(z3, expr.args[0], terms, widths)
        return (z3.Not(value), undef)
    if expr.op == "inv":
        value, undef = _encode(z3, expr.args[0], terms, widths)
        return (~value, undef)
    if expr.op == "u+":
        return _encode(z3, expr.args[0], terms, widths)
    if expr.op == "u-":
        value, undef = _encode(z3, expr.args[0], terms, widths)
        return (-value, undef)
    if expr.op == "ite":
        cond, cu = _encode(z3, expr.args[0], terms, widths)
        then, tu = _encode(z3, expr.args[1], terms, widths)
        els, eu = _encode(z3, expr.args[2], terms, widths)
        return (z3.If(cond, then, els), z3.Or(cu, tu, eu))
    if expr.op == "inside":
        left, lu = _encode(z3, expr.args[0], terms, widths)
        pieces = []
        undefs = [lu]
        for item in expr.args[1:]:
            right, ru = _encode(z3, item, terms, widths)
            undefs.append(ru)
            width = max(_bv_size(left), _bv_size(right))
            pieces.append(
                _cast_bv(z3, left, _bv_size(left), width, False)
                == _cast_bv(z3, right, _bv_size(right), width, False)
            )
        return (z3.Or(pieces) if pieces else z3.BoolVal(False), z3.Or(undefs))
    if expr.op == "land":
        left, lu = _encode(z3, expr.args[0], terms, widths)
        right, ru = _encode(z3, expr.args[1], terms, widths)
        return (z3.And(left, right), z3.Or(lu, ru))
    if expr.op == "lor":
        left, lu = _encode(z3, expr.args[0], terms, widths)
        right, ru = _encode(z3, expr.args[1], terms, widths)
        return (z3.Or(left, right), z3.Or(lu, ru))
    left, lu = _encode(z3, expr.args[0], terms, widths)
    right, ru = _encode(z3, expr.args[1], terms, widths)
    width = expr.ty.width or max(getattr(left, "size", lambda: 1)(), getattr(right, "size", lambda: 1)())
    if expr.ty.is_bool:
        width = max(_bv_size(left), _bv_size(right))
    signed = expr.ty.signed if not expr.ty.is_bool else False
    left = _cast_bv(z3, left, _bv_size(left), width, signed)
    right = _cast_bv(z3, right, _bv_size(right), width, signed)
    undef = z3.Or(lu, ru)
    if expr.op == "add":
        return (left + right, undef)
    if expr.op == "sub":
        return (left - right, undef)
    if expr.op == "mul":
        return (left * right, undef)
    if expr.op == "and":
        return (left & right, undef)
    if expr.op == "or":
        return (left | right, undef)
    if expr.op == "xor":
        return (left ^ right, undef)
    if expr.op == "mod":
        zero = left == 0  # placeholder to get sort
        zero = right == z3.BitVecVal(0, width)
        div = z3.If(zero, z3.BitVecVal(1, width), right)
        result = z3.If(zero, z3.BitVecVal(0, width), z3.SRem(left, div) if signed else z3.URem(left, div))
        return (result, z3.Or(undef, zero))
    if expr.op == "shl":
        return (left << right, undef)
    if expr.op == "shr":
        return ((left >> right) if signed else z3.LShR(left, right), undef)
    cmp = {
        "eq": left == right,
        "ne": left != right,
        "lt": left < right if signed else z3.ULT(left, right),
        "le": left <= right if signed else z3.ULE(left, right),
        "gt": left > right if signed else z3.UGT(left, right),
        "ge": left >= right if signed else z3.UGE(left, right),
    }[expr.op]
    if expr.op in {"lt", "le", "gt", "ge"} and not (expr.args[0].ty.signed and expr.args[1].ty.signed):
        cmp = {
            "lt": z3.ULT(left, right),
            "le": z3.ULE(left, right),
            "gt": z3.UGT(left, right),
            "ge": z3.UGE(left, right),
        }[expr.op]
    return (cmp, undef)


def _bv_size(term: Any) -> int:
    return int(term.size())


def _cast_bv(z3: Any, term: Any, src_width: int, dst_width: int, signed: bool) -> Any:
    if src_width == dst_width:
        return term
    if dst_width < src_width:
        return z3.Extract(dst_width - 1, 0, term)
    if signed:
        return z3.SignExt(dst_width - src_width, term)
    return z3.ZeroExt(dst_width - src_width, term)


def _min_width(value: int) -> int:
    if value >= 0:
        return max(value.bit_length(), 1)
    return max(value.bit_length() + 1, 2)
