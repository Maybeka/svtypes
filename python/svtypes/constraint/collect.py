"""Class-creation collection of @constraint declarations."""

from __future__ import annotations

from types import FunctionType
from typing import Any

from ..errors import ConstraintSyntaxError, DeclarationError
from ..parameter import ParamRef, Parameter
from .ast import ConstraintDecl
from .frontend import parse_constraint_function

_RESERVED = {
    "pack",
    "unpack",
    "specialize",
    "to_sv_obj",
    "to_cpp_obj",
    "randomize",
    "randomize_with",
    "layered_randomize",
    "pre_randomize",
    "post_randomize",
    "svtypes_sprint",
    "svtypes_display",
    "svtypes_object_number",
    "from_bytes",
    "to_bytes",
    "value",
    "codec_session",
    "apply_plusargs",
    "svtypes_randomize_status",
    "svtypes_layered_randomize_status",
    "svtypes_layered_randomize_active",
    "svtypes_layered_randomize_priority",
}

_FINAL_ENTRY_METHODS = frozenset({
    "randomize",
    "randomize_with",
    "layered_randomize",
    "svtypes_layered_randomize_active",
    "svtypes_layered_randomize_priority",
})


def constraint(fn: Any = None, *args: Any, **kwargs: Any) -> ConstraintDecl:
    if args or kwargs or not isinstance(fn, FunctionType):
        raise ConstraintSyntaxError(
            "@constraint does not accept arguments; decorate a function directly"
        )
    return parse_constraint_function(fn)


def reject_final_entry_overrides(cls: type) -> None:
    for name in _FINAL_ENTRY_METHODS:
        if name in cls.__dict__:
            raise DeclarationError(
                f"{cls.__name__}.{name} is a framework-owned entry and cannot be redefined"
            )


def _effective_parameter(cls: type, name: str, fallback: Parameter | None = None) -> Parameter | None:
    for base in cls.__mro__:
        attr = base.__dict__.get(name)
        if isinstance(attr, Parameter):
            return attr
    return fallback


def has_unbound_parameters(cls: type) -> bool:
    """A template with at least one unbound declared parameter (excludes the
    ParamRef-forwarding intermediate specialization, whose parameters are
    declared on the subclass)."""
    params = getattr(cls, "_SvObject__svtypes_params", ())
    return any(
        isinstance(parameter, Parameter)
        and _effective_parameter(cls, name, parameter).value is None
        for name, parameter in params
    )


def is_parameterized_template(cls: type) -> bool:
    if has_unbound_parameters(cls):
        return True
    # An unresolved ParamRef-forwarding specialization (Base.specialize(A=ParamRef()))
    # is only an inheritance bridge, not an instantiable type.
    overrides = getattr(cls, "_SvObject__svtypes_parameter_overrides", {})
    if any(isinstance(value, ParamRef) for value in overrides.values()):
        return not getattr(cls, "_SvObject__svtypes_param_refs", {})
    return False


def collect_constraints(cls: type) -> None:
    if cls.__name__ in {"SvObject", "SvStruct"}:
        cls._SvObject__svtypes_is_template = False
        cls._SvObject__svtypes_constraint_decls = {}
        cls._SvObject__svtypes_constraint_irs = {}
        cls._SvObject__svtypes_own_constraint_irs = {}
        cls._SvObject__svtypes_layer_table = {}
        cls._SvObject__svtypes_layer_batches = ()
        cls._SvObject__svtypes_sv_rand_targets = ()
        return

    from ..object import ObjectDescriptor, SvObject

    reject_final_entry_overrides(cls)

    is_struct = any(base.__name__ == "SvStruct" for base in cls.__mro__)
    decls: dict[str, ConstraintDecl] = {}
    own_decls: dict[str, ConstraintDecl] = {}
    for base in reversed(cls.mro()):
        if base is object or base.__name__ in {"SvObject", "SvStruct"}:
            continue
        for name, attr in base.__dict__.items():
            if isinstance(attr, ConstraintDecl):
                if is_struct:
                    raise DeclarationError(
                        f"@constraint is not allowed on SvStruct {cls.__name__}"
                    )
                decls[name] = attr
                if base is cls:
                    own_decls[name] = attr

    fields = {name for name, _ in getattr(cls, "_SvObject__svtypes_members", ())}
    params = {name for name, _ in getattr(cls, "_SvObject__svtypes_params", ())}
    for name, decl in list(decls.items()):
        _check_constraint_name(cls, name, fields, params, decls)

    template = is_parameterized_template(cls)
    cls._SvObject__svtypes_is_template = template
    cls._SvObject__svtypes_constraint_decls = decls
    cls._SvObject__svtypes_constraint_irs = {}
    cls._SvObject__svtypes_own_constraint_irs = {}

    # Mark declared parameters so a later in-place __call__ on the class
    # attribute (Template.W(4)) is rejected; only pre-collection binding
    # inside the class body (Parameter()(1)) is allowed.
    for _p_name, _p_attr in getattr(cls, "_SvObject__svtypes_params", ()):
        if isinstance(_p_attr, Parameter):
            _p_attr._declared_in_class = True

    from .analyze import compile_block
    from .layer import collect_rand_layers

    compiled = {}
    for name, decl in decls.items():
        compiled[name] = compile_block(cls, decl)
    cls._SvObject__svtypes_constraint_irs = compiled
    cls._SvObject__svtypes_own_constraint_irs = {
        name: compiled[name] for name in own_decls if name in compiled
    }

    collect_rand_layers(cls)

    if template:
        # A template's constraints are compiled with symbolic parameter
        # references and rendered on the parameterized class definition;
        # field-type checks below do not apply to the template itself.
        return

    for name, desc in getattr(cls, "_SvObject__svtypes_members", ()):
        target = desc.__class__ if isinstance(desc, SvObject) else None
        if isinstance(desc, ObjectDescriptor):
            target = desc.registry.get(desc.cls_name)
        if isinstance(target, type) and issubclass(target, SvObject) and is_parameterized_template(target):
            raise DeclarationError(
                f"{cls.__name__}.{name} cannot use parameterized template {target.__name__} as a field type"
            )


def _check_constraint_name(
    cls: type,
    name: str,
    fields: set[str],
    params: set[str],
    decls: dict[str, ConstraintDecl],
) -> None:
    if name.startswith("_svtypes_") or name.startswith("__svtypes_"):
        raise DeclarationError(f"constraint name {name!r} is reserved")
    if name in _RESERVED:
        raise DeclarationError(f"constraint name {name!r} conflicts with a reserved SvObject method")
    if name in fields or name in params:
        raise DeclarationError(f"constraint name {name!r} conflicts with a field or parameter")
    for base in cls.mro()[1:]:
        if base is object or base.__name__ in {"SvObject", "SvStruct"}:
            continue
        if name in base.__dict__:
            attr = base.__dict__[name]
            if isinstance(attr, ConstraintDecl):
                continue
            raise DeclarationError(
                f"constraint name {name!r} conflicts with base attribute {base.__name__}.{name}"
            )
