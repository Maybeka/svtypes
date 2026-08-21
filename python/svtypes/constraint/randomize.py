"""Python randomize() / randomize_with() / layered_randomize() implementation."""

from __future__ import annotations

from types import FunctionType
from typing import Any, Callable

from ..errors import ConstraintBackendError, ConstraintError, DeclarationError
from ..logic import Logic
from .analyze import compile_block
from .eval import eval_bool
from .frontend import parse_constraint_function
from .ir import ConstraintIR, VarDecl
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

    assignments: dict[str, Any] = {}
    for path, desc in unconstrained:
        value = sample_unconstrained(desc, stream)
        assignments[path] = value
        env[path] = leaf_unsigned(desc, value)

    chosen: dict[str, Any] | None = None
    if not constrained:
        chosen = {}
    else:
        for _ in range(32):
            candidate: dict[str, Any] = {}
            local = dict(env)
            for path, desc in constrained:
                value = sample_unconstrained(desc, stream)
                candidate[path] = value
                local[path] = leaf_unsigned(desc, value)
            if all(eval_bool(pred, local, widths) for ir in enabled for pred in ir.predicates):
                chosen = candidate
                break
        if chosen is None:
            from .backend.smt import minimum_model

            model = minimum_model(
                enabled,
                random_paths=[path for path, _ in constrained],
                state=state_values,
                var_index=var_index,
            )
            if model is None:
                restore_leaves(obj, snapshot)
                obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "unsat")
                return False
            chosen = {}
            for path, desc in constrained:
                chosen[path] = _value_from_bits(desc, model[path])

    try:
        for path, value in assignments.items():
            resolve_attr(obj, path).value = value
        for path, value in (chosen or {}).items():
            resolve_attr(obj, path).value = value
    except Exception:
        restore_leaves(obj, snapshot)
        raise
    obj._SvObject__svtypes_randomize_status = RandomizeStatus(True, "sat")
    return True


def _value_from_bits(desc: Any, bits: int) -> Any:
    from ..logic import LogicValue

    if isinstance(desc, Logic):
        return LogicValue(desc.width, bits & ((1 << desc.width) - 1), 0, 0)
    return desc._normalize(bits)
