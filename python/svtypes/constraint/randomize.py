"""Python randomize() / randomize_with() / layered_randomize() implementation."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from types import FunctionType
from typing import Any, Callable

from ..collection import AssocArray, DynArray, Queue
from ..errors import ConstraintBackendError, ConstraintError, DeclarationError
from ..logic import Logic
from .analyze import compile_block
from .eval import eval_bool, eval_dist_weight, eval_expr
from .frontend import parse_constraint_function
from .ir import BOOL, ConstraintIR, Expr, IRStmt, VarDecl, bv, c_int
from .leaves import (
    iter_class_leaves,
    iter_object_leaves,
    assoc_path,
    leaf_has_xz,
    leaf_unsigned,
    resolve_attr,
    restore_leaves,
    snapshot_leaves,
    split_path,
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


@dataclass(frozen=True)
class _GraphNode:
    path: str
    obj: Any
    cls: type


@dataclass
class _RandomGraph:
    root: Any
    nodes: list[_GraphNode]
    leaves: list[tuple[str, Any, bool]]
    irs: list[ConstraintIR]
    owners: dict[str, tuple[Any, str]]
    null_path: str | None = None

    def rand_mode(self, path: str) -> int:
        owner, local_path = self.owners[path]
        return _effective_rand_mode(owner, local_path)


def _with_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if path.startswith("@size:"):
        return f"@size:{prefix}.{path.removeprefix('@size:')}"
    return f"{prefix}.{path}"


def _prefix_ir(ir: ConstraintIR, prefix: str) -> ConstraintIR:
    """Rebase a class-local constraint IR onto its canonical graph path."""

    def expr(value: Expr | None) -> Expr | None:
        if value is None:
            return None
        args: list[Any] = []
        changed = False
        for arg in value.args:
            if isinstance(arg, Expr):
                new = expr(arg)
            elif isinstance(arg, tuple):
                new = tuple(
                    replace(
                        item,
                        low=expr(item.low),
                        high=expr(item.high),
                        weight=expr(item.weight),
                    )
                    if hasattr(item, "low") and hasattr(item, "weight") else item
                    for item in arg
                )
            else:
                new = arg
            args.append(new)
            changed = changed or new is not arg
        if value.op == "field":
            args[0] = _with_prefix(prefix, str(args[0]))
            changed = True
        elif value.op == "size":
            args[0] = _with_prefix(prefix, str(args[0]))
            args[1] = _with_prefix(prefix, str(args[1]))
            changed = True
        return Expr(value.op, tuple(args), value.ty, value.loc, value.undef, value.hint) if changed else value

    def statement(stmt: IRStmt) -> IRStmt:
        return IRStmt(
            kind=stmt.kind,
            expr=expr(stmt.expr),
            cond=expr(stmt.cond),
            then_body=[statement(child) for child in stmt.then_body],
            else_body=[statement(child) for child in stmt.else_body],
            var=stmt.var,
            start=expr(stmt.start),
            stop=expr(stmt.stop),
            array=_with_prefix(prefix, stmt.array) if stmt.array is not None else None,
            collection_kind=stmt.collection_kind,
            before=tuple(_with_prefix(prefix, path) for path in stmt.before) if stmt.before else None,
            after=tuple(_with_prefix(prefix, path) for path in stmt.after) if stmt.after else None,
        )

    return ConstraintIR(
        name=f"{prefix}.{ir.name}" if prefix else ir.name,
        predicates=[expr(predicate) for predicate in ir.predicates],  # type: ignore[list-item]
        soft_predicates=[expr(predicate) for predicate in ir.soft_predicates],  # type: ignore[list-item]
        solve_before=[
            (tuple(_with_prefix(prefix, item) for item in before), tuple(_with_prefix(prefix, item) for item in after))
            for before, after in ir.solve_before
        ],
        vars=[replace(var, path=_with_prefix(prefix, var.path)) for var in ir.vars],
        parameters=ir.parameters,
        statements=[statement(stmt) for stmt in ir.statements],
    )


def _remap_graph_ir(ir: ConstraintIR, aliases: dict[str, str]) -> ConstraintIR:
    """Rewrite a path through a shared-object alias to its canonical path."""

    def canonical(path: str) -> str:
        size_prefix = "@size:" if path.startswith("@size:") else ""
        bare = path.removeprefix(size_prefix)
        for alias in sorted(aliases, key=len, reverse=True):
            if bare == alias or bare.startswith(f"{alias}.") or bare.startswith(f"{alias}["):
                return size_prefix + aliases[alias] + bare[len(alias):]
        return path

    def rewrite(value: Expr | None) -> Expr | None:
        if value is None:
            return None
        args: list[Any] = []
        changed = False
        for arg in value.args:
            if isinstance(arg, Expr):
                new = rewrite(arg)
            elif isinstance(arg, tuple):
                new = tuple(
                    replace(item, low=rewrite(item.low), high=rewrite(item.high), weight=rewrite(item.weight))
                    if hasattr(item, "low") and hasattr(item, "weight") else item
                    for item in arg
                )
            else:
                new = arg
            args.append(new)
            changed = changed or new is not arg
        if value.op == "field":
            args[0] = canonical(str(args[0]))
            changed = changed or args[0] != value.args[0]
        elif value.op == "size":
            args[0], args[1] = canonical(str(args[0])), canonical(str(args[1]))
            changed = changed or tuple(args) != value.args
        return Expr(value.op, tuple(args), value.ty, value.loc, value.undef, value.hint) if changed else value

    def stmt(value: IRStmt) -> IRStmt:
        return IRStmt(value.kind, rewrite(value.expr), rewrite(value.cond), [stmt(x) for x in value.then_body], [stmt(x) for x in value.else_body], value.var, rewrite(value.start), rewrite(value.stop), canonical(value.array) if value.array else None, value.collection_kind, tuple(canonical(x) for x in value.before) if value.before else None, tuple(canonical(x) for x in value.after) if value.after else None)

    return ConstraintIR(
        ir.name,
        [rewrite(item) for item in ir.predicates],  # type: ignore[list-item]
        [replace(var, path=canonical(var.path)) for var in ir.vars],
        [rewrite(item) for item in ir.soft_predicates],  # type: ignore[list-item]
        [(tuple(canonical(x) for x in before), tuple(canonical(x) for x in after)) for before, after in ir.solve_before],
        ir.parameters,
        [stmt(item) for item in ir.statements],
    )


def _collect_random_graph(root: Any, root_cls: type, extra: ConstraintIR | None) -> _RandomGraph:
    """Collect allocated rand-object members without materializing null handles.

    A random class handle may occur directly on an object or as an existing
    element of any unpacked collection.  The traversal never allocates a
    handle: null entries, and descriptor templates used as Python's placeholder
    for newly resized collections, are deliberately skipped.
    """

    nodes: list[_GraphNode] = []
    identities: dict[int, str] = {}
    aliases: dict[str, str] = {}

    def visit(obj: Any, cls: type, path: str) -> None:
        existing = identities.get(id(obj))
        if existing is not None:
            aliases[path] = existing
            return
        identities[id(obj)] = path
        nodes.append(_GraphNode(path, obj, cls))
        for name, desc in getattr(cls, "_SvObject__svtypes_members", ()):
            member_path = _with_prefix(path, name)
            from ..object import ObjectDescriptor

            if isinstance(desc, ObjectDescriptor):
                if not desc.rand:
                    continue
                # Do not use getattr(): ObjectDescriptor.__get__ allocates a
                # child on demand, whereas SV randomize never does so.
                child = obj.__dict__.get(desc._cache_key)
                if child is not None:
                    visit(child, child.__class__, member_path)
                continue
            _visit_rand_container_handles(getattr(obj, name), desc, member_path, visit)

    visit(root, root_cls, "")
    leaves: list[tuple[str, Any, bool]] = []
    owners: dict[str, tuple[Any, str]] = {}
    irs: list[ConstraintIR] = []
    for node in nodes:
        for local_path, desc, declared_rand in iter_object_leaves(node.obj, node.cls):
            path = _with_prefix(node.path, local_path)
            leaves.append((path, desc, declared_rand))
            owners[path] = (node.obj, local_path)
        for ir in _enabled_irs(node.obj, node.cls, extra if node.path == "" else None):
            irs.append(_prefix_ir(ir, node.path))
    remapped = [_remap_graph_ir(ir, aliases) for ir in irs]
    null_path = None
    for ir in remapped:
        for var in ir.vars:
            if var.kind != "field":
                continue
            null_path = _null_handle_path(root, var.path)
            if null_path is not None:
                break
        if null_path is not None:
            break
    return _RandomGraph(
        root=root,
        nodes=nodes,
        leaves=leaves,
        irs=remapped,
        owners=owners,
        null_path=null_path,
    )


def _visit_rand_container_handles(
    value: Any,
    desc: Any,
    path: str,
    visit: Callable[[Any, type, str], None],
) -> None:
    """Visit allocated ``rand Object`` elements below one collection member."""

    from ..collection import Array, AssocArray, DynArray, Queue
    from ..object import ObjectDescriptor, SvObject

    if isinstance(desc, ObjectDescriptor):
        if desc.rand and isinstance(value, SvObject):
            visit(value, value.__class__, path)
        return
    if isinstance(desc, Array):
        for index, element in enumerate(value._elements):
            _visit_rand_container_handles(
                element, desc._elem_template, f"{path}[{index}]", visit
            )
        return
    if isinstance(desc, (DynArray, Queue)):
        for index, element in enumerate(value._elements):
            _visit_rand_container_handles(
                element, desc._elem_template, f"{path}[{index}]", visit
            )
        return
    if isinstance(desc, AssocArray):
        for key, element in value._elements.items():
            _visit_rand_container_handles(
                element, desc._val_template, assoc_path(path, key), visit
            )


def _null_handle_path(root: Any, path: str) -> str | None:
    """Return the first null handle crossed by *path*, without descriptor get."""

    from ..collection import Array, AssocArray, DynArray, Queue
    from ..object import ObjectDescriptor, SvObject

    current = root
    desc: Any | None = None
    traversed = ""
    # Structured foreach templates use symbolic ``[i]`` / ``[key]`` paths.
    # They are expanded against actual entries later, so no individual null
    # handle can be identified at this stage.
    try:
        tokens = split_path(path)
    except ValueError:
        return None
    for token in tokens:
        if isinstance(token, tuple) and token[0] == "assoc":
            if not isinstance(desc, AssocArray):
                return None
            traversed = assoc_path(traversed, token[1])
            desc = desc._val_template
            current = current._elements.get(token[1])
            if isinstance(desc, ObjectDescriptor) and current is None:
                return traversed
            continue
        if isinstance(token, int):
            if not isinstance(desc, (Array, DynArray, Queue)):
                return None
            traversed = f"{traversed}[{token}]"
            desc = desc._elem_template
            try:
                current = current._elements[token]
            except IndexError:
                return None
            if isinstance(desc, ObjectDescriptor) and current is None:
                return traversed
            continue
        if not isinstance(current, SvObject):
            return None
        desc = dict(current.__class__._SvObject__svtypes_members).get(token)
        if desc is None:
            return None
        traversed = f"{traversed}.{token}" if traversed else token
        if isinstance(desc, ObjectDescriptor):
            child = current.__dict__.get(desc._cache_key)
            if child is None:
                return traversed
            current = child
        else:
            current = getattr(current, token)
    return None


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
    graph = _collect_random_graph(obj, cls, extra)
    for node in graph.nodes:
        node.obj.pre_randomize()
    try:
        ok = _solve(obj, cls, stream, extra, graph=graph)
    except ConstraintBackendError:
        raise
    except ConstraintError:
        raise
    if ok:
        for node in reversed(graph.nodes):
            node.obj.post_randomize()
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
    dynamic_roots = _layered_dynamic_roots(cls, batches)
    dynamic_snap = _snapshot_dynamic_element_modes(obj, cls, dynamic_roots)
    active_snap = obj._SvObject__svtypes_layered_randomize_active
    priority_snap = obj._SvObject__svtypes_layered_randomize_priority
    try:
        obj._SvObject__svtypes_layered_randomize_active = True
        obj._SvObject__svtypes_rand_modes = {name: 0 for name in targets}
        # A dynamic collection's aggregate rand_mode controls its size.  A
        # rand_layer instead owns existing scalar elements individually, so an
        # outside aggregate setting must not leak into this custom algorithm.
        for root in dynamic_roots:
            obj._SvObject__svtypes_rand_modes.pop(root, None)
        for path in dynamic_snap:
            obj._SvObject__svtypes_rand_modes[path] = 0
        obj._SvObject__svtypes_constraint_modes = {name: 0 for name in constraints}
        last_status = None
        for batch in batches:
            obj._SvObject__svtypes_layered_randomize_priority = batch.priority
            _set_batch_modes(obj, cls, batch.variables, batch.constraints, 1, dynamic_snap)
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
            _set_batch_modes(obj, cls, batch.variables, batch.constraints, 0, dynamic_snap)
        obj._SvObject__svtypes_layered_randomize_status = LayeredRandomizeStatus(
            True,
            last_status.reason if last_status is not None else "sat",
            None,
            (),
            last_status,
        )
        return True
    finally:
        _restore_modes(obj, rand_snap, cstr_snap, dynamic_roots, dynamic_snap)
        obj._SvObject__svtypes_layered_randomize_active = active_snap
        obj._SvObject__svtypes_layered_randomize_priority = priority_snap


def _snapshot_modes(obj: Any) -> tuple[dict[str, int], dict[str, int]]:
    return (
        dict(obj._SvObject__svtypes_rand_modes),
        dict(obj._SvObject__svtypes_constraint_modes),
    )


def _restore_modes(
    obj: Any,
    rand_modes: dict[str, int],
    constraint_modes: dict[str, int],
    dynamic_roots: tuple[str, ...] = (),
    dynamic_snap: dict[str, int] | None = None,
) -> None:
    restored = dict(rand_modes)
    for root in dynamic_roots:
        # Aggregate modes are intentionally outside layered semantics.  The
        # current elements are restored below; elements created during the run
        # have no entry snapshot and therefore remain enabled.
        restored.pop(root, None)
    if dynamic_snap is not None:
        current = _current_dynamic_element_paths(obj, obj.__class__, dynamic_roots)
        for path in current:
            restored[path] = dynamic_snap.get(path, 1)
    obj._SvObject__svtypes_rand_modes = restored
    obj._SvObject__svtypes_constraint_modes = dict(constraint_modes)


def _set_batch_modes(
    obj: Any,
    cls: type,
    variables: tuple[str, ...],
    constraints: tuple[str, ...],
    on: int,
    dynamic_snap: dict[str, int] | None = None,
) -> None:
    from .layer import dynamic_layer_roots, expand_declared_paths

    rand_modes = obj._SvObject__svtypes_rand_modes
    constraint_modes = obj._SvObject__svtypes_constraint_modes
    for path in expand_declared_paths(cls, variables):
        rand_modes[path] = on
    if dynamic_snap is not None:
        for root in dynamic_layer_roots(cls, variables):
            for path in _current_dynamic_element_paths(obj, cls, (root,)):
                # An entry-disabled element never becomes enabled.  Elements
                # allocated by an earlier randomize call have no entry mode
                # and stay enabled through the remainder of this invocation.
                if path in dynamic_snap:
                    rand_modes[path] = dynamic_snap[path] if on else 0
                elif on:
                    rand_modes[path] = 1
    for name in constraints:
        constraint_modes[name] = on


def _layered_dynamic_roots(cls: type, batches: tuple[Any, ...]) -> tuple[str, ...]:
    from .layer import dynamic_layer_roots

    roots: list[str] = []
    for batch in batches:
        for root in dynamic_layer_roots(cls, batch.variables):
            if root not in roots:
                roots.append(root)
    return tuple(roots)


def _current_dynamic_element_paths(
    obj: Any, cls: type, roots: tuple[str, ...]
) -> set[str]:
    prefixes = tuple(f"{root}[" for root in roots)
    return {
        path
        for path, _desc, _declared in iter_object_leaves(obj, cls)
        if path.startswith(prefixes)
    }


def _snapshot_dynamic_element_modes(
    obj: Any, cls: type, roots: tuple[str, ...]
) -> dict[str, int]:
    modes = obj._SvObject__svtypes_rand_modes
    return {path: modes.get(path, 1) for path in _current_dynamic_element_paths(obj, cls, roots)}


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


def _solve(
    obj: Any,
    cls: type,
    stream: BitStream,
    extra: ConstraintIR | None,
    enabled_override: list[ConstraintIR] | None = None,
    graph: _RandomGraph | None = None,
) -> bool:
    if graph is not None and graph.null_path is not None:
        obj._SvObject__svtypes_randomize_status = RandomizeStatus(
            False, "null_handle", graph.null_path
        )
        return False
    enabled = (
        enabled_override
        if enabled_override is not None
        else (graph.irs if graph is not None else _enabled_irs(obj, cls, extra))
    )
    if enabled_override is None and any(
        var.kind == "size" for ir in enabled for var in ir.vars
    ):
        return _solve_dynamic_collections(obj, cls, stream, enabled)
    if enabled_override is None and any(
        stmt.kind == "assoc_foreach" for ir in enabled for stmt in ir.statements
    ):
        return _solve(obj, cls, stream, None, [_expand_dynamic_ir(obj, ir) for ir in enabled])
    mentioned: set[str] = set()
    var_index: dict[str, VarDecl] = {}
    for ir in enabled:
        for var in ir.vars:
            mentioned.add(var.path)
            var_index[var.path] = var

    leaves = list(graph.leaves) if graph is not None else list(iter_object_leaves(obj, cls))
    unconstrained: list[tuple[str, Any]] = []
    constrained: list[tuple[str, Any]] = []
    state_paths: list[str] = []
    write_paths: list[str] = []
    for path, desc, declared_rand in leaves:
        mode = graph.rand_mode(path) if graph is not None else _effective_rand_mode(obj, path)
        if declared_rand and mode == 1:
            write_paths.append(path)
            if path in mentioned:
                constrained.append((path, desc))
            else:
                unconstrained.append((path, desc))
        elif path in mentioned:
            state_paths.append(path)

    constrained = _order_constrained_paths(constrained, enabled)

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
        if declared_rand and bool(getattr(desc, "randc", False)) and (
            graph.rand_mode(path) if graph is not None else _effective_rand_mode(obj, path)
        ) == 1
    ]
    randc_seen = _prepare_randc_seen(obj, randc_paths, leaves, signature, graph)

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
        soft_constraints = _ordered_soft_constraints(enabled)
        best_soft_score: tuple[bool, ...] | None = None
        solve_order = _solve_before_order(enabled, constrained)
        exact_ordered = bool(solve_order) and not randc_seen and not _irs_contain_dist(enabled)
        for _ in range(0 if exact_ordered else 32):
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
            if not all(eval_bool(pred, local, widths) for ir in enabled for pred in ir.predicates):
                continue
            if not _accept_distributions(_active_dist_exprs(enabled, local, widths), local, widths, stream):
                continue
            if not soft_constraints:
                chosen = candidate
                break
            score = tuple(eval_bool(pred, local, widths) for pred in soft_constraints)
            if best_soft_score is None or score > best_soft_score:
                chosen = candidate
                best_soft_score = score
        # A merely best-effort random candidate must not silently override a
        # satisfiable soft clause.  Let the incremental SMT policy decide
        # whether the missing clauses are genuinely conflicting.
        if soft_constraints and best_soft_score is not None and not all(best_soft_score):
            chosen = None
        if chosen is None:
            from .backend.model import SolveRequest
            from .backend.smt import solve, solve_ordered

            request = SolveRequest(
                irs=tuple(enabled),
                random_paths=tuple(path for path, _ in constrained),
                state=state_values,
                var_index=var_index,
                assumptions=_randc_remaining_assumptions(var_index, randc_seen),
                soft_constraints=soft_constraints,
            )
            ordered_result = (
                solve_ordered(request, solve_order, lambda count: _draw_index(stream, count))
                if exact_ordered else None
            )
            result = ordered_result if ordered_result is not None else (
                _solve_finite_dist(request, enabled, env, widths, stream, solve) or solve(request)
            )
            if not result.is_sat and randc_seen:
                # The cycle has no remaining legal value.  Start a fresh
                # cycle only after proving the full constraint set is SAT.
                reset_request = SolveRequest(
                    irs=tuple(enabled),
                    random_paths=tuple(path for path, _ in constrained),
                    state=state_values,
                    var_index=var_index,
                    soft_constraints=soft_constraints,
                )
                reset_result = _solve_finite_dist(
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
    _commit_randc_state(obj, randc_seen, signature, graph)
    obj._SvObject__svtypes_randomize_status = RandomizeStatus(True, "sat")
    return True


def _solve_dynamic_collections(
    obj: Any,
    cls: type,
    stream: BitStream,
    enabled: list[ConstraintIR],
) -> bool:
    """Solve dynamic sizes first, then solve the expanded element constraints.

    This mirrors the LRM ordering for a constrained dynamic array or queue.
    The first pass deliberately contains no foreach element predicates; after
    resizing, the second pass expands those predicates at the chosen size.
    """

    from .backend.model import SolveRequest
    from .backend.smt import solve

    all_leaves = list(iter_class_leaves(cls))
    var_index = {
        var.path: var
        for ir in enabled
        for var in ir.vars
        if var.kind in {"field", "size"} and "[" not in var.path
    }
    size_vars = {var.path: var for var in var_index.values() if var.kind == "size"}
    if not size_vars:
        return _solve(obj, cls, stream, None, enabled)

    static_snapshot = snapshot_leaves(obj, [path for path, _, _ in all_leaves])
    collection_snapshot = {
        str(var.path): copy.deepcopy(resolve_attr(obj, str(var.path).removeprefix("@size:"))._elements)
        for var in size_vars.values()
    }
    try:
        state: dict[str, int] = {}
        for path, desc, declared_rand in all_leaves:
            active = declared_rand and _effective_rand_mode(obj, path) == 1
            if not active:
                value = leaf_unsigned(resolve_attr(obj, path), resolve_attr(obj, path).value)
                state[path] = value

        constrained_size_keys = _explicit_size_constraint_keys(enabled)
        active_sizes: list[tuple[str, Any]] = []
        for key, var in size_vars.items():
            collection = resolve_attr(obj, key.removeprefix("@size:"))
            if (
                key in constrained_size_keys
                and var.declared_rand
                and _effective_rand_mode(obj, key.removeprefix("@size:")) == 1
            ):
                active_sizes.append((key, collection))
            else:
                value = collection.size()
                state[key] = value

        candidates: list[dict[str, int]] = [{}]
        if active_sizes:
            assumptions: list[Expr] = []
            for path, collection in active_sizes:
                size_expr = Expr("size", (path.removeprefix("@size:"), path), bv(32, False))
                # Keep the resource ceiling out of generated SV; it is the
                # Python container's established decoder/encoder safety cap.
                assumptions.append(Expr("le", (size_expr, c_int(collection._max_length)), BOOL))
            request = SolveRequest(
                irs=tuple(enabled),
                random_paths=tuple(path for path, _ in active_sizes),
                state=state,
                var_index=var_index,
                assumptions=tuple(assumptions),
                soft_constraints=_ordered_soft_constraints(enabled),
            )
            candidates = _enumerate_dynamic_size_models(solve, request, active_sizes, stream)
            if not candidates:
                obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "unsat")
                return False

        while candidates:
            index = _draw_index(stream, len(candidates))
            chosen = candidates.pop(index)
            for path, collection in active_sizes:
                collection._resize_for_randomize(chosen[path])
            expanded = [_expand_dynamic_ir(obj, ir) for ir in enabled]
            if _solve(obj, cls, stream, None, expanded):
                return True
            restore_leaves(obj, static_snapshot)
            for key, elements in collection_snapshot.items():
                resolve_attr(obj, key.removeprefix("@size:"))._elements = copy.deepcopy(elements)

        obj._SvObject__svtypes_randomize_status = RandomizeStatus(False, "unsat")
        return False
    except Exception:
        restore_leaves(obj, static_snapshot)
        for key, elements in collection_snapshot.items():
            resolve_attr(obj, key.removeprefix("@size:"))._elements = elements
        raise


def _expand_dynamic_ir(obj: Any, ir: ConstraintIR) -> ConstraintIR:
    """Expand dynamic foreach statements after their collection sizes are fixed."""

    from .analyze import _collect_solve_before, _flatten_hard_stmt, _flatten_soft_stmt

    def expr(
        value: Expr,
        loop: str | None = None,
        index: Any | None = None,
        assoc_array: str | None = None,
    ) -> Expr:
        if value.op == "size":
            return c_int(resolve_attr(obj, str(value.args[0])).size(), value.loc)
        if value.op == "loopvar" and loop == str(value.args[0]):
            assert index is not None
            return c_int(index, value.loc)
        args: list[Any] = []
        changed = False
        for arg in value.args:
            if isinstance(arg, Expr):
                new = expr(arg, loop, index, assoc_array)
            elif isinstance(arg, tuple):
                new = tuple(
                    replace(item, low=expr(item.low, loop, index, assoc_array), high=expr(item.high, loop, index, assoc_array) if item.high is not None else None, weight=expr(item.weight, loop, index, assoc_array))
                    if hasattr(item, "low") and hasattr(item, "weight") else item
                    for item in arg
                )
            else:
                new = arg
            args.append(new)
            changed = changed or new is not arg
        if value.op == "field" and loop is not None:
            path = str(args[0])
            if assoc_array is not None:
                path = path.replace(f"{assoc_array}[{loop}]", assoc_path(assoc_array, index))
            else:
                path = path.replace(f"[{loop}]", f"[{index}]")
            changed = changed or path != args[0]
            args[0] = path
        return Expr(value.op, tuple(args), value.ty, value.loc, value.undef, value.hint) if changed else value

    def statement(
        stmt: IRStmt,
        loop: str | None = None,
        index: Any | None = None,
        assoc_array: str | None = None,
    ) -> list[IRStmt]:
        if stmt.kind == "for" and stmt.array is not None:
            collection = resolve_attr(obj, stmt.array)
            if isinstance(collection, (DynArray, Queue)):
                out: list[IRStmt] = []
                for item_index in range(collection.size()):
                    for child in stmt.then_body:
                        out.extend(statement(child, stmt.var, item_index))
                return out
        if stmt.kind == "assoc_foreach" and stmt.array is not None:
            collection = resolve_attr(obj, stmt.array)
            if isinstance(collection, AssocArray):
                out: list[IRStmt] = []
                for key in collection._elements:
                    for child in stmt.then_body:
                        out.extend(statement(child, stmt.var, key, stmt.array))
                return out
        return [IRStmt(
            kind=stmt.kind,
            expr=expr(stmt.expr, loop, index, assoc_array) if stmt.expr is not None else None,
            cond=expr(stmt.cond, loop, index, assoc_array) if stmt.cond is not None else None,
            then_body=[item for child in stmt.then_body for item in statement(child, loop, index, assoc_array)],
            else_body=[item for child in stmt.else_body for item in statement(child, loop, index, assoc_array)],
            var=stmt.var,
            start=expr(stmt.start, loop, index, assoc_array) if stmt.start is not None else None,
            stop=expr(stmt.stop, loop, index, assoc_array) if stmt.stop is not None else None,
            array=stmt.array,
            collection_kind=stmt.collection_kind,
            before=stmt.before,
            after=stmt.after,
        )]

    statements = [item for stmt in ir.statements for item in statement(stmt)]
    dynamic_vars = {
        path: VarDecl(path, declared, desc.width, bool(desc.signed), None, None, desc)
        for path, desc, declared in iter_object_leaves(obj)
    }
    vars = [var for var in ir.vars if var.kind == "field" and "[" not in var.path]
    vars.extend(var for path, var in dynamic_vars.items() if path not in {item.path for item in vars})
    return ConstraintIR(
        name=ir.name,
        predicates=[_flatten_hard_stmt(stmt) for stmt in statements],
        soft_predicates=[predicate for stmt in statements for predicate in _flatten_soft_stmt(stmt)],
        solve_before=[edge for stmt in statements for edge in _collect_solve_before(stmt)],
        vars=vars,
        parameters=ir.parameters,
        statements=statements,
    )


def _explicit_size_constraint_keys(irs: list[ConstraintIR]) -> set[str]:
    """Return size variables used by predicates, excluding foreach bounds."""

    keys: set[str] = set()

    def visit_expr(expr: Expr | None) -> None:
        if expr is None:
            return
        if expr.op == "size":
            keys.add(str(expr.args[1]))
        for arg in expr.args:
            if isinstance(arg, Expr):
                visit_expr(arg)
            elif isinstance(arg, tuple):
                for item in arg:
                    if hasattr(item, "low"):
                        visit_expr(item.low)
                        visit_expr(item.high)
                        visit_expr(item.weight)

    def visit_stmt(stmt: IRStmt) -> None:
        if stmt.kind in {"pred", "soft"}:
            visit_expr(stmt.expr)
        elif stmt.kind == "if":
            visit_expr(stmt.cond)
            for child in [*stmt.then_body, *stmt.else_body]:
                visit_stmt(child)

    for ir in irs:
        for stmt in ir.statements:
            visit_stmt(stmt)
    return keys


def _enumerate_dynamic_size_models(
    solve: Any,
    request: Any,
    active_sizes: list[tuple[str, Any]],
    stream: BitStream,
) -> list[dict[str, int]]:
    """Return size witnesses, exhaustively for a bounded product domain."""

    domain_size = 1
    for _path, collection in active_sizes:
        domain_size *= collection._max_length + 1
    if domain_size <= 4096:
        models: list[dict[str, int]] = []
        assumptions = list(request.assumptions)
        while True:
            result = solve(replace(request, assumptions=tuple(assumptions)))
            if not result.is_sat:
                return models
            model = dict(result.assignments)
            models.append(model)
            alternatives = [
                Expr(
                    "ne",
                    (Expr("size", (path.removeprefix("@size:"), path), bv(32, False)), c_int(model[path])),
                    BOOL,
                )
                for path, _collection in active_sizes
            ]
            blocker = alternatives[0]
            for alternative in alternatives[1:]:
                blocker = Expr("lor", (blocker, alternative), BOOL)
            assumptions.append(blocker)

    # An unbounded expansion is not a safe solver strategy.  Try the
    # deterministic model plus randomized bounded probes; callers still use
    # the fully expanded solver to validate each selected size.
    probes: list[dict[str, int]] = []
    attempted: set[tuple[int, ...]] = set()
    for attempt in range(33):
        assumptions = list(request.assumptions)
        if attempt:
            values = tuple(
                stream.draw_bits(32) % (collection._max_length + 1)
                for _path, collection in active_sizes
            )
            if values in attempted:
                continue
            attempted.add(values)
            for (path, _collection), value in zip(active_sizes, values):
                assumptions.append(Expr(
                    "eq",
                    (Expr("size", (path.removeprefix("@size:"), path), bv(32, False)), c_int(value)),
                    BOOL,
                ))
        result = solve(replace(request, assumptions=tuple(assumptions)))
        if result.is_sat:
            model = dict(result.assignments)
            if tuple(model[path] for path, _ in active_sizes) not in {
                tuple(item[path] for path, _ in active_sizes) for item in probes
            }:
                probes.append(model)
    return probes


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


def _solve_finite_dist(
    request: Any,
    irs: list[ConstraintIR],
    env: dict[str, int],
    widths: dict[str, tuple[int, bool]],
    stream: BitStream,
    solve: Any,
) -> Any | None:
    """Choose finite, state-resolvable direct distributions by joint weight.

    The generic SMT solver is a satisfiability engine, not a weighted model
    sampler.  For a bounded Cartesian product of direct ``dist`` supports,
    enumerate the support choices, prove each combination satisfiable, then
    choose a witness by the product of its declared weights.  This preserves
    weights when distributions constrain one another instead of silently
    accepting an arbitrary SMT model.
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
    if not dist_exprs:
        return None
    from fractions import Fraction
    from math import lcm
    from .ir import c_int

    random_paths = set(request.random_paths)
    supports: list[tuple[Expr, Any, dict[int, Fraction]]] = []
    combinations = 1
    for dist_expr in dist_exprs:
        options = _finite_dist_options(dist_expr, env, widths, random_paths)
        if options is None:
            return None
        nonzero = {value: weight for value, weight in options.items() if weight > 0}
        if not nonzero:
            return None
        combinations *= len(nonzero)
        if combinations > 4096:
            return None
        supports.append((dist_expr.args[0], dist_expr.loc, nonzero))

    # Keep distinct support combinations even if the backend returns the same
    # completion for unrelated leaves: each combination represents a declared
    # distribution event, so their weights add.
    model_weights: dict[tuple[tuple[str, int], ...], tuple[Fraction, Any]] = {}
    choices: list[tuple[Fraction, tuple[Expr, ...]]] = [(Fraction(1), ())]
    for left, loc, options in supports:
        choices = [
            (
                prior_weight * weight,
                (*prior_assumptions, Expr("eq", (left, c_int(value, loc)), BOOL, loc)),
            )
            for prior_weight, prior_assumptions in choices
            for value, weight in options.items()
        ]
    for weight, assumptions in choices:
        result = solve(replace(request, assumptions=(*request.assumptions, *assumptions)))
        if result.is_sat:
            key = tuple(sorted(result.assignments.items()))
            previous = model_weights.get(key)
            model_weights[key] = (
                weight if previous is None else previous[0] + weight,
                result,
            )
    models = list(model_weights.values())
    if not models:
        return None
    denominator = 1
    for weight, _ in models:
        denominator = lcm(denominator, weight.denominator)
    integer_weights = [(int(weight * denominator), result) for weight, result in models]
    total = sum(weight for weight, _ in integer_weights)
    draw_limit = ((1 << 64) // total) * total
    while True:
        draw = stream.draw_bits(64)
        if draw < draw_limit:
            break
    pick = draw % total
    for weight, result in integer_weights:
        if pick < weight:
            return result
        pick -= weight
    raise ConstraintBackendError("weighted finite dist selection lost its chosen model")


def _finite_dist_options(
    dist_expr: Expr,
    env: dict[str, int],
    widths: dict[str, tuple[int, bool]],
    random_paths: set[str],
) -> dict[int, Any] | None:
    """Resolve one direct ``dist`` support to exact rational per-value weights."""

    from fractions import Fraction

    options: dict[int, Fraction] = {}
    for item in dist_expr.args[1]:
        if _expr_fields(item.low) & random_paths or _expr_fields(item.weight) & random_paths:
            return None
        if item.high is not None and _expr_fields(item.high) & random_paths:
            return None
        low = eval_expr(item.low, env, widths)
        high = eval_expr(item.high, env, widths) if item.high is not None else None
        weight = eval_expr(item.weight, env, widths)
        if low.undef or weight.undef or (high is not None and high.undef):
            return None
        raw_weight = _value_as_int(weight.bits, weight.ty.width, weight.ty.signed)
        if raw_weight < 0:
            return None
        low_value = _value_as_int(low.bits, low.ty.width, low.ty.signed)
        high_value = low_value if high is None else _value_as_int(high.bits, high.ty.width, high.ty.signed)
        count = high_value - low_value + 1
        if count <= 0:
            continue
        if len(options) + count > 4096:
            return None
        contribution = Fraction(raw_weight, 1 if item.each else count)
        for value in range(low_value, high_value + 1):
            options[value] = options.get(value, Fraction(0)) + contribution
    return options


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
    graph: _RandomGraph | None = None,
) -> dict[str, set[int]]:
    descriptors = {path: desc for path, desc, _ in leaves}
    working: dict[str, set[int]] = {}
    for path in paths:
        owner, key = graph.owners[path] if graph is not None else (obj, path)
        old = owner._SvObject__svtypes_randc_state.get(key)
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
    graph: _RandomGraph | None = None,
) -> None:
    for path, seen in seen_by_path.items():
        target = resolve_attr(obj, path)
        updated = set(seen)
        updated.add(leaf_unsigned(target, target.value))
        owner, key = graph.owners[path] if graph is not None else (obj, path)
        owner._SvObject__svtypes_randc_state[key] = (signature, updated)


def _ordered_soft_constraints(irs: list[ConstraintIR]) -> tuple[Expr, ...]:
    """Return highest-priority soft clauses first.

    Inline constraints are appended after class blocks; derived blocks are
    collected after base blocks.  Reversing both levels therefore matches the
    SV override direction while retaining deterministic same-block ordering.
    """

    return tuple(
        predicate
        for ir in reversed(irs)
        for predicate in reversed(ir.soft_predicates)
    )


def _order_constrained_paths(
    constrained: list[tuple[str, Any]],
    irs: list[ConstraintIR],
) -> list[tuple[str, Any]]:
    """Apply solve-before edges to deterministic candidate draw order."""

    original = [path for path, _ in constrained]
    present = set(original)
    successors: dict[str, set[str]] = {path: set() for path in original}
    indegree: dict[str, int] = {path: 0 for path in original}
    for ir in irs:
        for before, after in ir.solve_before:
            for left in before:
                for right in after:
                    if left not in present or right not in present or right in successors[left]:
                        continue
                    successors[left].add(right)
                    indegree[right] += 1
    result: list[str] = []
    ready = [path for path in original if indegree[path] == 0]
    while ready:
        path = ready.pop(0)
        result.append(path)
        for target in original:
            if target not in successors[path]:
                continue
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(result) != len(original):
        raise ConstraintError("solve_before constraints contain a cycle")
    descs = dict(constrained)
    return [(path, descs[path]) for path in result]


def _solve_before_order(irs: list[ConstraintIR], constrained: list[tuple[str, Any]]) -> tuple[str, ...]:
    involved = {
        path
        for ir in irs
        for before, after in ir.solve_before
        for path in (*before, *after)
    }
    if not involved:
        return ()
    selected = [(path, desc) for path, desc in constrained if path in involved]
    return tuple(path for path, _ in _order_constrained_paths(selected, irs))


def _irs_contain_dist(irs: list[ConstraintIR]) -> bool:
    def contains(expr: Expr) -> bool:
        if expr.op == "dist":
            return True
        return any(contains(arg) for arg in expr.args if isinstance(arg, Expr))

    return any(contains(pred) for ir in irs for pred in ir.predicates)


def _draw_index(stream: BitStream, count: int) -> int:
    if count < 1:
        raise ConstraintBackendError("ordered solve has no feasible values")
    return stream.draw_enum(list(range(count)))


def _value_from_bits(desc: Any, bits: int) -> Any:
    from ..logic import LogicValue

    if isinstance(desc, Logic):
        return LogicValue(desc.width, bits & ((1 << desc.width) - 1), 0, 0)
    return desc._normalize(bits)
