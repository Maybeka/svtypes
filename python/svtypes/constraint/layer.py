"""@rand_layer declaration, membership, and cross-priority diagnostics."""

from __future__ import annotations

import ast
import warnings
from types import FunctionType
from typing import Any, Callable

from ..errors import (
    ConstraintSyntaxError,
    DeclarationError,
    LayeredRandomizationPriorityWarning,
)
from ..randomizable import is_randomizable
from .ast import RandLayerBatch, RandLayerDecl, RandLayerInfo, SourceLoc
from .collect import _RESERVED
from .frontend import _loc, extract_function_def

BUILTIN_ALIAS = "builtin"
BUILTIN_PRIORITY = 0

_layer_ref_policy = "warning"


def set_layered_randomization_reference_policy(policy: str) -> str:
    """Set how lower-priority random references are diagnosed.

    Returns the previous policy so callers can restore it.
    """
    global _layer_ref_policy
    if policy not in ("warning", "error"):
        raise ValueError(
            "layered randomization reference policy must be 'warning' or 'error'"
        )
    previous = _layer_ref_policy
    _layer_ref_policy = policy
    return previous


def get_layered_randomization_reference_policy() -> str:
    return _layer_ref_policy


def rand_layer(priority: Any = None, /, **kwargs: Any) -> Callable[[FunctionType], RandLayerDecl]:
    if kwargs:
        raise ConstraintSyntaxError("@rand_layer does not accept keyword arguments")
    if type(priority) is not int:
        raise ConstraintSyntaxError("@rand_layer requires a non-zero int priority")
    if priority == 0:
        raise ConstraintSyntaxError(
            "@rand_layer cannot use reserved priority 0 (alias 'builtin')"
        )

    def decorator(fn: FunctionType) -> RandLayerDecl:
        if not isinstance(fn, FunctionType):
            raise ConstraintSyntaxError("@rand_layer can only decorate a function")
        if fn.__name__ == BUILTIN_ALIAS:
            raise DeclarationError("rand_layer alias 'builtin' is reserved")
        return parse_rand_layer_function(fn, priority)

    return decorator


def parse_rand_layer_function(fn: FunctionType, priority: int) -> RandLayerDecl:
    func, filename, source = extract_function_def(fn, kind="rand_layer")
    loc = _loc(func, filename, fn.__name__)
    args = func.args
    if (
        len(args.args) != 1
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or args.kwonlyargs
        or args.posonlyargs
    ):
        raise ConstraintSyntaxError(
            f"{loc.format()}: rand_layer method must take exactly one parameter (self)"
        )
    extra = [_decorator_name(item) for item in func.decorator_list]
    extra = [name for name in extra if name != "rand_layer"]
    if extra:
        raise ConstraintSyntaxError(
            f"{loc.format()}: decorator stack besides @rand_layer is not allowed"
        )
    members: list[str] = []
    uses_super = False
    for stmt in func.body:
        item = _layer_statement(stmt, func.args.args[0].arg, fn.__name__, loc, filename)
        if item is None:
            continue
        kind, payload = item
        if kind == "super":
            uses_super = True
            continue
        if payload not in members:
            members.append(payload)
    if not members and not uses_super:
        raise ConstraintSyntaxError(f"{loc.format()}: rand_layer body cannot be empty")
    return RandLayerDecl(
        alias=fn.__name__,
        priority=priority,
        members=tuple(members),
        uses_super=uses_super,
        loc=loc,
        filename=filename,
        func=fn,
    )


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    if isinstance(node, ast.Attribute):
        return node.attr
    return type(node).__name__


def _layer_statement(
    node: ast.stmt,
    self_name: str,
    alias: str,
    func_loc: SourceLoc,
    filename: str,
) -> tuple[str, str] | None:
    loc = _loc(node, filename, alias)
    if isinstance(node, ast.Expr):
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return None
        if _is_super_call(value, alias):
            return ("super", alias)
        path = _self_target_path(value, self_name, loc)
        if path is not None:
            return ("member", path)
        raise ConstraintSyntaxError(
            f"{loc.format()}: rand_layer body must list self.<name>, "
            f"self.<array>[index], or super().{alias}()"
        )
    raise ConstraintSyntaxError(
        f"{loc.format()}: assignment, calls, control flow, and other "
        f"expressions are not allowed in rand_layer {alias!r}"
    )


def _self_target_path(node: ast.AST, self_name: str, loc: SourceLoc) -> str | None:
    indices: list[int] = []
    current: ast.AST = node
    while isinstance(current, ast.Subscript):
        indices.append(_const_index(current.slice, loc))
        current = current.value
    indices.reverse()
    if not (
        isinstance(current, ast.Attribute)
        and isinstance(current.value, ast.Name)
        and current.value.id == self_name
    ):
        return None
    path = current.attr
    for index in indices:
        path = f"{path}[{index}]"
    return path


def _const_index(node: ast.AST, loc: SourceLoc) -> int:
    if isinstance(node, ast.Slice):
        raise ConstraintSyntaxError(
            f"{loc.format()}: part-select/slice is not allowed in rand_layer targets"
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        raise ConstraintSyntaxError(
            f"{loc.format()}: rand_layer array index must be a non-negative int"
        )
    if not isinstance(node, ast.Constant) or type(node.value) is not int:
        raise ConstraintSyntaxError(
            f"{loc.format()}: rand_layer array index must be a constant non-bool int"
        )
    if node.value < 0:
        raise ConstraintSyntaxError(
            f"{loc.format()}: rand_layer array index must be a non-negative int"
        )
    return node.value


def _is_super_call(node: ast.AST, alias: str) -> bool:
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != alias:
        return False
    callee = func.value
    return (
        isinstance(callee, ast.Call)
        and isinstance(callee.func, ast.Name)
        and callee.func.id == "super"
        and not callee.args
        and not callee.keywords
    )


def collect_rand_layers(cls: type) -> None:
    from ..object import SvStruct

    is_struct = any(base.__name__ == "SvStruct" for base in cls.__mro__)
    own: dict[str, RandLayerDecl] = {}
    for name, attr in cls.__dict__.items():
        if isinstance(attr, RandLayerDecl):
            if is_struct:
                raise DeclarationError(
                    f"@rand_layer is not allowed on SvStruct {cls.__name__}"
                )
            own[name] = attr

    parent_table: dict[str, RandLayerInfo] = {}
    for base in cls.__mro__[1:]:
        if base is object or base.__name__ in {"SvObject", "SvStruct"}:
            continue
        inherited = getattr(base, "_SvObject__svtypes_layer_table", None)
        if inherited:
            parent_table = {
                alias: info
                for alias, info in inherited.items()
                if alias != BUILTIN_ALIAS
            }
            break

    fields = {name for name, _ in getattr(cls, "_SvObject__svtypes_members", ())}
    params = {name for name, _ in getattr(cls, "_SvObject__svtypes_params", ())}
    constraints = getattr(cls, "_SvObject__svtypes_constraint_decls", {})
    members = dict(getattr(cls, "_SvObject__svtypes_members", ()))

    table: dict[str, RandLayerInfo] = dict(parent_table)
    for alias, decl in own.items():
        _check_layer_alias(cls, alias, fields, params, constraints)
        if alias in parent_table and parent_table[alias].priority != decl.priority:
            raise DeclarationError(
                f"{cls.__name__}.{alias} rand_layer priority {decl.priority} "
                f"conflicts with inherited priority {parent_table[alias].priority}"
            )
        parent_info = parent_table.get(alias)
        if decl.uses_super:
            if parent_info is None:
                raise DeclarationError(
                    f"{cls.__name__}.{alias} calls super().{alias}() but no "
                    f"inherited rand_layer {alias!r} exists"
                )
            if parent_info.priority != decl.priority:
                raise DeclarationError(
                    f"{cls.__name__}.{alias} super() merge requires the same "
                    f"priority as the inherited layer ({parent_info.priority})"
                )
        variables, layer_constraints = _resolve_layer_members(
            cls,
            decl,
            members,
            constraints,
            parent_info if decl.uses_super else None,
        )
        table[alias] = RandLayerInfo(
            alias=alias,
            priority=decl.priority,
            variables=variables,
            constraints=layer_constraints,
        )

    var_owner: dict[str, str] = {}
    cstr_owner: dict[str, str] = {}
    for alias, info in table.items():
        for name in info.variables:
            for previous, prev_alias in var_owner.items():
                if _paths_overlap(previous, name):
                    raise DeclarationError(
                        f"{cls.__name__} random variable {name!r} overlaps "
                        f"{previous!r} in rand_layer {prev_alias!r} and {alias!r}"
                    )
            var_owner[name] = alias
        for name in info.constraints:
            previous = cstr_owner.get(name)
            if previous is not None:
                raise DeclarationError(
                    f"{cls.__name__} constraint {name!r} belongs to both "
                    f"rand_layer {previous!r} and {alias!r}"
                )
            cstr_owner[name] = alias

    sv_targets = singular_rand_paths(cls)
    builtin_vars = _builtin_variables(cls, members, var_owner)
    builtin_constraints = tuple(
        name for name in constraints if name not in cstr_owner
    )
    table[BUILTIN_ALIAS] = RandLayerInfo(
        alias=BUILTIN_ALIAS,
        priority=BUILTIN_PRIORITY,
        variables=builtin_vars,
        constraints=builtin_constraints,
    )

    batches = _build_batches(table)
    cls._SvObject__svtypes_layer_table = table
    cls._SvObject__svtypes_layer_batches = batches
    cls._SvObject__svtypes_sv_rand_targets = sv_targets
    _diagnose_cross_priority_refs(cls, table, members)


def _check_layer_alias(
    cls: type,
    alias: str,
    fields: set[str],
    params: set[str],
    constraints: dict[str, Any],
) -> None:
    if alias.startswith("_svtypes_") or alias.startswith("__svtypes_"):
        raise DeclarationError(f"rand_layer alias {alias!r} is reserved")
    if alias in _RESERVED:
        raise DeclarationError(
            f"rand_layer alias {alias!r} conflicts with a reserved SvObject method"
        )
    if alias in fields or alias in params:
        raise DeclarationError(
            f"rand_layer alias {alias!r} conflicts with a field or parameter"
        )
    if alias in constraints:
        raise DeclarationError(
            f"rand_layer alias {alias!r} conflicts with constraint {alias!r}"
        )
    for base in cls.mro()[1:]:
        if base is object or base.__name__ in {"SvObject", "SvStruct"}:
            continue
        if alias not in base.__dict__:
            continue
        attr = base.__dict__[alias]
        if isinstance(attr, RandLayerDecl):
            continue
        raise DeclarationError(
            f"rand_layer alias {alias!r} conflicts with base attribute {base.__name__}.{alias}"
        )


def _resolve_layer_members(
    cls: type,
    decl: RandLayerDecl,
    members: dict[str, Any],
    constraints: dict[str, Any],
    parent: RandLayerInfo | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    variables: list[str] = []
    layer_constraints: list[str] = []
    if parent is not None:
        variables.extend(parent.variables)
        layer_constraints.extend(parent.constraints)
    for name in decl.members:
        if "[" not in name and name in constraints:
            if name not in layer_constraints:
                layer_constraints.append(name)
            continue
        _validate_variable_target(cls, name, members, decl)
        if name not in variables:
            variables.append(name)
    return tuple(variables), tuple(layer_constraints)


def _validate_variable_target(
    cls: type,
    path: str,
    members: dict[str, Any],
    decl: RandLayerDecl,
) -> None:
    loc = decl.loc
    root, indices = _split_root_indices(path)
    desc = members.get(root)
    if desc is None:
        raise DeclarationError(
            f"{loc.format()}: {cls.__name__}.{decl.alias} lists unknown member {path!r}"
        )
    if not bool(getattr(desc, "rand", False)):
        raise DeclarationError(
            f"{loc.format()}: {cls.__name__}.{decl.alias} cannot list "
            f"non-rand variable {path!r}"
        )
    current = desc
    walked = root
    for index in indices:
        from ..collection import Array

        if not isinstance(current, Array):
            raise DeclarationError(
                f"{loc.format()}: {cls.__name__}.{decl.alias} lists {path!r}, "
                "which is not a fixed unpacked-array element "
                "(packed vector bit select is not a rand target)"
            )
        if index >= current._size:
            raise DeclarationError(
                f"{loc.format()}: {cls.__name__}.{decl.alias} index {index} "
                f"is out of range for {walked}"
            )
        walked = f"{walked}[{index}]"
        current = current._elem_template
    if not is_randomizable(current):
        raise DeclarationError(
            f"{loc.format()}: {cls.__name__}.{decl.alias} lists "
            f"{path!r}, which is not a SystemVerilog-controllable rand target"
        )


def _split_root_indices(path: str) -> tuple[str, tuple[int, ...]]:
    from .leaves import split_path

    tokens = split_path(path)
    if not tokens or not isinstance(tokens[0], str):
        raise DeclarationError(f"invalid rand_layer path {path!r}")
    indices: list[int] = []
    for token in tokens[1:]:
        if not isinstance(token, int):
            raise DeclarationError(f"invalid rand_layer path {path!r}")
        indices.append(token)
    return tokens[0], tuple(indices)


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(right + "[")
        or left.startswith(right + ".")
        or right.startswith(left + "[")
        or right.startswith(left + ".")
    )


def expand_declared_paths(cls: type, paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Expand declared layer/field paths to singular SV rand_mode targets."""
    members = dict(getattr(cls, "_SvObject__svtypes_members", ()))
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for item in _expand_one_path(path, members):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return tuple(out)


def singular_rand_paths(cls: type) -> tuple[str, ...]:
    members = dict(getattr(cls, "_SvObject__svtypes_members", ()))
    paths: list[str] = []
    for name, desc in members.items():
        if bool(getattr(desc, "rand", False)) and is_randomizable(desc):
            paths.extend(_expand_descriptor(name, desc))
    return tuple(paths)


def _expand_one_path(path: str, members: dict[str, Any]) -> list[str]:
    root, indices = _split_root_indices(path)
    desc = members[root]
    walked = root
    current = desc
    for index in indices:
        current = current._elem_template
        walked = f"{walked}[{index}]"
    return _expand_descriptor(walked, current)


def _expand_descriptor(path: str, desc: Any) -> list[str]:
    from ..collection import Array

    if isinstance(desc, Array):
        out: list[str] = []
        for index in range(desc._size):
            out.extend(_expand_descriptor(f"{path}[{index}]", desc._elem_template))
        return out
    return [path]


def _builtin_variables(
    cls: type,
    members: dict[str, Any],
    var_owner: dict[str, str],
) -> tuple[str, ...]:
    explicit = tuple(var_owner)
    builtin: list[str] = []
    for name, desc in members.items():
        if not (bool(getattr(desc, "rand", False)) and is_randomizable(desc)):
            continue
        related = [
            path for path in explicit
            if path == name or path.startswith(name + "[") or path.startswith(name + ".")
        ]
        if not related:
            builtin.append(name)
            continue
        owned = set(expand_declared_paths(cls, tuple(related)))
        for item in _expand_descriptor(name, desc):
            if item not in owned:
                builtin.append(item)
    return tuple(builtin)


def _build_batches(table: dict[str, RandLayerInfo]) -> tuple[RandLayerBatch, ...]:
    grouped: dict[int, list[RandLayerInfo]] = {}
    for info in table.values():
        grouped.setdefault(info.priority, []).append(info)
    batches: list[RandLayerBatch] = []
    for priority in sorted(grouped, reverse=True):
        infos = sorted(grouped[priority], key=lambda item: item.alias)
        variables: list[str] = []
        constraints: list[str] = []
        aliases: list[str] = []
        seen_vars: set[str] = set()
        seen_cstr: set[str] = set()
        for info in infos:
            aliases.append(info.alias)
            for name in info.variables:
                if name not in seen_vars:
                    seen_vars.add(name)
                    variables.append(name)
            for name in info.constraints:
                if name not in seen_cstr:
                    seen_cstr.add(name)
                    constraints.append(name)
        batches.append(
            RandLayerBatch(
                priority=priority,
                aliases=tuple(aliases),
                variables=tuple(variables),
                constraints=tuple(constraints),
            )
        )
    return tuple(batches)


def _diagnose_cross_priority_refs(
    cls: type,
    table: dict[str, RandLayerInfo],
    members: dict[str, Any],
) -> None:
    var_priority: dict[str, tuple[int, str]] = {}
    constraint_priority: dict[str, tuple[int, str]] = {}
    for info in table.values():
        for name in info.variables:
            var_priority[name] = (info.priority, info.alias)
        for name in info.constraints:
            constraint_priority[name] = (info.priority, info.alias)

    irs = getattr(cls, "_SvObject__svtypes_constraint_irs", {})
    for cname, ir in irs.items():
        owner = constraint_priority.get(cname, (BUILTIN_PRIORITY, BUILTIN_ALIAS))
        for var in ir.vars:
            path = str(var.path)
            root = path.split("[", 1)[0].split(".", 1)[0]
            desc = members.get(root)
            if desc is None or not bool(getattr(desc, "rand", False)):
                continue
            if not getattr(var, "declared_rand", True):
                continue
            referenced = _owner_for_var_path(path, var_priority)
            if referenced is None:
                continue
            ref_priority, ref_alias = referenced
            if ref_priority >= owner[0]:
                continue
            message = (
                f"{cls.__name__} constraint {cname!r} (priority {owner[0]}, "
                f"alias {owner[1]!r}) references random variable {path!r} "
                f"(priority {ref_priority}, alias {ref_alias!r})"
            )
            if _layer_ref_policy == "error":
                raise DeclarationError(message)
            warnings.warn(message, LayeredRandomizationPriorityWarning, stacklevel=2)


def _owner_for_var_path(
    path: str,
    var_priority: dict[str, tuple[int, str]],
) -> tuple[int, str] | None:
    best: tuple[int, str] | None = None
    best_len = -1
    for key, owner in var_priority.items():
        if path == key or path.startswith(key + "[") or path.startswith(key + "."):
            if len(key) > best_len:
                best = owner
                best_len = len(key)
    return best


def layers_schema_entries(cls: type) -> list[dict[str, Any]]:
    table: dict[str, RandLayerInfo] = getattr(cls, "_SvObject__svtypes_layer_table", {})
    entries = []
    for info in table.values():
        entries.append(
            {
                "alias": info.alias,
                "constraints": sorted(info.constraints),
                "priority": info.priority,
                "variables": sorted(info.variables),
            }
        )
    return sorted(entries, key=lambda item: (-item["priority"], item["alias"]))
