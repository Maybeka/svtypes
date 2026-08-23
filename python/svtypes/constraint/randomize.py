"""Python randomize() / randomize_with() / layered_randomize() implementation."""

from __future__ import annotations

from types import FunctionType
from typing import Any, Callable

from ..errors import ConstraintBackendError, ConstraintError, DeclarationError
from ..logic import Logic
from .analyze import compile_block
from .eval import eval_bool, eval_dist_weight, eval_expr
from .frontend import parse_constraint_function
from .ir import BOOL, ConstraintIR, Expr, VarDecl
from .leaves import (
    iter_class_leaves,
    leaf_has_xz,
    leaf_unsigned,
    resolve_attr,
    restore_leaves,
    snapshot_leaves,
)
from .modes import path_prefixes
from .sample import (
    BitStream,
    LayeredRandomizeStatus,
    RandomizeStatus,
    current_context,
    sample_unconstrained,
)

_inline_cache: dict[tuple[int, type], ConstraintIR] = {}


def _require_actual(obj: Any) -> type:
    cls = obj if isinstance(obj, type) else obj.__class__
    if getattr(cls, "_SvObject__svtypes_is_template", False):
        raise DeclarationError(
            f"{cls.__name__} is a parameterized template; bind every Parameter with specialize() first"
        )
    return cls


def randomize_object(obj: Any, extra: ConstraintIR | None = None) -> bool:
    cls = _require_actual(obj)
    ctx = current_context()
    _, seed = ctx.consume_call()
    stream = BitStream(seed)
    obj.pre_randomize()
    try:
        ok = _solve(obj, cls, stream, extra)
    except ConstraintBackendError:
        raise
    except ConstraintError:
        raise
    if ok:
        obj.post_randomize()
    return ok


def randomize_object_with(obj: Any, fn: Callable[..., Any]) -> bool:
    if not isinstance(fn, FunctionType):
        raise ConstraintError("randomize_with() requires a Python function")
    cls = _require_actual(obj)
    key = (id(fn), cls)
    ir = _inline_cache.get(key)
    if ir is None:
        decl = parse_constraint_function(fn)
        ir = compile_block(cls, decl)
        _inline_cache[key] = ir
    return randomize_object(obj, extra=ir)


def layered_randomize_object(obj: Any) -> bool:
    cls = _require_actual(obj)
    batches = getattr(cls, "_SvObject__svtypes_layer_batches", ())
    targets = getattr(cls, "_SvObject__svtypes_sv_rand_targets", ())
    constraints = tuple(getattr(cls, "_SvObject__svtypes_constraint_irs", {}))
    rand_snap, cstr_snap = _snapshot_modes(obj)
    try:
        obj._SvObject__svtypes_rand_modes = {name: 0 for name in targets}
        obj._SvObject__svtypes_constraint_modes = {name: 0 for name in constraints}
        last_status = None
        for batch in batches:
            _set_batch_modes(obj, cls, batch.variables, batch.constraints, 1)
            ok = randomize_object(obj)
            last_status = obj._SvObject__svtypes_randomize_status
            if not ok:
                obj._SvObject__svtypes_layered_randomize_status = LayeredRandomizeStatus(
                    False,
                    last_status.reason if last_status is not None else "unsat",
                    batch.priority,
                    tuple(batch.aliases),
                    last_status,
                )
                return False
            _set_batch_modes(obj, cls, batch.variables, batch.constraints, 0)
        obj._SvObject__svtypes_layered_randomize_status = LayeredRandomizeStatus(
            True,
            last_status.reason if last_status is not None else "sat",
            None,
            (),
            last_status,
        )
        return True
    finally:
        _restore_modes(obj, rand_snap, cstr_snap)


def _snapshot_modes(obj: Any) -> tuple[dict[str, int], dict[str, int]]:
    return (
        dict(obj._SvObject__svtypes_rand_modes),
        dict(obj._SvObject__svtypes_constraint_modes),
    )


def _restore_modes(obj: Any, rand_modes: dict[str, int], constraint_modes: dict[str, int]) -> None:
    obj._SvObject__svtypes_rand_modes = dict(rand_modes)
    obj._SvObject__svtypes_constraint_modes = dict(constraint_modes)


def _set_batch_modes(
    obj: Any,
    cls: type,
    variables: tuple[str, ...],
    constraints: tuple[str, ...],
    on: int,
) -> None:
    from .layer import expand_declared_paths

    rand_modes = obj._SvObject__svtypes_rand_modes
    constraint_modes = obj._SvObject__svtypes_constraint_modes
    for path in expand_declared_paths(cls, variables):
        rand_modes[path] = on
    for name in constraints:
        constraint_modes[name] = on


def _enabled_irs(obj: Any, cls: type, extra: ConstraintIR | None) -> list[ConstraintIR]:
    decls: dict[str, ConstraintIR] = getattr(cls, "_SvObject__svtypes_constraint_irs", {})
    modes: dict[str, int] = obj._SvObject__svtypes_constraint_modes
    enabled = [ir for name, ir in decls.items() if modes.get(name, 1) == 1]
    if extra is not None:
        enabled.append(extra)
    return enabled


def _effective_rand_mode(obj: Any, path: str) -> int:
    modes: dict[str, int] = obj._SvObject__svtypes_rand_modes
    for prefix in path_prefixes(path):
        if modes.get(prefix, 1) == 0:
            return 0
    return 1


def _solve(obj: Any, cls: type, stream: BitStream, extra: ConstraintIR | None) -> bool:
    enabled = _enabled_irs(obj, cls, extra)
    mentioned: set[str] = set()
    var_index: dict[str, VarDecl] = {}
    for ir in enabled:
        for var in ir.vars:
            mentioned.add(var.path)
            var_index[var.path] = var

    leaves = list(iter_class_leaves(cls))
    unconstrained: list[tuple[str, Any]] = []
    constrained: list[tuple[str, Any]] = []
    state_paths: list[str] = []
    write_paths: list[str] = []
    for path, desc, declared_rand in leaves:
        mode = _effective_rand_mode(obj, path)
        if declared_rand and mode == 1:
            write_paths.append(path)
            if path in mentioned:
                constrained.append((path, desc))
            else:
                unconstrained.append((path, desc))
        elif path in mentioned:
            state_paths.append(path)

    for path in state_paths:
        target = resolve_attr(obj, path)
        if isinstance(target, Logic) and leaf_has_xz(target, target.value):
            obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "state_xz", path)
            return False

    snapshot = snapshot_leaves(obj, write_paths)
    state_values = {}
    for path in state_paths:
        target = resolve_attr(obj, path)
        state_values[path] = leaf_unsigned(target, target.value)

    env: dict[str, int] = dict(state_values)
    widths = {path: (desc.width, bool(desc.signed)) for path, desc, _ in leaves}
    for var in var_index.values():
        widths[var.path] = (var.width, var.signed)

    signature = tuple(sorted(ir.digest() for ir in enabled))
    randc_paths = [
        path for path, desc, declared_rand in leaves
        if declared_rand and bool(getattr(desc, "randc", False)) and _effective_rand_mode(obj, path) == 1
    ]
    randc_seen = _prepare_randc_seen(obj, randc_paths, leaves, signature)

    assignments: dict[str, Any] = {}
    for path, desc in unconstrained:
        value = _sample_randc(desc, stream, randc_seen[path]) if path in randc_seen else sample_unconstrained(desc, stream)
        assignments[path] = value
        env[path] = leaf_unsigned(desc, value)

    chosen: dict[str, Any] | None = None
    if not constrained:
        if not all(eval_bool(pred, env, widths) for ir in enabled for pred in ir.predicates):
            restore_leaves(obj, snapshot)
            obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "unsat")
            return False
        chosen = {}
    else:
        for _ in range(32):
            candidate: dict[str, Any] = {}
            local = dict(env)
            for path, desc in constrained:
                if path not in randc_seen:
                    continue
                value = _sample_randc(desc, stream, randc_seen[path])
                candidate[path] = value
                local[path] = leaf_unsigned(desc, value)
            for path, desc in constrained:
                if path in randc_seen:
                    continue
                value = sample_unconstrained(desc, stream)
                candidate[path] = value
                local[path] = leaf_unsigned(desc, value)
            if all(eval_bool(pred, local, widths) for ir in enabled for pred in ir.predicates) and _accept_distributions(
                _active_dist_exprs(enabled, local, widths), local, widths, stream
            ):
                chosen = candidate
                break
        if chosen is None:
            from .backend.model import SolveRequest
            from .backend.smt import solve

            request = SolveRequest(
                irs=tuple(enabled),
                random_paths=tuple(path for path, _ in constrained),
                state=state_values,
                var_index=var_index,
                assumptions=_randc_remaining_assumptions(var_index, randc_seen),
            )
            result = _solve_sparse_singleton_dist(
                request, enabled, env, widths, stream, solve
            ) or solve(request)
            if not result.is_sat and randc_seen:
                # The cycle has no remaining legal value.  Start a fresh
                # cycle only after proving the full constraint set is SAT.
                reset_request = SolveRequest(
                    irs=tuple(enabled),
                    random_paths=tuple(path for path, _ in constrained),
                    state=state_values,
                    var_index=var_index,
                )
                reset_result = _solve_sparse_singleton_dist(
                    reset_request, enabled, env, widths, stream, solve
                ) or solve(reset_request)
                if reset_result.is_sat:
                    for seen in randc_seen.values():
                        seen.clear()
                    result = reset_result
            if not result.is_sat:
                restore_leaves(obj, snapshot)
                obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "unsat")
                return False
            chosen = {}
            for path, desc in constrained:
                chosen[path] = _value_from_bits(desc, result.assignments[path])

    try:
        for path, value in assignments.items():
            resolve_attr(obj, path).value = value
        for path, value in (chosen or {}).items():
            resolve_attr(obj, path).value = value
    except Exception:
        restore_leaves(obj, snapshot)
        raise
    _commit_randc_state(obj, randc_seen, signature)
    obj._SvObject__svtypes_randomize_status = RandomizeStatus(True, "sat")
    return True


def _active_dist_exprs(
    irs: list[ConstraintIR],
    env: dict[str, int],
    widths: dict[str, tuple[int, bool]],
) -> list[Any]:
    """Find direct dist statements enabled by the current structured branch."""

    out: list[Any] = []

    def visit(statements: list[Any]) -> None:
        for stmt in statements:
            if stmt.kind == "pred" and stmt.expr is not None and stmt.expr.op == "dist":
                out.append(stmt.expr)
            elif stmt.kind == "if" and stmt.cond is not None:
                visit(stmt.then_body if eval_bool(stmt.cond, env, widths) else stmt.else_body)

    for ir in irs:
        visit(ir.statements)
    return out


def _accept_distributions(
    exprs: list[Any],
    env: dict[str, int],
    widths: dict[str, tuple[int, bool]],
    stream: BitStream,
) -> bool:
    """Apply `dist` weights with exact, bounded rejection sampling."""

    numerator = 1
    denominator = 1
    for expr in exprs:
        selected, bound, undef = eval_dist_weight(expr, env, widths)
        if undef or selected <= 0 or bound <= 0:
            return False
        numerator *= selected.numerator * bound.denominator
        denominator *= selected.denominator * bound.numerator
    if numerator >= denominator:
        return True
    return stream.draw_bits(64) * denominator < numerator * (1 << 64)


def _solve_sparse_singleton_dist(
    request: Any,
    irs: list[ConstraintIR],
    env: dict[str, int],
    widths: dict[str, tuple[int, bool]],
    stream: BitStream,
    solve: Any,
) -> Any | None:
    """Choose a SAT singleton `dist` support value by its exact weight.

    Uniform candidate sampling almost never reaches a tiny support subset of a
    wide field.  For a direct, state-resolvable single-value distribution we
    can instead prove each support value SAT, then make one weighted choice.
    Other dist shapes continue to use the general backend fallback.
    """

    # A branch that depends on an unsolved leaf cannot be selected before the
    # backend solves it.  The exact sparse path therefore handles only direct
    # top-level dist statements; conditional dist continues through the
    # general fallback.
    dist_exprs = [
        stmt.expr
        for ir in irs
        for stmt in ir.statements
        if stmt.kind == "pred" and stmt.expr is not None and stmt.expr.op == "dist"
    ]
    if len(dist_exprs) != 1:
        return None
    dist_expr = dist_exprs[0]
    options: dict[int, tuple[Expr, int]] = {}
    for item in dist_expr.args[1]:
        if item.high is not None or _expr_fields(item.low) & set(request.random_paths):
            return None
        if _expr_fields(item.weight) & set(request.random_paths):
            return None
        low = eval_expr(item.low, env, widths)
        weight = eval_expr(item.weight, env, widths)
        if low.undef or weight.undef:
            return None
        raw_weight = _value_as_int(weight.bits, weight.ty.width, weight.ty.signed)
        if raw_weight < 0:
            return None
        key = low.bits
        prior = options.get(key)
        options[key] = (item.low, raw_weight + (prior[1] if prior else 0))
    models: list[tuple[int, Any]] = []
    for low, weight in options.values():
        if weight == 0:
            continue
        assumption = Expr("eq", (dist_expr.args[0], low), BOOL, dist_expr.loc)
        result = solve(type(request)(
            irs=request.irs,
            random_paths=request.random_paths,
            state=request.state,
            var_index=request.var_index,
            assumptions=(*request.assumptions, assumption),
        ))
        if result.is_sat:
            models.append((weight, result))
    if not models:
        return None
    total = sum(weight for weight, _ in models)
    draw_limit = ((1 << 64) // total) * total
    while True:
        draw = stream.draw_bits(64)
        if draw < draw_limit:
            break
    pick = draw % total
    for weight, result in models:
        if pick < weight:
            return result
        pick -= weight
    raise ConstraintBackendError("weighted singleton dist selection lost its chosen model")


def _expr_fields(expr: Expr) -> set[str]:
    if expr.op == "field":
        return {str(expr.args[0])}
    fields: set[str] = set()
    for arg in expr.args:
        if isinstance(arg, Expr):
            fields.update(_expr_fields(arg))
    return fields


def _value_as_int(bits: int, width: int, signed: bool) -> int:
    if not signed or width == 0:
        return bits
    sign = 1 << (width - 1)
    return bits - (1 << width) if bits & sign else bits


def _prepare_randc_seen(
    obj: Any,
    paths: list[str],
    leaves: list[tuple[str, Any, bool]],
    signature: tuple[str, ...],
) -> dict[str, set[int]]:
    descriptors = {path: desc for path, desc, _ in leaves}
    state = obj._SvObject__svtypes_randc_state
    working: dict[str, set[int]] = {}
    for path in paths:
        old = state.get(path)
        seen = set(old[1]) if old is not None and old[0] == signature else set()
        desc = descriptors[path]
        if _randc_domain_exhausted(desc, seen):
            seen.clear()
        working[path] = seen
    return working


def _randc_domain_exhausted(desc: Any, seen: set[int]) -> bool:
    from ..enum import Enum

    if isinstance(desc, Enum):
        return len(seen) >= len(desc.__class__._enum_items)
    return len(seen) >= (1 << desc.width)


def _sample_randc(desc: Any, stream: BitStream, seen: set[int]) -> Any:
    from ..enum import Enum

    if isinstance(desc, Enum):
        members = [int(item) for item in desc.__class__._enum_items if int(item) not in seen]
        if not members:
            raise ConstraintBackendError("randc cycle has no remaining enum values")
        return desc._normalize(stream.draw_enum(members))
    # A draw/retry avoids materializing the potentially enormous packed domain.
    while True:
        value = sample_unconstrained(desc, stream)
        if leaf_unsigned(desc, value) not in seen:
            return value


def _randc_remaining_assumptions(
    var_index: dict[str, VarDecl],
    seen_by_path: dict[str, set[int]],
) -> tuple[Expr, ...]:
    from .ir import bv, c_field, c_int

    assumptions: list[Expr] = []
    for path, seen in seen_by_path.items():
        decl = var_index.get(path)
        if decl is None:
            continue
        field = c_field(path, bv(decl.width, decl.signed))
        for value in seen:
            assumptions.append(Expr("ne", (field, c_int(value, hint=None)), BOOL))
    return tuple(assumptions)


def _commit_randc_state(
    obj: Any,
    seen_by_path: dict[str, set[int]],
    signature: tuple[str, ...],
) -> None:
    state = obj._SvObject__svtypes_randc_state
    for path, seen in seen_by_path.items():
        target = resolve_attr(obj, path)
        updated = set(seen)
        updated.add(leaf_unsigned(target, target.value))
        state[path] = (signature, updated)


def _value_from_bits(desc: Any, bits: int) -> Any:
    from ..logic import LogicValue

    if isinstance(desc, Logic):
        return LogicValue(desc.width, bits & ((1 << desc.width) - 1), 0, 0)
    return desc._normalize(bits)
