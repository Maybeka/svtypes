"""Uniform sampling of independent feasible bit-vector components.

SMT witnesses are used only for proof and complete enumeration. Large spaces
use uniform proposals and rejection, never a biased witness fallback.
"""

from math import prod

from ..budget import SolveFailure, check, poll
from ..eval import eval_bool
from ..sample import accept_distributions
from .model import SolveResult
from .smt import _as_long, _build_solver, _encode


def _fields(expr):
    result = set()
    if expr.op in ("field", "size"):
        result.add(str(expr.args[-1]))
    for arg in expr.args:
        if hasattr(arg, "op"):
            result.update(_fields(arg))
        elif isinstance(arg, tuple):
            for item in arg:
                for name in ("low", "high", "weight"):
                    child = getattr(item, name, None)
                    if child is not None:
                        result.update(_fields(child))
    return result


def _predicates(request):
    def flatten(expr):
        if expr.op == "land":
            yield from flatten(expr.args[0])
            yield from flatten(expr.args[1])
        else:
            yield expr
    return [expr for ir in request.irs for pred in ir.predicates for expr in flatten(pred)] + list(request.assumptions)


def _available_distributions(request, values, group, widths):
    """Only resolve a prefix distribution once all its inputs are available."""
    known = set(values)
    result = []
    def visit(statements):
        for stmt in statements:
            if stmt.kind == "pred" and stmt.expr is not None and stmt.expr.op == "dist":
                if _fields(stmt.expr.args[0]) & set(group) and _fields(stmt.expr).issubset(known):
                    result.append(stmt.expr)
            elif stmt.kind == "if" and stmt.cond is not None and _fields(stmt.cond).issubset(known):
                visit(stmt.then_body if eval_bool(stmt.cond, values, widths) else stmt.else_body)
    for ir in request.irs:
        visit(ir.statements)
    return result


def _bounds(z3, request, terms, widths, predicates, path):
    """A safe enclosing domain from unary hard clauses (including fixed state).

    Bounds do not need to be tight: uniform rejection preserves probabilities
    even when holes or other coupled clauses remove proposed values.
    """
    decl = request.var_index[path]
    solver = z3.Solver()
    for key, value in request.state.items():
        if key in terms:
            solver.add(terms[key] == value)
    if decl.enum_name and decl.descriptor is not None:
        mask = (1 << decl.width) - 1
        def enum_numeric(value):
            bits = int(value) & mask
            return bits - (1 << decl.width) if decl.signed and bits & (1 << (decl.width - 1)) else bits
        members = sorted({enum_numeric(x) for x in decl.descriptor.__class__._enum_items})
        solver.add(z3.Or([terms[path] == x for x in members]))
    else:
        members = None
    unary = [p for p in predicates if _fields(p) - set(request.state) == {path}]
    for pred in unary:
        value, undef = _encode(z3, pred, terms, widths)
        solver.add(value, z3.Not(undef))
    if not unary:
        return (-(1 << (decl.width - 1)), (1 << (decl.width - 1)) - 1, members) if decl.signed else (0, (1 << decl.width) - 1, members)
    # XOR the sign bit to search signed numeric order with unsigned SMT tests.
    sign_bit = (1 << (decl.width - 1)) if decl.signed else 0
    term = terms[path] ^ sign_bit if sign_bit else terms[path]
    def feasible(condition):
        solver.push()
        solver.add(condition)
        answer = check(solver, z3) == z3.sat
        solver.pop()
        return answer
    lo, hi = 0, (1 << decl.width) - 1
    while lo < hi:
        middle = (lo + hi) // 2
        if feasible(z3.ULE(term, middle)):
            hi = middle
        else:
            lo = middle + 1
    minimum = lo
    hi = (1 << decl.width) - 1
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if feasible(z3.UGE(term, middle)):
            lo = middle
        else:
            hi = middle - 1
    def numeric(bits):
        value = bits ^ sign_bit
        return value - (1 << decl.width) if sign_bit and value & sign_bit else value
    return (numeric(minimum), numeric(lo), members)


def sample(request, stream, order=None, accept=None):
    if order is None:
        order = request.selection_order
        groups = request.selection_groups or tuple((p,) for p in order)
    else:
        groups = tuple((p,) for p in order)
    z3, solver, terms, widths = _build_solver(request)
    if check(solver, z3) != z3.sat:
        return SolveResult.unsat()
    paths = tuple(p for p in request.random_paths if p in terms)
    predicates = _predicates(request)
    domains = {p: _bounds(z3, request, terms, widths, predicates, p) for p in paths}
    parent = {p: p for p in terms if p not in request.state}
    def root(p):
        while parent[p] != p:
            p = parent[p]
        return p
    def join(a, b):
        parent[root(b)] = root(a)
    # Connected components of the *accepted* solver assertions also include
    # soft clauses and enum guards. Fixed state cannot couple components.
    from z3.z3util import get_vars
    for assertion in solver.assertions():
        used = [str(v) for v in get_vars(assertion) if str(v) in parent]
        for p in used[1:]:
            join(used[0], p)
    components = {}
    for p in paths:
        components.setdefault(root(p), []).append(p)
    # Same-width direct equalities have exactly one completion per representative.
    aliases = {p: p for p in paths}
    def alias(p):
        while aliases[p] != p:
            p = aliases[p]
        return p
    for pred in predicates:
        if pred.op == "eq" and all(a.op == "field" for a in pred.args):
            a, b = (str(x.args[0]) for x in pred.args)
            if a in aliases and b in aliases and (request.var_index[a].width, request.var_index[a].signed) == (request.var_index[b].width, request.var_index[b].signed):
                aliases[alias(b)] = alias(a)
    # Equivalent fields share one proposal domain. Intersect their unary
    # enclosures, otherwise a wide parent alias could erase a child's tight
    # unique domain and force impractical rejection of permutations.
    for p in paths:
        representative = alias(p)
        a, b = domains[representative], domains[p]
        low, high = max(a[0], b[0]), min(a[1], b[1])
        members = a[2] if a[2] is not None else b[2]
        if members is not None:
            members = [v for v in members if low <= v <= high and (b[2] is None or v in b[2])]
        domains[representative] = (low, high, members)
    unique_groups = []
    for pred in predicates:
        if pred.op == "unique" and all(a.op == "field" for a in pred.args):
            all_group = [str(a.args[0]) for a in pred.args]
            group = [alias(p) for p in all_group if p in domains]
            states = [p for p in all_group if p not in domains]
            if len(group) >= 2 and len(set(group)) == len(group) and all(p in request.state for p in states):
                domain = domains[group[0]]
                decl = request.var_index[group[0]]
                if all(domains[p] == domain for p in group) and all(request.var_index[p].width == decl.width for p in all_group):
                    if not any(set(group) & set(old[0]) for old in unique_groups):
                        values = [request.state[p] for p in states]
                        if decl.signed:
                            values = [v - (1 << decl.width) if v & (1 << (decl.width - 1)) else v for v in values]
                        unique_groups.append((group, values))
    chosen = {}

    def draw(domain):
        low, high, members = domain
        return members[stream._draw_weighted_index(len(members))] if members is not None else low + stream._draw_weighted_index(high - low + 1)

    model_cache = {}

    def select(component, *, ordered=False):
        # Complete projection enumeration gives uniform feasible values/models.
        # Sparse relational spaces are probed even if their enclosing box is wide.
        box = prod((len(domains[p][2]) if domains[p][2] is not None else domains[p][1] - domains[p][0] + 1) for p in component)
        key = (ordered, tuple(component))
        if key in model_cache and model_cache[key] is not None:
            models = model_cache[key]
            return models[stream._draw_weighted_index(len(models))]
        limit = (4096 if box <= 4096 else 128) if key not in model_cache else 0
        models = []
        complete = False
        solver.push()
        for _ in range(limit + 1):
            poll("model_enumeration")
            if not limit:
                break
            if check(solver, z3) != z3.sat:
                complete = True
                break
            model = {p: _as_long(solver.model(), terms[p]) for p in component}
            models.append(model)
            solver.add(z3.Or([terms[p] != v for p, v in model.items()]))
        solver.pop()
        if complete:
            models.sort(key=lambda model: tuple(model[p] for p in component))
            model_cache[key] = models
            return models[stream._draw_weighted_index(len(models))]
        model_cache[key] = None
        # Every representative tuple has the same proposal probability. Unique
        # groups use uniform sampling without replacement on equal domains.
        for _ in range(100_000):
            poll("uniform_sampling")
            candidate = {}
            for group, state_values in unique_groups:
                if not set(group).issubset(component):
                    continue
                fixed = {p: domains[p][0] for p in group if domains[p][0] == domains[p][1] and domains[p][2] is None}
                remaining = [p for p in group if p not in fixed]
                candidate.update(fixed)
                if not remaining:
                    continue
                low, high, members = domains[remaining[0]]
                excluded = sorted(set((*fixed.values(), *state_values)))
                if members is not None:
                    members = [v for v in members if v not in excluded]
                    excluded = []
                else:
                    excluded = [v for v in excluded if low <= v <= high]
                size = len(members) if members is not None else high - low + 1 - len(excluded)
                if size < len(remaining):
                    return None
                swaps = {}
                for i, p in enumerate(remaining):
                    index = stream._draw_weighted_index(size - i)
                    value = swaps.get(index, index)
                    swaps[index] = swaps.get(size - i - 1, size - i - 1)
                    if members is not None:
                        candidate[p] = members[value]
                    else:
                        value += low
                        for excluded_value in excluded:
                            if value >= excluded_value:
                                value += 1
                        candidate[p] = value
            for p in component:
                representative = alias(p)
                if representative not in candidate:
                    candidate[representative] = draw(domains[representative])
                candidate[p] = candidate[representative]
            candidate = {p: value & ((1 << request.var_index[p].width) - 1) for p, value in candidate.items()}
            solver.push()
            solver.add(*[terms[p] == candidate[p] for p in component])
            feasible = check(solver, z3) == z3.sat
            solver.pop()
            if feasible:
                return {p: candidate[p] for p in component}
        raise SolveFailure("resource_limit", "uniform_sampling", "uniform proposal limit exhausted")

    # solve-before samples a projection before its dependent completion; it
    # does not weight the ordered value by its number of completions.
    ordered_values = {}
    applied_distributions = set()
    eval_widths = {p: (decl.width, decl.signed) for p, decl in request.var_index.items()}
    for group in groups:
        group = [p for p in group if p in terms]
        if not group:
            continue
        for _ in range(100_000):
            selected = select(group, ordered=True)
            if selected is None:
                return SolveResult.unsat()
            local = {**request.state, **ordered_values, **selected}
            distributions = [expr for expr in _available_distributions(request, local, group, eval_widths) if id(expr) not in applied_distributions]
            if accept_distributions(distributions, local, eval_widths, stream):
                applied_distributions.update(id(expr) for expr in distributions)
                break
        else:
            raise SolveFailure("resource_limit", "ordered_distribution_sampling", "ordered weight proposal limit exhausted")
        for p in group:
            solver.add(terms[p] == selected[p])
            selected_value = selected[p]
            decl = request.var_index[p]
            if decl.signed and selected_value & (1 << (decl.width - 1)):
                selected_value -= 1 << decl.width
            domains[p] = (selected_value, selected_value, None)
            domains[alias(p)] = domains[p]
        ordered_values.update(selected)
    # Weight rejection resamples only completions, never the already selected
    # randc/solve-before projection (which would bias its marginal probability).
    for _ in range(100_000 if accept is not None else 1):
        poll("weighted_sampling" if accept is not None else "uniform_sampling")
        chosen = dict(ordered_values)
        solver.push()
        for component in components.values():
            selected = select(component)
            if selected is None:
                solver.pop()
                return SolveResult.unsat()
            solver.add(*[terms[p] == value for p, value in selected.items()])
            chosen.update(selected)
        solver.pop()
        if accept is None or accept(chosen, applied_distributions):
            return SolveResult.sat(chosen)
    raise SolveFailure("resource_limit", "weighted_sampling", "weighted proposal limit exhausted")


def enumerate_models(request, limit=4096):
    """Enumerate complete projected models; None means strategy switch only."""
    z3, solver, terms, _ = _build_solver(request)
    models = []
    while check(solver, z3) == z3.sat:
        poll("model_enumeration")
        model = {p: _as_long(solver.model(), terms[p]) for p in request.random_paths}
        if len(models) >= limit:
            return None
        models.append(SolveResult.sat(model))
        solver.add(z3.Or([terms[p] != value for p, value in model.items()]))
    models.sort(key=lambda result: tuple(result.assignments[p] for p in request.random_paths))
    return models
