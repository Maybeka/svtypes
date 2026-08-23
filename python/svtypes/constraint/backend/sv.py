"""SystemVerilog constraint {} renderer."""

from __future__ import annotations

from typing import Any

from ..ir import ConstraintIR, Expr, IRStmt


def render_constraint_blocks(cls: type, indent: str, step: str) -> list[str]:
    irs: dict[str, ConstraintIR] = getattr(cls, "_SvObject__svtypes_own_constraint_irs", {})
    if not irs:
        return []
    lines: list[str] = [""]
    for name in sorted(irs):
        ir = irs[name]
        lines.append(f"{indent}{step}constraint {name} {{")
        body = ir.statements or [IRStmt(kind="pred", expr=pred) for pred in ir.predicates]
        for stmt in body:
            lines.extend(_render_stmt(stmt, indent + step + step, step))
        lines.append(f"{indent}{step}}}")
    return lines


def _sv_rand_mode_paths(cls: type, names: tuple[str, ...] | list[str]) -> list[str]:
    from ..layer import expand_declared_paths
    from ...collection import DynArray, Queue

    members = dict(getattr(cls, "_SvObject__svtypes_members", ()))
    # SV permits rand_mode() only on a singular variable.  A dynamic array or
    # queue is randomizable, but `array.rand_mode()` is illegal; its elements
    # are the singular variables.  Do not emit an invalid call in the helper.
    return [
        path
        for path in expand_declared_paths(cls, names)
        if not isinstance(members.get(path.split(".", 1)[0].split("[", 1)[0]), (DynArray, Queue))
    ]


def _mode_tmp(kind: str, path: str) -> str:
    ident = path.replace("[", "_").replace("]", "").replace(".", "_")
    return f"__svtypes_{kind}_{ident}"


def render_layered_randomize(cls: type, indent: str, step: str) -> list[str]:
    batches = getattr(cls, "_SvObject__svtypes_layer_batches", ())
    targets = _sv_rand_mode_paths(cls, getattr(cls, "_SvObject__svtypes_sv_rand_targets", ()))
    constraints = tuple(getattr(cls, "_SvObject__svtypes_constraint_irs", {}))
    ind = indent + step
    inner = indent + step + step
    lines = [
        "",
        f"{ind}virtual function int layered_randomize();",
        f"{inner}int __svtypes_ok;",
    ]
    for path in targets:
        lines.append(f"{inner}int {_mode_tmp('rand', path)};")
    for name in constraints:
        lines.append(f"{inner}int {_mode_tmp('cstr', name)};")
    lines.append(f"{inner}__svtypes_ok = 1;")
    for path in targets:
        lines.append(f"{inner}{_mode_tmp('rand', path)} = {path}.rand_mode();")
    for name in constraints:
        lines.append(f"{inner}{_mode_tmp('cstr', name)} = {name}.constraint_mode();")
    for path in targets:
        lines.append(f"{inner}{path}.rand_mode(0);")
    for name in constraints:
        lines.append(f"{inner}{name}.constraint_mode(0);")
    for batch in batches:
        batch_paths = _sv_rand_mode_paths(cls, batch.variables)
        lines.append(f"{inner}if (__svtypes_ok) begin")
        for path in batch_paths:
            lines.append(f"{inner}{step}{path}.rand_mode(1);")
        for name in batch.constraints:
            lines.append(f"{inner}{step}{name}.constraint_mode(1);")
        lines.append(f"{inner}{step}if (!this.randomize()) begin")
        lines.append(f"{inner}{step}{step}__svtypes_ok = 0;")
        lines.append(f"{inner}{step}end")
        lines.append(f"{inner}{step}else begin")
        for path in batch_paths:
            lines.append(f"{inner}{step}{step}{path}.rand_mode(0);")
        for name in batch.constraints:
            lines.append(f"{inner}{step}{step}{name}.constraint_mode(0);")
        lines.append(f"{inner}{step}end")
        lines.append(f"{inner}end")
    for path in targets:
        lines.append(f"{inner}{path}.rand_mode({_mode_tmp('rand', path)});")
    for name in constraints:
        lines.append(f"{inner}{name}.constraint_mode({_mode_tmp('cstr', name)});")
    lines.append(f"{inner}return __svtypes_ok;")
    lines.append(f"{ind}endfunction")
    return lines


def _render_stmt(stmt: IRStmt, indent: str, step: str) -> list[str]:
    if stmt.kind == "pred":
        assert stmt.expr is not None
        return [f"{indent}{render_expr(stmt.expr)};"]
    if stmt.kind == "soft":
        assert stmt.expr is not None
        return [f"{indent}soft {render_expr(stmt.expr)};"]
    if stmt.kind == "solve_before":
        assert stmt.before is not None and stmt.after is not None
        before = ", ".join(_field_sv(path) for path in stmt.before)
        after = ", ".join(_field_sv(path) for path in stmt.after)
        return [f"{indent}solve {before} before {after};"]
    if stmt.kind == "for":
        assert stmt.var is not None and stmt.start is not None and stmt.stop is not None
        if not stmt.array:
            raise ValueError(
                "a symbolic range() loop requires an indexed array to be rendered "
                "in a SystemVerilog constraint (SV constraints have no `for` statement)"
            )
        # SystemVerilog constraints support only `foreach`; a symbolic
        # range(i) loop becomes a foreach over the indexed array filtered by
        # the loop bounds.
        lines = [
            f"{indent}foreach ({_field_sv(str(stmt.array))}[{stmt.var}]) {{",
            f"{indent}{step}if ({stmt.var} >= {render_expr(stmt.start)} && "
            f"{stmt.var} < {render_expr(stmt.stop)}) {{",
        ]
        for child in stmt.then_body:
            lines.extend(_render_stmt(child, indent + step + step, step))
        lines.append(f"{indent}{step}}}")
        lines.append(f"{indent}}}")
        return lines
    assert stmt.cond is not None
    lines = [f"{indent}if ({render_expr(stmt.cond)}) {{"]
    for child in stmt.then_body:
        lines.extend(_render_stmt(child, indent + step, step))
    if stmt.else_body:
        lines.append(f"{indent}}} else {{")
        for child in stmt.else_body:
            lines.extend(_render_stmt(child, indent + step, step))
    lines.append(f"{indent}}}")
    return lines


def render_expr(expr: Expr) -> str:
    if expr.op == "bool":
        return "1'b1" if expr.args[0] else "1'b0"
    if expr.op == "int":
        return _render_int(expr)
    if expr.op == "field":
        return _field_sv(str(expr.args[0]))
    if expr.op == "size":
        return f"{_field_sv(str(expr.args[0]))}.size()"
    if expr.op in ("param", "loopvar"):
        return str(expr.args[0])
    if expr.op == "not":
        inner = expr.args[0]
        if inner.op == "inside":
            return f"!{render_expr(inner)}"
        return f"!({render_expr(inner)})"
    if expr.op == "inv":
        return f"(~{render_expr(expr.args[0])})"
    if expr.op == "u+":
        return f"(+{render_expr(expr.args[0])})"
    if expr.op == "u-":
        return f"(-{render_expr(expr.args[0])})"
    if expr.op == "ite":
        return f"({render_expr(expr.args[0])} ? {render_expr(expr.args[1])} : {render_expr(expr.args[2])})"
    if expr.op == "inside":
        value = render_expr(expr.args[0])
        items = ", ".join(render_expr(item) for item in expr.args[1:])
        return f"({value} inside {{{items}}})"
    if expr.op == "dist":
        value = render_expr(expr.args[0])
        items = []
        for item in expr.args[1]:
            target = render_expr(item.low)
            if item.high is not None:
                target = f"[{target}:{render_expr(item.high)}]"
            op = ":=" if item.each else ":/"
            items.append(f"{target} {op} {render_expr(item.weight)}")
        return f"({value} dist {{{', '.join(items)}}})"
    if expr.op == "unique":
        return f"unique {{{', '.join(render_expr(item) for item in expr.args)}}}"
    binops = {
        "add": "+", "sub": "-", "mul": "*", "mod": "%",
        "shl": "<<", "shr": ">>",
        "and": "&", "or": "|", "xor": "^",
        "eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
        "land": "&&", "lor": "||",
    }
    if expr.op in binops:
        left = _cast_operand(expr.args[0], expr)
        right = _cast_operand(expr.args[1], expr)
        return f"({left} {binops[expr.op]} {right})"
    raise ValueError(f"cannot render operator {expr.op!r}")


def _render_int(expr: Expr) -> str:
    value = int(expr.args[0])
    if expr.hint and expr.hint.startswith("enum:"):
        return expr.hint.split(":", 1)[1]
    width = expr.ty.width or max(value.bit_length() if value >= 0 else value.bit_length() + 1, 1)
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if expr.hint == "hex":
        return f"{sign}{width}'h{magnitude:x}"
    if expr.hint == "bin":
        return f"{sign}{width}'b{magnitude:b}"
    if expr.hint == "oct":
        return f"{sign}{width}'o{magnitude:o}"
    return f"{sign}{width}'d{magnitude}"


def _cast_operand(expr: Expr, parent: Expr) -> str:
    text = render_expr(expr)
    if expr.hint and expr.hint.startswith("enum:"):
        return text
    width = parent.ty.width if parent.ty.width else expr.ty.width
    if not width or parent.ty.is_bool:
        signed = expr.ty.signed
        if parent.op in {"lt", "le", "gt", "ge"}:
            mixed = expr.ty.signed != parent.args[0].ty.signed or expr.ty.signed != parent.args[1].ty.signed
            if mixed:
                return f"$unsigned({text})"
            if expr.ty.signed:
                return f"$signed({text})"
        return text
    cast = f"{width}'({text})"
    if parent.ty.signed or expr.ty.signed:
        if parent.op in {"lt", "le", "gt", "ge"} and not (
            parent.args[0].ty.signed and parent.args[1].ty.signed
        ):
            return f"$unsigned({cast})"
        if parent.ty.signed:
            return f"$signed({cast})"
    return cast


def _field_sv(path: str) -> str:
    return path.replace("].", "].")
