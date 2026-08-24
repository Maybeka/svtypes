from __future__ import annotations

import copy
import struct
from typing import Any, TypeVar, Callable, overload

from .base import TypeBase, UserDefinedType
from .enum import Enum
from .errors import DeclarationError, EncodeError, RegistryError, ResourceLimitError, UnsupportedTypeError
from .limits import DecodeLimits


class ObjectRegistry:
    """Registry for SystemVerilog Object types."""

    _types: dict[str, Any]

    def __init__(self):
        object.__setattr__(self, '_types', {})

    def register(self, cls: type[SvObject] | type[Enum] | type[TypeBase], name: str | None = None) -> None:
        reg_name = name or cls.__name__
        existing = self._types.get(reg_name)
        if existing is not None and existing is not cls:
            raise RegistryError(
                f"SvTypes type '{reg_name}' is already registered as "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        self._types[reg_name] = cls
        namespace = getattr(self, "path", None)
        if namespace:
            normalized_namespace = namespace.replace("::", ".")
            cls._svtypes_unified_type_name = f"{normalized_namespace}.{reg_name}"

    def get(self, name: str) -> type[SvObject] | None:
        return self._types.get(name)

    def clear(self) -> None:
        self._types.clear()

    def list_types(self) -> list[str]:
        return list(self._types.keys())


_default_registry = ObjectRegistry()

SVTYPES_OBJECT_NUMBER_ORIGIN = 0x0001
SVX_OBJECT_ID_COUNTER_MASK = (1 << 48) - 1


class CodecSession:
    """Own object numbers and the runtime object registry for one synchronization session."""

    def __init__(self, *, origin: int = SVTYPES_OBJECT_NUMBER_ORIGIN, counter_start: int = 1) -> None:
        if not 0 < origin <= 0xFFFF:
            raise ValueError(f"Invalid SvTypes object number origin: {origin}")
        self.origin = origin
        self._objects: dict[int, SvObject] = {}
        self.reset_object_number_allocator(counter_start)

    def allocate_object_number(self) -> int:
        if self._next_object_counter > SVX_OBJECT_ID_COUNTER_MASK:
            raise RuntimeError("SvTypes Python object number counter exhausted")
        object_number = (self.origin << 48) | self._next_object_counter
        self._next_object_counter += 1
        return object_number

    def reset_object_number_allocator(self, start: int = 1) -> None:
        if start <= 0 or start > SVX_OBJECT_ID_COUNTER_MASK:
            raise ValueError(f"Invalid SvTypes object number counter start: {start}")
        self._next_object_counter = start

    def register(self, obj: "SvObject") -> None:
        if obj.svtypes_object_number != 0:
            existing = self._objects.get(obj.svtypes_object_number)
            if existing is not None and existing is not obj:
                raise RegistryError(
                    f"SvTypes object number collision for {obj.svtypes_object_number}: "
                    f"{existing.__class__.__name__} and {obj.__class__.__name__}"
                )
            self._objects[obj.svtypes_object_number] = obj

    def get(self, object_number: int) -> "SvObject" | None:
        return self._objects.get(object_number)

    def remove(self, object_number: int) -> "SvObject" | None:
        return self._objects.pop(object_number, None)

    def clear(self) -> None:
        self._objects.clear()

    def close(self) -> None:
        self.clear()

    def pack_context(self) -> "PackContext":
        return PackContext(self)

    def unpack_context(self, limits: DecodeLimits | None = None) -> "UnpackContext":
        return UnpackContext(self, limits=limits)


_default_session = CodecSession()


class PackContext:
    def __init__(self, session: CodecSession | None = None) -> None:
        self.session = session or _default_session
        self.seen: dict[int, int] = {}


class UnpackContext:
    def __init__(
        self,
        session: CodecSession | None = None,
        *,
        limits: DecodeLimits | None = None,
    ) -> None:
        self.session = session or _default_session
        self.limits = limits or DecodeLimits()
        self.objects: dict[int, SvObject] = {}
        self._depth = 0
        self._inline_object_numbers: set[int] = set()
        self._replaceable_bindings: dict[int, SvObject] = {}
        self._pending: list[tuple[SvObject, int, int, list[tuple[str, Any, Any]]]] = []

    def _begin(self, input_size: int) -> bool:
        root = self._depth == 0
        if root:
            if input_size > self.limits.max_input_bytes:
                raise ResourceLimitError(
                    f"input size {input_size} exceeds decoder limit {self.limits.max_input_bytes}"
                )
            self.objects.clear()
            self._pending.clear()
            self._inline_object_numbers.clear()
            self._replaceable_bindings.clear()
        if self._depth >= self.limits.max_nesting_depth:
            raise ResourceLimitError(
                f"object nesting depth exceeds decoder limit {self.limits.max_nesting_depth}"
            )
        self._depth += 1
        return root

    def _record_inline_object(self, object_number: int) -> None:
        self._inline_object_numbers.add(object_number)
        if len(self._inline_object_numbers) > self.limits.max_object_count:
            raise ResourceLimitError(
                f"object count exceeds decoder limit {self.limits.max_object_count}"
            )

    def _stage(
        self,
        obj: "SvObject",
        object_number: int,
        old_id: int,
        fields: list[tuple[str, Any, Any]],
    ) -> None:
        self._pending.append((obj, object_number, old_id, fields))

    def _finish(self, root: bool, succeeded: bool) -> None:
        self._depth -= 1
        if not root:
            return
        try:
            if succeeded:
                for obj, object_number, _, _ in self._pending:
                    existing = self.session.get(object_number)
                    replaceable = self._replaceable_bindings.get(object_number)
                    if existing is not None and existing is not obj and existing is not replaceable:
                        raise RegistryError(
                            f"SvTypes object number collision for {object_number}: "
                            f"{existing.__class__.__name__} and {obj.__class__.__name__}"
                        )
                # Make every object numberentity resolvable before assigning graph edges.
                for obj, object_number, old_id, _ in self._pending:
                    replaceable = self._replaceable_bindings.get(object_number)
                    if replaceable is not None and replaceable is not obj:
                        self.session.remove(object_number)
                    if old_id and old_id != object_number and self.session.get(old_id) is obj:
                        self.session.remove(old_id)
                    object.__setattr__(obj, "_SvObject__svtypes_object_number", object_number)
                    self.session.register(obj)
                for obj, _, _, fields in self._pending:
                    for name, desc, value in fields:
                        SvObject._assign_unpacked_field(obj, name, desc, value)
        finally:
            self.objects.clear()
            self._pending.clear()
            self._inline_object_numbers.clear()
            self._replaceable_bindings.clear()


_PackContext = PackContext
_UnpackContext = UnpackContext


def allocate_object_number() -> int:
    return _default_session.allocate_object_number()


def reset_object_number_allocator(start: int = 1) -> None:
    _default_session.reset_object_number_allocator(start)


def register_object(obj: "SvObject") -> None:
    obj.codec_session.register(obj)


def get_object(object_number: int) -> "SvObject" | None:
    """Return one object from the default runtime registry, if present."""
    return _default_session.get(object_number)


def unregister_object(object_number: int) -> "SvObject" | None:
    """Remove and return one object from the default runtime registry."""
    return _default_session.remove(object_number)


def clear_object_registry() -> None:
    """Clear the default runtime object registry for the current session."""
    _default_session.clear()


class ObjectDescriptor:
    def __init__(
        self,
        cls_name: str,
        registry: ObjectRegistry | None = None,
        strict_set: bool = False,
        rand: bool = False,
    ):
        self.cls_name = cls_name
        self.registry = registry or _default_registry
        self.strict_set = strict_set
        # A class handle is only recursively randomized when it is declared
        # ``rand``.  This is deliberately separate from handle construction:
        # randomization never allocates a missing object.
        self.rand = bool(rand)
        self.attr_name: str | None = None
        self._cache_key = f'_object_cache_{id(self)}'

    def __set_name__(self, owner, name):
        self.attr_name = name
        self._cache_key = f'_object_cache_{name}'

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        if self._cache_key in obj.__dict__:
            return obj.__dict__[self._cache_key]
        sv_obj: SvObject | None = None
        if sv_obj is None:
            cls = self.registry.get(self.cls_name)
            if cls is not None:
                session = getattr(obj, "codec_session", None)
                instance = cls(session=session)
                setattr(obj, self._cache_key, instance)
                sv_obj = instance
            else:
                raise ValueError(f"Class name '{self.cls_name}' not found in registry. Available types: {self.registry.list_types()}")
        return sv_obj

    def __set__(self, obj, value):
        if self.strict_set and value is not None:
            expected_cls = self.registry.get(self.cls_name)
            if expected_cls is not None and not isinstance(value, expected_cls):
                raise TypeError(f"Expected {self.cls_name}, got {type(value).__name__}")
        setattr(obj, self._cache_key, value)

    @property
    def pack_bytes(self) -> bool:
        return True

    def sv_decl(self, name: str) -> str:
        prefix = "rand " if self.rand else ""
        return f"{prefix}{self.cls_name} {name}"

    def to_sv_code(self, level=0, name: str | None = None) -> str:
        name = name or self.attr_name
        return f"{TypeBase.IND * level}{self.sv_decl(name)};"

    def cpp_decl(self, name: str) -> str:
        if name:
            return f"{self.cls_name}* {name} = nullptr"
        return f"{self.cls_name}*"


class ReadOnlyMetaclass(type):
    def __setattr__(cls, name, value):
        from .parameter import Parameter
        if name in cls.__dict__:
            attr = cls.__dict__[name]
            if isinstance(attr, Parameter):
                raise AttributeError(f"Parameter '{name}' in class '{cls.__name__}' is immutable. Use specialize() to override.")
        super().__setattr__(name, value)


from typing import TypeVar
T = TypeVar('T')

class SvObject(UserDefinedType, metaclass=ReadOnlyMetaclass):
    __svtypes_params: list[tuple[str, Any]] = []
    __svtypes_members: list[tuple[str, TypeBase]] = []
    __svtypes_fields: list[tuple[str, TypeBase]] = []
    __svtypes_base_name: str | None = None
    __svtypes_specialized_from: type["SvObject"] | None = None
    __svtypes_parameter_overrides: dict[str, Any] = {}
    __svtypes_emit_specialization_class: bool = True
    __svtypes_is_template: bool = False
    __svtypes_constraint_decls: dict[str, Any] = {}
    __svtypes_constraint_irs: dict[str, Any] = {}
    __svtypes_layer_table: dict[str, Any] = {}
    __svtypes_layer_batches: tuple[Any, ...] = ()
    __svtypes_sv_rand_targets: tuple[str, ...] = ()

    def __init__(
        self,
        svtypes_object_number: int | None = None,
        session: CodecSession | None = None,
        _defer_identity: bool = False,
        **kwargs,
    ) -> None:
        if type(self).__svtypes_is_template:
            raise DeclarationError(
                f"{type(self).__name__} is a parameterized template; bind every Parameter with specialize() first"
            )
        super().__init__(**kwargs)
        self._codec_session = session or _default_session
        self.__svtypes_object_number = (
            0 if _defer_identity else (svtypes_object_number or self._codec_session.allocate_object_number())
        )
        self.__svtypes_rand_modes: dict[str, int] = {}
        self.__svtypes_randc_state: dict[str, tuple[tuple[str, ...], set[int]]] = {}
        self.__svtypes_constraint_modes: dict[str, int] = {}
        self.__svtypes_randomize_status = None
        self.__svtypes_layered_randomize_status = None
        self.__svtypes_layered_randomize_active = False
        self.__svtypes_layered_randomize_priority = 0
        if not _defer_identity:
            register_object(self)

    def __deepcopy__(self, memo):
        cls = self.__class__
        session = memo.get("__svtypes_session__")
        struct_type = globals().get("SvStruct")
        if struct_type is not None and isinstance(self, struct_type):
            session = None
        if session is None:
            try:
                session = self.codec_session
            except AttributeError:
                session = None
        copied = cls(session=session) if session is not None else cls()
        memo[id(self)] = copied
        for name, _ in self.__svtypes_members:
            desc = dict(self.__svtypes_members)[name]
            if isinstance(desc, ObjectDescriptor):
                if desc._cache_key in self.__dict__:
                    setattr(copied, name, copy.deepcopy(getattr(self, name), memo))
                continue
            getattr(copied, name).value = copy.deepcopy(getattr(self, name).value, memo)
        rand_modes = getattr(self, "_SvObject__svtypes_rand_modes", None)
        if rand_modes is not None:
            copied.__svtypes_rand_modes = dict(rand_modes)
            copied.__svtypes_randc_state = {
                path: (signature, set(seen))
                for path, (signature, seen) in self.__svtypes_randc_state.items()
            }
            copied.__svtypes_constraint_modes = dict(self.__svtypes_constraint_modes)
            copied.__svtypes_randomize_status = self.__svtypes_randomize_status
            copied.__svtypes_layered_randomize_status = self.__svtypes_layered_randomize_status
            copied.__svtypes_layered_randomize_active = False
            copied.__svtypes_layered_randomize_priority = 0
        return copied

    def __copy__(self):
        return copy.deepcopy(self)

    def svtypes_sprint(self, _seen: set[int] | None = None) -> str:
        from .collection import AssocArray, DynArray, Queue, Array

        seen = set() if _seen is None else _seen
        identity = id(self)
        if identity in seen:
            return f"<ref#{self.svtypes_object_number}>"
        seen.add(identity)

        def render(desc: Any, value: Any) -> str:
            if isinstance(desc, ObjectDescriptor):
                return "null" if value is None else value.svtypes_sprint(seen)
            if isinstance(desc, SvStruct):
                members = []
                for member_name, member_desc in desc.__svtypes_members:
                    member = getattr(value, member_name)
                    members.append(
                        f"{member_name}="
                        + render(member_desc, member if isinstance(member_desc, SvStruct) else member.value)
                    )
                return f"{desc.__class__.__name__}{{{', '.join(members)}}}"
            if isinstance(desc, SvObject):
                return "null" if value is None else value.svtypes_sprint(seen)
            if isinstance(desc, Array):
                return "[" + ", ".join(render(desc._elem_template, item) for item in value) + "]"
            if isinstance(desc, (DynArray, Queue)):
                return "[" + ", ".join(render(desc._elem_template, item) for item in value) + "]"
            if isinstance(desc, AssocArray):
                items = sorted(value.items(), key=lambda item: desc._key_template.pack(item[0]))
                return "{" + ", ".join(
                    f"{render(desc._key_template, key)}: {render(desc._val_template, item)}"
                    for key, item in items
                ) + "}"
            if isinstance(value, str):
                return repr(value)
            if isinstance(desc, Enum):
                return f"{desc.__class__.__name__}.{value.name}"
            return repr(value)

        fields = []
        for name, desc in self.__svtypes_members:
            if isinstance(desc, TypeBase) and not desc.dump:
                continue
            member = getattr(self, name)
            value = member if isinstance(desc, (ObjectDescriptor, SvStruct)) else member.value
            fields.append(f"{name}={render(desc, value)}")
        return f"{self.__class__.__name__}#{self.svtypes_object_number}{{{', '.join(fields)}}}"

    def svtypes_display(self) -> None:
        print(self.svtypes_sprint())

    @property
    def svtypes_object_number(self) -> int:
        return self.__svtypes_object_number

    @property
    def codec_session(self) -> CodecSession:
        return self._codec_session

    @svtypes_object_number.setter
    def svtypes_object_number(self, value: int) -> None:
        if value < 0 or value > ((1 << 64) - 1):
            raise ValueError(f"Invalid SvTypes object number: {value}")
        existing = self._codec_session.get(value)
        if value != 0 and existing is not None and existing is not self:
            raise RegistryError(
                f"SvTypes object number collision for {value}: "
                f"{existing.__class__.__name__} and {self.__class__.__name__}"
            )
        old_id = self.__svtypes_object_number
        if old_id != value and self._codec_session.get(old_id) is self:
            self._codec_session.remove(old_id)
        self.__svtypes_object_number = value
        register_object(self)

    @property
    def value(self):
        return self

    @value.setter
    def value(self, val):
        if val is None:
            self._value = None
            return
        if not isinstance(val, self.__class__):
             raise TypeError(f"Expected {self.__class__.__name__}, got {type(val)}")
        for name, _ in self.__svtypes_fields:
            # Copy values between objects
            getattr(self, name).value = getattr(val, name).value

    def __get__(self, instance, owner):
        if instance is None:
            return self
        # Descriptor behavior for nested objects
        if self._storage_key not in instance.__dict__:
            instance.__dict__[self._storage_key] = copy.deepcopy(self)
        return instance.__dict__[self._storage_key]

    def __getattribute__(self, name):
        # We override __getattribute__ to handle returning cloned TypeBase members
        # while keeping the descriptor behavior for ObjectDescriptor/SvObject
        if name.startswith('_'):
             return object.__getattribute__(self, name)

        # Check if it's a known SV member
        members = object.__getattribute__(self, '_SvObject__svtypes_members')
        for m_name, m_attr in members:
            if m_name == name:
                if isinstance(m_attr, ObjectDescriptor):
                    return object.__getattribute__(self, name)
                # Return instance-specific clone of the member
                storage_key = m_attr._storage_key
                if storage_key not in self.__dict__:
                    deepcopy_memo = {}
                    try:
                        deepcopy_memo["__svtypes_session__"] = object.__getattribute__(
                            self, "_codec_session"
                        )
                    except AttributeError:
                        pass
                    object.__setattr__(
                        self,
                        storage_key,
                        copy.deepcopy(m_attr, deepcopy_memo),
                    )
                    from .constraint.modes import bind_runtime_field

                    bind_runtime_field(getattr(self, storage_key), self, m_name, m_attr)
                return getattr(self, storage_key)

        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
            return

        members = object.__getattribute__(self, '_SvObject__svtypes_members')
        for m_name, m_attr in members:
            if m_name == name:
                if isinstance(m_attr, ObjectDescriptor):
                    super().__setattr__(name, value)
                    return
                raise AttributeError(f"Direct assignment to '{name}' is disabled. Use '{name}.value = ...' instead.")

        super().__setattr__(name, value)

    def _normalize(self, value):
        if not isinstance(value, self.__class__) and value is not None:
             raise TypeError(f"Expected {self.__class__.__name__}, got {type(value)}")
        return value

    def from_bytes(self, bytes_: bytes):
        decoded, count = self.unpack(bytes_, _target=self)
        if decoded is None:
            raise ValueError(f"Cannot unpack null into existing {self.__class__.__name__} object")
        self.__svtypes_object_number = decoded.svtypes_object_number
        for name, desc in self.__svtypes_fields:
            if isinstance(desc, ObjectDescriptor):
                setattr(self, name, getattr(decoded, name))
            elif isinstance(desc, SvObject):
                object.__setattr__(self, desc._storage_key, getattr(decoded, name))
            else:
                storage_key = desc._storage_key
                if storage_key in decoded.__dict__:
                    object.__setattr__(self, storage_key, decoded.__dict__[storage_key])
                else:
                    getattr(self, name).value = getattr(decoded, name).value
        register_object(self)
        return count

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from .parameter import Parameter, ParamRef

        seen = set()
        params = []
        ordered_members = []
        packed_fields = []

        mro = cls.mro()
        cls.__svtypes_base_name = None
        for base in mro[1:]:
            if base.__name__ in ('SvObject', 'SvStruct'):
                break
            if issubclass(base, SvObject):
                cls.__svtypes_base_name = base.__name__
                break

        # Parameters are only those declared in this class body; a subclass
        # does not inherit the base's parameter table. Constraint analysis can
        # still see bound base parameters through the MRO.
        for name, attr in cls.__dict__.items():
            if name.startswith('_'): continue
            if isinstance(attr, Parameter):
                seen.add(name)
                params.append((name, attr))

        for base in reversed(mro):
            if base is object or base.__name__ in ('SvObject', 'SvStruct'): continue
            for name, attr in base.__dict__.items():
                if name.startswith('_'): continue
                if name in seen: continue
                if isinstance(attr, Parameter):
                    # Parameters are never members; they are collected from the
                    # class body only, so a base's parameter must not leak in.
                    seen.add(name)
                    continue
                if isinstance(attr, ObjectDescriptor):
                    seen.add(name)
                    ordered_members.append((name, attr))
                    packed_fields.append((name, attr))
                    continue
                if isinstance(attr, TypeBase):
                    seen.add(name)
                    ordered_members.append((name, attr))
                    if attr.pack_bytes:
                        packed_fields.append((name, attr))

        cls.__svtypes_params = params
        cls.__svtypes_members = ordered_members
        cls.__svtypes_fields = packed_fields

        # Resolve ParamRef forwards declared on the base specialization.
        cls.__svtypes_param_refs: dict[str, str] = {}
        direct_base = cls.__bases__[0] if cls.__bases__ else None
        if (
            isinstance(direct_base, type)
            and issubclass(direct_base, SvObject)
            and direct_base.__name__ not in ('SvObject', 'SvStruct')
        ):
            from .constraint.collect import has_unbound_parameters

            is_spec_cls = "_SvObject__svtypes_emit_specialization_class" in cls.__dict__
            if not is_spec_cls and has_unbound_parameters(direct_base):
                raise DeclarationError(
                    f"{cls.__name__} cannot inherit unbound parameter template "
                    f"{direct_base.__name__}; bind every Parameter with specialize() first"
                )
            if not is_spec_cls:
                db_spec = getattr(direct_base, "_SvObject__svtypes_specialized_from", None)
                if db_spec is not None:
                    # A user-declared subclass of a specialization points at the
                    # intermediate specialization, not the original template.
                    cls._SvObject__svtypes_specialized_from = direct_base
                own_params = dict(params)
                base_overrides = getattr(direct_base, "_SvObject__svtypes_parameter_overrides", {})
                for name, value in base_overrides.items():
                    if not isinstance(value, ParamRef):
                        continue
                    target = value.name or name
                    if target not in own_params:
                        raise DeclarationError(
                            f"ParamRef({value.name!r}) on {direct_base.__name__}.specialize() refers to "
                            f"undeclared parameter {target!r} in {cls.__name__}"
                        )
                    base_param = next(
                        (p for p_name, p in getattr(direct_base, "_SvObject__svtypes_params", []) if p_name == name),
                        None,
                    )
                    if base_param is None:
                        # The original template's parameter list lives on the chain root.
                        root = direct_base
                        while root.__svtypes_specialized_from is not None:
                            root = root.__svtypes_specialized_from
                        base_param = next(
                            (p for p_name, p in getattr(root, "_SvObject__svtypes_params", []) if p_name == name),
                            None,
                        )
                    base_dtype = base_param.dtype if base_param is not None else None
                    if base_dtype is not None and own_params[target].dtype != base_dtype:
                        raise DeclarationError(
                            f"ParamRef({value.name!r}) dtype mismatch: base {direct_base.__name__} "
                            f"parameter {name!r} is {base_dtype!r}, subclass parameter {target!r} "
                            f"is {own_params[target].dtype!r}"
                        )
                    cls.__svtypes_param_refs[name] = target

        from .collection import Array
        from .bit import Bit
        from .logic import Logic
        struct_type = globals().get("SvStruct")
        is_struct_class = cls.__name__ == "SvStruct" or (
            struct_type is not None and issubclass(cls, struct_type)
        )

        def packed_struct_member_supported(desc: Any) -> bool:
            from .int import Int, LongInt

            return isinstance(desc, (Bit, Logic, Int, LongInt, Enum)) or (
                struct_type is not None and isinstance(desc, struct_type)
            )

        if is_struct_class and cls.__name__ != "SvStruct":
            if cls.__svtypes_base_name is not None:
                raise UnsupportedTypeError(
                    f"SvStruct inheritance is not supported in 1.0: {cls.__name__} extends {cls.__svtypes_base_name}"
                )
            for name, desc in ordered_members:
                if not packed_struct_member_supported(desc):
                    raise UnsupportedTypeError(
                        f"SvStruct member {cls.__name__}.{name} ({desc.__class__.__name__}) "
                        "is not a portable packed value"
                    )

        def rand_supported(desc: Any) -> bool:
            from .randomizable import is_randomizable

            return is_randomizable(desc)

        def randc_supported(desc: Any) -> bool:
            from .bit import Bit

            return isinstance(desc, (Bit, Enum))

        def plusarg_supported(desc: Any) -> bool:
            from .real import Real
            from .string import String

            if isinstance(desc, (Bit, Logic, Enum, Real, String)):
                return True
            if struct_type is not None and isinstance(desc, struct_type):
                return all(
                    not isinstance(member, TypeBase)
                    or not member.plusarg
                    or plusarg_supported(member)
                    for _, member in desc.__svtypes_members
                )
            return False

        def cov_supported(desc: Any) -> bool:
            from .collection import AssocArray, DynArray, Queue

            return isinstance(desc, (Bit, Logic, Enum, DynArray, Queue, AssocArray, ObjectDescriptor)) or (
                isinstance(desc, SvObject) and not (
                    struct_type is not None and isinstance(desc, struct_type)
                )
            )

        for name, desc in ordered_members:
            if isinstance(desc, TypeBase):
                if is_struct_class and desc.field_options.rand is True:
                    raise ValueError(
                        f"rand belongs to the containing field, not SvStruct member {cls.__name__}.{name}"
                    )
                if is_struct_class and desc.field_options.randc:
                    raise ValueError(
                        f"randc belongs to the containing field, not SvStruct member {cls.__name__}.{name}"
                    )
                if is_struct_class and desc.field_options.plusarg is True:
                    raise ValueError(
                        f"plusarg belongs to the containing field, not SvStruct member {cls.__name__}.{name}"
                    )
                if is_struct_class and desc.field_options.cov is True:
                    raise ValueError(
                        f"cov belongs to the containing field, not SvStruct member {cls.__name__}.{name}"
                    )
                if desc.field_options.rand is True and not rand_supported(desc):
                    raise ValueError(
                        f"rand=True is unsupported for {cls.__name__}.{name} ({desc.__class__.__name__})"
                    )
                if desc.randc and not randc_supported(desc):
                    raise ValueError(
                        f"randc=True is unsupported for {cls.__name__}.{name} ({desc.__class__.__name__})"
                    )
                if desc.field_options.plusarg is True and not plusarg_supported(desc):
                    raise ValueError(
                        f"plusarg=True is unsupported for {cls.__name__}.{name} ({desc.__class__.__name__})"
                    )
                if desc.field_options.cov is True and not cov_supported(desc):
                    raise ValueError(
                        f"cov=True is unsupported for {cls.__name__}.{name} ({desc.__class__.__name__})"
                    )
                if is_struct_class and not desc.pack_bytes:
                    raise ValueError(
                        f"pack_bytes=False is illegal for packed SvStruct member {cls.__name__}.{name}"
                    )

        from .constraint.collect import collect_constraints

        collect_constraints(cls)

    @classmethod
    def specialize(cls: type[T], **kwargs) -> type[T]:
        from .parameter import DTYPE_CLASSES, Parameter, ParamRef

        if "_SvObject__svtypes_emit_specialization_class" in cls.__dict__:
            raise DeclarationError(
                f"{cls.__name__} is already a specialization of "
                f"{cls.__svtypes_specialized_from.__name__}; specialize the original template again"
            )

        unbound = [name for name, parameter in cls.__svtypes_params if parameter.value is None]
        missing = [name for name in unbound if name not in kwargs]
        if missing:
            raise DeclarationError(
                f"{cls.__name__}.specialize() must bind every unbound Parameter; missing {', '.join(missing)}"
            )

        valid_params = {p_name for p_name, _ in cls.__svtypes_params}
        for name, value in kwargs.items():
            if name not in valid_params:
                raise AttributeError(f"Class {cls.__name__} has no parameter named '{name}'")

        def name_part(value: Any) -> str:
            if isinstance(value, type):
                value = value.__name__
            text = str(value).replace("-", "neg_")
            return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "value"

        suffix = "_".join(f"{name}_{name_part(value)}" for name, value in kwargs.items())
        new_name = f"{cls.__name__}_{suffix}" if suffix else f"{cls.__name__}_spec"
        namespace = {
            "__module__": cls.__module__,
            "_SvObject__svtypes_specialized_from": cls,
            "_SvObject__svtypes_parameter_overrides": dict(kwargs),
            # Fixed marker: this class is a Python-side binding of the template;
            # it never emits a generated SV/C++ class.
            "_SvObject__svtypes_emit_specialization_class": True,
        }
        base_dtypes = {p_name: p_attr.dtype for p_name, p_attr in cls.__svtypes_params}

        for name, value in kwargs.items():
            if isinstance(value, ParamRef):
                # Forward this parameter to a parameter declared in the
                # subclass body; resolved in __init_subclass__.
                namespace[name] = ParamRef(value.name)
                continue
            base_dtype = base_dtypes.get(name)
            if base_dtype == "type":
                if not isinstance(value, type):
                    raise TypeError(
                        f"type parameter {name!r} must be bound to a class/type, "
                        f"not {type(value).__name__}"
                    )
                namespace[name] = Parameter(type)(value)
                continue
            if isinstance(value, type):
                raise TypeError(
                    f"value parameter {name!r} cannot be bound to a type; "
                    "bind an int/str/float value"
                )
            param_cls = DTYPE_CLASSES.get(base_dtype) if base_dtype else None
            if param_cls is not None:
                namespace[name] = Parameter(param_cls)(value)
            else:
                namespace[name] = Parameter(value)
        return type(new_name, (cls,), namespace)

    @classmethod
    def _sv_specialized_base_expr(cls) -> str | None:
        base = cls.__svtypes_specialized_from
        if base is None:
            return None
        from .parameter import Parameter, ParamRef

        # Flatten to the original template only for a ParamRef-forwarding
        # subclass (which declares its own param_refs); a specialize() product
        # keeps extending its own template source (Python-side binding only).
        is_spec_cls = "_SvObject__svtypes_emit_specialization_class" in cls.__dict__
        template = base
        if not is_spec_cls:
            while template.__svtypes_specialized_from is not None:
                template = template.__svtypes_specialized_from
        refs = getattr(cls, "_SvObject__svtypes_param_refs", {})
        overrides = dict(getattr(cls, "_SvObject__svtypes_parameter_overrides", {}))
        params = []
        for name, value in overrides.items():
            if isinstance(value, ParamRef):
                params.append(f".{name}({refs.get(name, name)})")
                continue
            param = cls.__dict__.get(name)
            if param is None:
                param = Parameter(type)(value) if isinstance(value, type) else Parameter(value)
            params.append(f".{name}({param.sv_repr()})")
        return f"{template.__name__}#({', '.join(params)})"

    @classmethod
    def _sv_coverage_sample_type(cls) -> str:
        """Type used by the nested coverage collector's sample().

        For a template the nested class references the enclosing class with
        explicit parameter references (e.g. Templated#(.WIDTH(WIDTH))), so
        no default values are required; all other classes use their plain
        generated name."""
        if cls.__svtypes_is_template:
            refs = ", ".join(f".{p_name}({p_name})" for p_name, _ in cls.__svtypes_params)
            return f"{cls.__name__}#({refs})"
        return cls.__name__

    def _svtypes_rand_mode(self, path: str, on: int | None = None) -> int:
        from .constraint.modes import _require_mode_arg

        if on is None:
            return self.__svtypes_rand_modes.get(path, 1)
        self.__svtypes_rand_modes[path] = _require_mode_arg(on)
        return self.__svtypes_rand_modes[path]

    def randomize(self, *args: Any, **kwargs: Any) -> bool:
        if args or kwargs:
            raise TypeError("randomize() does not accept seed or solver options; use RandomContext")
        from .constraint.randomize import randomize_object

        return randomize_object(self)

    def randomize_with(self, fn: Any, *args: Any, **kwargs: Any) -> bool:
        if args or kwargs:
            raise TypeError("randomize_with() does not accept seed or solver options; use RandomContext")
        from .constraint.randomize import randomize_object_with

        return randomize_object_with(self, fn)

    def layered_randomize(self, *args: Any, **kwargs: Any) -> bool:
        if args or kwargs:
            raise TypeError("layered_randomize() does not accept arguments")
        from .constraint.randomize import layered_randomize_object

        return layered_randomize_object(self)

    def pre_randomize(self) -> None:
        return None

    def post_randomize(self) -> None:
        return None

    def svtypes_layered_randomize_active(self) -> bool:
        """Whether a hook is executing inside ``layered_randomize()``."""
        return self.__svtypes_layered_randomize_active

    def svtypes_layered_randomize_priority(self) -> int:
        """Current layered priority; meaningful only while ``active()`` is true."""
        return self.__svtypes_layered_randomize_priority

    @property
    def svtypes_randomize_status(self):
        return self.__svtypes_randomize_status

    @property
    def svtypes_layered_randomize_status(self):
        return self.__svtypes_layered_randomize_status

    @classmethod
    def _encoding_type_name(cls) -> str:
        from .schema import unified_type_name

        return unified_type_name(cls)

    @classmethod
    def _encoding_fingerprint_hex(cls) -> str:
        if cls.__svtypes_is_template:
            # Templates share the parameter-independent encoding fingerprint
            # with every specialization; only the encoding type name varies.
            from .schema import _descriptors, _fingerprint

            _, encoding = _descriptors(cls, set(), allow_template=True)
            return _fingerprint(encoding).hex()
        from .schema import schema_descriptor

        return schema_descriptor(cls).encoding_fingerprint_hex

    @classmethod
    def _sv_encoding_type_expr(cls) -> str:
        if cls.__svtypes_params and cls.__svtypes_specialized_from is None:
            type_name = cls._encoding_type_name()
            prefix = type_name.split("[", 1)[0]
            format_parts = []
            arguments = []
            for name, parameter in cls.__svtypes_params:
                dtype = parameter.dtype
                if dtype == "type":
                    format_parts.append(f"{name}:type=%s")
                    arguments.append(
                        parameter.sv_type_value() if parameter.is_bound else f"$typename({name})"
                    )
                elif dtype == "str":
                    format_parts.append(f"{name}:str=%s")
                    arguments.append(name)
                elif dtype == "float":
                    format_parts.append(f"{name}:float=%0g")
                    arguments.append(name)
                else:
                    format_parts.append(f"{name}:{dtype or 'int'}=%0d")
                    arguments.append(name)
            return f'$sformatf("{prefix}[{",".join(format_parts)}]", {", ".join(arguments)})'
        return f'"{cls._encoding_type_name()}"'

    @classmethod
    def _cpp_encoding_type_expr(cls) -> str:
        if cls.__svtypes_params and cls.__svtypes_specialized_from is None:
            type_name = cls._encoding_type_name()
            prefix = type_name.split("[", 1)[0]
            pieces = [f'std::string("{prefix}[")']
            for index, (name, parameter) in enumerate(cls.__svtypes_params):
                if index:
                    pieces.append('","')
                dtype = parameter.dtype
                if dtype == "type":
                    pieces.append(f'"{name}:type="')
                    pieces.append(
                        f'"{parameter.sv_type_value()}"'
                        if parameter.is_bound
                        else f"std::string(typeid({name}).name())"
                    )
                elif dtype == "str":
                    pieces.append(f'"{name}:str="')
                    pieces.append(name)
                elif dtype == "float":
                    pieces.append(f'"{name}:float="')
                    pieces.append(f"std::to_string({name})")
                else:
                    pieces.append(f'"{name}:{dtype or "int"}="')
                    pieces.append(f"std::to_string({name})")
            pieces.append('"]"')
            return " + ".join(pieces)
        return f'"{cls._encoding_type_name()}"'

    @classmethod
    def _cpp_specialized_base_expr(cls) -> str | None:
        base = cls.__svtypes_specialized_from
        if base is None:
            return None
        from .parameter import ParamRef, cpp_param_literal

        is_spec_cls = "_SvObject__svtypes_emit_specialization_class" in cls.__dict__
        template = base
        if not is_spec_cls:
            while template.__svtypes_specialized_from is not None:
                template = template.__svtypes_specialized_from
        refs = getattr(cls, "_SvObject__svtypes_param_refs", {})
        overrides = dict(getattr(cls, "_SvObject__svtypes_parameter_overrides", {}))
        values = []
        for name, value in overrides.items():
            if isinstance(value, ParamRef):
                values.append(refs.get(name, name))
                continue
            if isinstance(value, str):
                raise DeclarationError(
                    f"parameter {name!r} of type str cannot be represented as a generated "
                    "C++ template argument; bind an int/type parameter or skip C++ generation"
                )
            if isinstance(value, float):
                raise DeclarationError(
                    f"parameter {name!r} of type float cannot be represented as a generated "
                    "C++ template argument before C++20; bind an int/type parameter"
                )
            values.append(getattr(value, "__name__", cpp_param_literal(value)) if isinstance(value, type) else cpp_param_literal(value))
        return f"{template.__name__}<{', '.join(values)}>"

    @staticmethod
    def _sv_type_expr(desc: TypeBase) -> str:
        from .bit import Bit
        from .logic import Logic
        from .collection import AssocArray, DynArray, Queue, Array
        from .enum import Enum
        from .int import Int, LongInt
        from .logic import Logic
        from .real import Real, ShortReal
        from .remote_ref import RemoteRef
        from .string import String

        if isinstance(desc, Int):
            return "int"
        if isinstance(desc, LongInt):
            return "longint"
        if isinstance(desc, Bit):
            return desc.sv_decl("").strip()
        if isinstance(desc, Logic):
            return desc.sv_decl("").strip()
        if isinstance(desc, Enum):
            return desc.__class__.__name__
        if isinstance(desc, String):
            return "string"
        if isinstance(desc, ShortReal):
            return "shortreal"
        if isinstance(desc, Real):
            return "real"
        if isinstance(desc, RemoteRef):
            return "svtypes_pkg::remote_ref"
        if isinstance(desc, Array):
            return f"{SvObject._sv_type_expr(desc._elem_template)} [{desc._size}]"
        if isinstance(desc, Queue):
            return f"{SvObject._sv_type_expr(desc._elem_template)} [$]"
        if isinstance(desc, DynArray):
            return f"{SvObject._sv_type_expr(desc._elem_template)} []"
        if isinstance(desc, AssocArray):
            key_t = SvObject._sv_type_expr(desc._key_template)
            val_t = SvObject._sv_type_expr(desc._val_template)
            return f"{val_t} [{key_t}]"
        if isinstance(desc, ObjectDescriptor):
            return desc.cls_name
        if isinstance(desc, SvStruct):
            return f"{desc.__class__.__name__}_t"
        if isinstance(desc, SvObject):
            if desc.__class__.__svtypes_specialized_from is not None:
                # A specialization is a Python-side binding; the target-language
                # field type is the parameterized template instance.
                return desc.__class__._sv_specialized_base_expr()
            return desc.__class__.__name__
        raise NotImplementedError(
            f"SV type generation is not supported for {desc.__class__.__name__}"
        )

    @staticmethod
    def _sv_packer_expr(desc: TypeBase) -> str:
        from .bit import Bit
        from .collection import AssocArray, DynArray, Queue, Array
        from .enum import Enum
        from .int import Int, LongInt
        from .logic import Logic
        from .real import Real, ShortReal
        from .remote_ref import RemoteRef
        from .string import String

        if isinstance(desc, Int):
            return "svtypes_pkg::int_packer"
        if isinstance(desc, LongInt):
            return "svtypes_pkg::longint_packer"
        if isinstance(desc, Bit):
            return f"svtypes_pkg::bit_packer#({SvObject._sv_type_expr(desc)})"
        if isinstance(desc, Logic):
            return f"svtypes_pkg::logic_packer#({SvObject._sv_type_expr(desc)})"
        if isinstance(desc, Enum):
            return f"svtypes_pkg::bit_packer#({desc.__class__.__name__})"
        if isinstance(desc, String):
            return "svtypes_pkg::string_packer"
        if isinstance(desc, ShortReal):
            return "svtypes_pkg::shortreal_packer"
        if isinstance(desc, Real):
            return "svtypes_pkg::real_packer"
        if isinstance(desc, RemoteRef):
            return "svtypes_pkg::remote_ref_packer"
        if isinstance(desc, Array):
            elem_t = SvObject._sv_type_expr(desc._elem_template)
            elem_packer = SvObject._sv_packer_expr(desc._elem_template)
            return f"svtypes_pkg::fixed_array_packer#({elem_t}, {desc._size}, {elem_packer})"
        if isinstance(desc, Queue):
            elem_t = SvObject._sv_type_expr(desc._elem_template)
            elem_packer = SvObject._sv_packer_expr(desc._elem_template)
            return f"svtypes_pkg::queue_packer#({elem_t}, {elem_packer})"
        if isinstance(desc, DynArray):
            elem_t = SvObject._sv_type_expr(desc._elem_template)
            elem_packer = SvObject._sv_packer_expr(desc._elem_template)
            return f"svtypes_pkg::dyn_array_packer#({elem_t}, {elem_packer})"
        if isinstance(desc, AssocArray):
            key_t = SvObject._sv_type_expr(desc._key_template)
            val_t = SvObject._sv_type_expr(desc._val_template)
            key_packer = SvObject._sv_packer_expr(desc._key_template)
            val_packer = SvObject._sv_packer_expr(desc._val_template)
            return f"svtypes_pkg::assoc_array_packer#({key_t}, {val_t}, {key_packer}, {val_packer})"
        if isinstance(desc, ObjectDescriptor):
            return f"svtypes_pkg::object_packer#({desc.cls_name})"
        if isinstance(desc, SvStruct):
            return f"{desc.__class__.__name__}_packer"
        if isinstance(desc, SvObject):
            return f"svtypes_pkg::object_packer#({SvObject._sv_type_expr(desc)})"
        raise NotImplementedError(
            f"SV packer generation is not supported for {desc.__class__.__name__}"
        )

    @staticmethod
    def _sv_pack_lines(name: str, desc: TypeBase, indent: str) -> list[str]:
        from .bit import Bit
        from .enum import Enum
        from .int import Int, LongInt
        from .logic import Logic
        from .real import Real, ShortReal
        from .remote_ref import RemoteRef
        from .string import String

        if isinstance(desc, Int):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, LongInt):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, Bit):
            return [
                f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"
            ]
        if isinstance(desc, Logic):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, String):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, ShortReal):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, Real):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, RemoteRef):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, Enum):
            return [
                f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"
            ]
        if isinstance(desc, SvStruct):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, SvObject):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        if isinstance(desc, ObjectDescriptor):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::pack({name}, bytes);"]
        raise NotImplementedError(
            f"SV pack code generation is not supported for {desc.__class__.__name__}"
        )

    @staticmethod
    def _sv_unpack_lines(name: str, desc: TypeBase, indent: str) -> list[str]:
        from .bit import Bit
        from .enum import Enum
        from .int import Int, LongInt
        from .logic import Logic
        from .real import Real, ShortReal
        from .remote_ref import RemoteRef
        from .string import String

        if isinstance(desc, Int):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, LongInt):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, Bit):
            return [
                f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"
            ]
        if isinstance(desc, Logic):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, String):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, ShortReal):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, Real):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, RemoteRef):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, Enum):
            return [
                f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"
            ]
        if isinstance(desc, SvStruct):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, SvObject):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        if isinstance(desc, ObjectDescriptor):
            return [f"{indent}{SvObject._sv_packer_expr(desc)}::unpack({name}, bytes, offset);"]
        raise NotImplementedError(
            f"SV unpack code generation is not supported for {desc.__class__.__name__}"
        )

    @classmethod
    def to_sv_obj(cls, level=0):
        if "_SvObject__svtypes_emit_specialization_class" in cls.__dict__:
            raise DeclarationError(
                f"{cls.__name__} is a Python-side binding of "
                f"{cls.__svtypes_specialized_from.__name__}; specializations are not generated "
                "classes — use the template with parameters (Tpl#(.W(4))) in the target language"
            )
        ind_str = cls.IND * level
        header = f"{ind_str}class {cls.__name__}"
        specialized_base = cls._sv_specialized_base_expr()
        if cls.__svtypes_params:
            params_str = ", ".join([p_attr.to_sv_code(name=p_name).strip().strip(';') for p_name, p_attr in cls.__svtypes_params])
            header += f" #({params_str})"
        if specialized_base is not None:
            header += f" extends {specialized_base}"
        elif cls.__svtypes_base_name:
            header += f" extends {cls.__svtypes_base_name}"
        else:
            header += " extends svtypes_pkg::sv_object"
        header += ";"
        lines = [header]

        local_members = []
        for name, attr in cls.__dict__.items():
            if (
                (isinstance(attr, TypeBase) or isinstance(attr, ObjectDescriptor))
                and not name.startswith('_')
                and name not in {p_name for p_name, _ in cls.__svtypes_params}
            ):
                local_members.append((name, attr))

        for name, desc in local_members:
             declaration = desc.to_sv_code(level + 1, name=name)
             if isinstance(desc, TypeBase) and desc.randc:
                 declaration = declaration.replace(
                     ind_str + cls.IND,
                     ind_str + cls.IND + "randc ",
                     1,
                 )
             elif isinstance(desc, TypeBase) and desc.rand:
                 declaration = declaration.replace(
                     ind_str + cls.IND,
                     ind_str + cls.IND + "rand ",
                     1,
                 )
             lines.append(declaration)

        from .constraint.backend.sv import (
            render_constraint_blocks,
            render_layered_randomize,
            render_layered_randomize_context,
        )

        if "_SvObject__svtypes_emit_specialization_class" not in cls.__dict__:
            # Constraints render only on the class that declares them. A
            # specialize() product inherits the template's blocks (written with
            # parameter names) via extends and must not re-declare them;
            # templates and ParamRef-forwarding subclasses render their own.
            lines.extend(render_layered_randomize_context(ind_str, cls.IND))
            lines.extend(render_constraint_blocks(cls, ind_str, cls.IND))
            lines.extend(render_layered_randomize(cls, ind_str, cls.IND))

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}static function svtypes_pkg::encoding_descriptor svtypes_encoding_descriptor();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::encoding_descriptor descriptor;")
        lines.append(
            f'{ind_str}{cls.IND}{cls.IND}descriptor = new({cls._sv_encoding_type_expr()}, '
            f'"{cls._encoding_fingerprint_hex()}", 1);'
        )
        lines.append(f"{ind_str}{cls.IND}{cls.IND}return descriptor;")
        lines.append(f"{ind_str}{cls.IND}endfunction")
        lines.append("")
        lines.append(f"{ind_str}{cls.IND}static function svtypes_pkg::runtime_capabilities svtypes_runtime_capabilities();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}return svtypes_pkg::get_runtime_capabilities();")
        lines.append(f"{ind_str}{cls.IND}endfunction")

        lines.append("")
        lines.append(f'{ind_str}{cls.IND}virtual function void apply_plusargs(string prefix = "");')
        from .bit import Bit
        from .enum import Enum
        from .logic import Logic
        from .real import Real
        from .string import String
        plusarg_declarations = [
            f"{ind_str}{cls.IND}{cls.IND}string __svtypes_key;",
            f"{ind_str}{cls.IND}{cls.IND}bit __svtypes_repeated;",
        ]
        plusarg_body = [
            f"{ind_str}{cls.IND * 2}__svtypes_repeated = svtypes_pkg::begin_plusarg_object(__svtypes_object_number);",
            f"{ind_str}{cls.IND * 2}if (__svtypes_repeated) begin",
            f"{ind_str}{cls.IND * 3}svtypes_pkg::end_plusarg_object();",
            f"{ind_str}{cls.IND * 3}return;",
            f"{ind_str}{cls.IND * 2}end",
        ]
        plusarg_index = 0

        def emit_plusarg(desc, expression, external_path):
            nonlocal plusarg_index
            if isinstance(desc, SvStruct):
                for member_name, member_desc in desc.__svtypes_members:
                    if isinstance(member_desc, TypeBase) and member_desc.plusarg:
                        emit_plusarg(
                            member_desc,
                            f"{expression}.{member_name}",
                            f"{external_path}.{member_name}",
                        )
                return
            if isinstance(desc, Enum):
                suffix = plusarg_index
                plusarg_index += 1
                raw = f"__svtypes_enum_text_{suffix}"
                numeric = f"__svtypes_enum_value_{suffix}"
                plusarg_declarations.append(f"{ind_str}{cls.IND}{cls.IND}string {raw};")
                plusarg_declarations.append(f"{ind_str}{cls.IND}{cls.IND}longint signed {numeric};")
                plusarg_body.append(
                    f'{ind_str}{cls.IND * 2}__svtypes_key = (prefix == "") ? '
                    f'"{external_path}=%s" : {{prefix, ".{external_path}=%s"}};'
                )
                plusarg_body.append(
                    f"{ind_str}{cls.IND * 2}if ($value$plusargs(__svtypes_key, {raw})) begin"
                )
                plusarg_body.append(f"{ind_str}{cls.IND * 3}case ({raw})")
                for member_name in desc.__class__._enum_map:
                    plusarg_body.append(
                        f'{ind_str}{cls.IND * 4}"{member_name}": {expression} = {member_name};'
                    )
                plusarg_body.append(f"{ind_str}{cls.IND * 4}default: begin")
                plusarg_body.append(
                    f'{ind_str}{cls.IND * 5}if ($sscanf({raw}, "%d", {numeric}) != 1) '
                    f'$fatal(2, "Malformed enum plusarg {external_path}=%s", {raw});'
                )
                plusarg_body.append(f"{ind_str}{cls.IND * 5}case ({numeric})")
                for member in desc.__class__._enum_items:
                    plusarg_body.append(
                        f"{ind_str}{cls.IND * 6}{member.value}: {expression} = {desc.__class__.__name__}'({numeric});"
                    )
                plusarg_body.append(
                    f'{ind_str}{cls.IND * 6}default: $fatal(2, "Invalid enum plusarg {external_path}=%s", {raw});'
                )
                plusarg_body.append(f"{ind_str}{cls.IND * 5}endcase")
                plusarg_body.append(f"{ind_str}{cls.IND * 4}end")
                plusarg_body.append(f"{ind_str}{cls.IND * 3}endcase")
                plusarg_body.append(f"{ind_str}{cls.IND * 2}end")
                return
            if isinstance(desc, String):
                value_format = "%s"
            elif isinstance(desc, Real):
                value_format = "%f"
            elif isinstance(desc, Logic):
                value_format = "%h"
            elif isinstance(desc, Bit):
                value_format = "%d" if desc.signed else "%h"
            else:
                return
            plusarg_body.append(
                f'{ind_str}{cls.IND * 2}__svtypes_key = (prefix == "") ? '
                f'"{external_path}={value_format}" : {{prefix, ".{external_path}={value_format}"}};'
            )
            plusarg_body.append(
                f"{ind_str}{cls.IND * 2}if ($test$plusargs((prefix == \"\") ? \"{external_path}\" : "
                f"{{prefix, \".{external_path}\"}}) && !$value$plusargs(__svtypes_key, {expression})) "
                f'$fatal(2, "Malformed plusarg {external_path}");'
            )

        for name, desc in cls.__svtypes_members:
            if isinstance(desc, ObjectDescriptor) or (
                isinstance(desc, SvObject) and not isinstance(desc, SvStruct)
            ):
                nested_prefix = (
                    f'(prefix == "") ? "{name}" : {{prefix, ".{name}"}}'
                )
                plusarg_body.append(
                    f"{ind_str}{cls.IND * 2}if ({name} != null) {name}.apply_plusargs({nested_prefix});"
                )
            elif isinstance(desc, TypeBase) and desc.plusarg:
                emit_plusarg(desc, name, name)
        lines.extend(plusarg_declarations)
        lines.extend(plusarg_body)
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::end_plusarg_object();")
        lines.append(f"{ind_str}{cls.IND}endfunction")

        from .collection import AssocArray, DynArray, Queue, Array
        dump_declarations = []
        dump_body = []
        dump_temp_index = 0

        def emit_dump_value(desc, expression, indent):
            nonlocal dump_temp_index
            if isinstance(desc, (Array, DynArray, Queue)):
                suffix = dump_temp_index
                dump_temp_index += 1
                index = f"__svtypes_index_{suffix}"
                first = f"__svtypes_first_{suffix}"
                dump_declarations.append(f"{ind_str}{cls.IND * 2}bit {first};")
                dump_body.append(f'{indent}result = {{result, "["}};')
                dump_body.append(f"{indent}{first} = 1'b1;")
                dump_body.append(f"{indent}foreach ({expression}[{index}]) begin")
                inner = indent + cls.IND
                dump_body.append(f'{inner}if (!{first}) result = {{result, ", "}};')
                dump_body.append(f"{inner}{first} = 1'b0;")
                emit_dump_value(desc._elem_template, f"{expression}[{index}]", inner)
                dump_body.append(f"{indent}end")
                dump_body.append(f'{indent}result = {{result, "]"}};')
                return
            if isinstance(desc, AssocArray):
                suffix = dump_temp_index
                dump_temp_index += 1
                key = f"__svtypes_key_{suffix}"
                valid = f"__svtypes_valid_{suffix}"
                first = f"__svtypes_first_{suffix}"
                dump_declarations.append(
                    f"{ind_str}{cls.IND * 2}{desc._key_template.sv_decl(key)};"
                )
                dump_declarations.append(f"{ind_str}{cls.IND * 2}bit {valid};")
                dump_declarations.append(f"{ind_str}{cls.IND * 2}bit {first};")
                dump_body.append(f'{indent}result = {{result, "{{"}};')
                dump_body.append(f"{indent}{first} = 1'b1;")
                dump_body.append(f"{indent}{valid} = {expression}.first({key});")
                dump_body.append(f"{indent}while ({valid}) begin")
                inner = indent + cls.IND
                dump_body.append(f'{inner}if (!{first}) result = {{result, ", "}};')
                dump_body.append(f"{inner}{first} = 1'b0;")
                emit_dump_value(desc._key_template, key, inner)
                dump_body.append(f'{inner}result = {{result, ": "}};')
                emit_dump_value(desc._val_template, f"{expression}[{key}]", inner)
                dump_body.append(f"{inner}{valid} = {expression}.next({key});")
                dump_body.append(f"{indent}end")
                dump_body.append(f'{indent}result = {{result, "}}"}};')
                return
            if isinstance(desc, SvStruct):
                dump_body.append(f'{indent}result = {{result, "{desc.__class__.__name__}{{"}};')
                for member_index, (member_name, member_desc) in enumerate(desc.__svtypes_members):
                    prefix = "" if member_index == 0 else ", "
                    dump_body.append(f'{indent}result = {{result, "{prefix}{member_name}="}};')
                    emit_dump_value(member_desc, f"{expression}.{member_name}", indent)
                dump_body.append(f'{indent}result = {{result, "}}"}};')
                return
            if isinstance(desc, ObjectDescriptor) or (
                isinstance(desc, SvObject) and not isinstance(desc, SvStruct)
            ):
                dump_body.append(
                    f'{indent}result = {{result, ({expression} == null ? "null" : {expression}.svtypes_sprint())}};'
                )
                return
            if isinstance(desc, String):
                dump_body.append(
                    f"{indent}result = {{result, svtypes_pkg::escape_dump_string({expression})}};"
                )
                return
            if isinstance(desc, Enum):
                dump_body.append(
                    f'{indent}result = {{result, $sformatf("{desc.__class__.__name__}.%s", {expression}.name())}};'
                )
                return
            from .int import Int, LongInt
            from .remote_ref import RemoteRef
            if isinstance(desc, (Int, LongInt)) or (isinstance(desc, Bit) and desc.signed):
                dump_body.append(f'{indent}result = {{result, $sformatf("%0d", {expression})}};')
                return
            if isinstance(desc, (Bit, Logic)):
                dump_body.append(f'{indent}result = {{result, $sformatf("%0h", {expression})}};')
                return
            if isinstance(desc, Real):
                dump_body.append(f'{indent}result = {{result, $sformatf("%0g", {expression})}};')
                return
            if isinstance(desc, RemoteRef):
                dump_body.append(
                    f'{indent}result = {{result, ({expression}.object_number == 0 ? "null" : '
                    f'$sformatf("RemoteRef(%s,%0d)", {expression}.target_type_name, {expression}.object_number))}};'
                )
                return
            dump_body.append(f'{indent}result = {{result, $sformatf("%p", {expression})}};')

        dump_index = 0
        for name, desc in cls.__svtypes_members:
            if isinstance(desc, TypeBase) and not desc.dump:
                continue
            separator = "" if dump_index == 0 else ", "
            dump_body.append(
                f'{ind_str}{cls.IND * 2}result = {{result, "{separator}{name}="}};'
            )
            emit_dump_value(desc, name, ind_str + cls.IND * 2)
            dump_index += 1

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}virtual function string svtypes_sprint();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}string result;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}bit repeated;")
        lines.extend(dump_declarations)
        lines.append(f"{ind_str}{cls.IND}{cls.IND}repeated = svtypes_pkg::begin_dump_object(__svtypes_object_number);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}if (repeated) begin")
        lines.append(f'{ind_str}{cls.IND * 3}result = $sformatf("<ref#%0d>", __svtypes_object_number);')
        lines.append(f"{ind_str}{cls.IND * 3}svtypes_pkg::end_dump_object();")
        lines.append(f"{ind_str}{cls.IND * 3}return result;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}end")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}result = $sformatf("{cls.__name__}#%0d{{", __svtypes_object_number);')
        lines.extend(dump_body)
        lines.append(f'{ind_str}{cls.IND}{cls.IND}result = {{result, "}}"}};')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::end_dump_object();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}return result;")
        lines.append(f"{ind_str}{cls.IND}endfunction")
        lines.append("")
        lines.append(f"{ind_str}{cls.IND}virtual function void svtypes_display();")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}$display("%s", svtypes_sprint());')
        lines.append(f"{ind_str}{cls.IND}endfunction")

        lines.append("")
        field_count = len(cls.__svtypes_fields)

        # Pack
        lines.append(f"{ind_str}{cls.IND}virtual function void pack(ref byte unsigned bytes[$]);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::begin_pack_graph();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::pack_object_value(this, bytes);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::end_pack_graph();")
        lines.append(f"{ind_str}{cls.IND}endfunction")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}virtual function void pack_body(ref byte unsigned bytes[$]);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}ensure_svtypes_object_number();")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::register_object(this);")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}svtypes_pkg::pack_object_header({cls._sv_encoding_type_expr()}, "{cls._encoding_fingerprint_hex()}", {field_count}, __svtypes_object_number, bytes);')
        for name, desc in cls.__svtypes_fields:
            if desc.pack_bytes:
                from .collection import CollectionBase
                if isinstance(desc, CollectionBase):
                    lines.extend(desc.sv_pack_loop(name, level + 2, ind_str + cls.IND + cls.IND))
                else:
                    lines.extend(cls._sv_pack_lines(name, desc, ind_str + cls.IND + cls.IND))
        lines.append(f"{ind_str}{cls.IND}endfunction")

        lines.append("")
        # Unpack
        lines.append(f"{ind_str}{cls.IND}virtual function void unpack(ref byte unsigned bytes[$], ref int offset);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}byte unsigned present;")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}svtypes_pkg::require_available(bytes, offset, 1, "object presence");')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}present = bytes[offset];")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}offset += 1;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}if (present == 8'h02) begin")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}{cls.IND}$fatal(2, "SvTypes cannot unpack root reference into existing {cls.__name__} object");')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}end")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}if (present != 8'h01) begin")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}{cls.IND}$fatal(2, "SvTypes cannot unpack null into existing {cls.__name__} object");')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}end")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}unpack_body(bytes, offset);")
        lines.append(f"{ind_str}{cls.IND}endfunction")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}longint unsigned incoming_svtypes_object_number;")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}svtypes_pkg::unpack_object_header({cls._sv_encoding_type_expr()}, "{cls._encoding_fingerprint_hex()}", {field_count}, incoming_svtypes_object_number, bytes, offset);')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}__svtypes_object_number = incoming_svtypes_object_number;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes_pkg::register_object(this);")
        for name, desc in cls.__svtypes_fields:
            if desc.pack_bytes:
                from .collection import CollectionBase
                if isinstance(desc, CollectionBase):
                    lines.extend(desc.sv_unpack_loop(name, level + 2, ind_str + cls.IND + cls.IND))
                else:
                    lines.extend(cls._sv_unpack_lines(name, desc, ind_str + cls.IND + cls.IND))
        lines.append(f"{ind_str}{cls.IND}endfunction")

        from .collection import AssocArray, DynArray, Queue
        coverage_fields = []
        for name, desc in cls.__svtypes_members:
            enabled = desc.cov if isinstance(desc, TypeBase) else isinstance(desc, ObjectDescriptor)
            if not enabled:
                continue
            if isinstance(desc, (Bit, Logic, Enum)):
                expression = f"item.{name}"
            elif isinstance(desc, (DynArray, Queue)):
                expression = f"item.{name}.size()"
            elif isinstance(desc, AssocArray):
                expression = f"item.{name}.num()"
            elif isinstance(desc, ObjectDescriptor) or (
                isinstance(desc, SvObject) and not isinstance(desc, SvStruct)
            ):
                expression = f"(item.{name} == null)"
            else:
                continue
            coverage_fields.append((name, expression))
        if coverage_fields:
            # The coverage collector is emitted as a nested class of the
            # generated class, so it can reference the enclosing class
            # parameters and coverpoints always sample the enclosing type.
            sample_type = cls._sv_coverage_sample_type()
            lines.append("")
            lines.append(f"{ind_str}{cls.IND}class {cls.__name__}__svtypes_coverage;")
            lines.append(f"{ind_str}{cls.IND * 2}covergroup cg with function sample({sample_type} item);")
            lines.append(f"{ind_str}{cls.IND * 3}option.per_instance = 1;")
            for name, expression in coverage_fields:
                lines.append(f"{ind_str}{cls.IND * 3}{name}_cp: coverpoint {expression};")
            lines.append(f"{ind_str}{cls.IND * 2}endgroup")
            lines.append("")
            lines.append(f"{ind_str}{cls.IND * 2}function new();")
            lines.append(f"{ind_str}{cls.IND * 3}cg = new();")
            lines.append(f"{ind_str}{cls.IND * 2}endfunction")
            lines.append("")
            lines.append(f"{ind_str}{cls.IND * 2}function void sample({sample_type} item);")
            lines.append(f"{ind_str}{cls.IND * 3}cg.sample(item);")
            lines.append(f"{ind_str}{cls.IND * 2}endfunction")
            lines.append(f"{ind_str}{cls.IND}endclass")

        lines.append(f"{ind_str}endclass")
        return "\n".join(lines)

    @classmethod
    def to_cpp_obj(cls, level=0):
        if "_SvObject__svtypes_emit_specialization_class" in cls.__dict__:
            raise DeclarationError(
                f"{cls.__name__} is a Python-side binding of "
                f"{cls.__svtypes_specialized_from.__name__}; specializations are not generated "
                "classes — use the template with parameters (Tpl<4>) in the target language"
            )
        ind_str = cls.IND * level
        lines = []
        specialized_base = cls._cpp_specialized_base_expr()
        if cls.__svtypes_params:
            from .parameter import cpp_param_literal

            param_parts = []
            for p_name, p_attr in cls.__svtypes_params:
                if p_attr.dtype in ("str", "float"):
                    raise DeclarationError(
                        f"parameter {p_name!r} of type {p_attr.dtype!r} cannot be a generated "
                        "C++ template parameter; bind an int/type parameter or skip C++ generation"
                    )
                decl = p_attr.cpp_decl(p_name).replace("static constexpr ", "")
                if p_attr.is_bound and p_attr.dtype != "type":
                    decl += f" = {cpp_param_literal(p_attr.value)}"
                param_parts.append(decl)
            lines.append(f"{ind_str}template <{', '.join(param_parts)}>")

        if specialized_base is not None:
            base_clause = f" : public {specialized_base}"
        elif cls.__svtypes_base_name:
            base_clause = f" : public {cls.__svtypes_base_name}"
        else:
            base_clause = " : public svtypes::SvObject"
        lines.append(f"{ind_str}struct {cls.__name__}{base_clause} {{")

        local_members = []
        for name, attr in cls.__dict__.items():
            if (
                (isinstance(attr, TypeBase) or isinstance(attr, ObjectDescriptor))
                and not name.startswith('_')
                and name not in {p_name for p_name, _ in cls.__svtypes_params}
            ):
                local_members.append((name, attr))

        for name, desc in local_members:
             lines.append(f"{ind_str}{cls.IND}{desc.cpp_decl(name)};")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}static svtypes::EncodingDescriptor svtypes_encoding_descriptor() {{")
        lines.append(
            f'{ind_str}{cls.IND * 2}return {{{cls._cpp_encoding_type_expr()}, '
            f'"{cls._encoding_fingerprint_hex()}", 1}};'
        )
        lines.append(f"{ind_str}{cls.IND}}}")
        lines.append("")
        lines.append(f"{ind_str}{cls.IND}static svtypes::RuntimeCapabilities svtypes_runtime_capabilities() {{")
        lines.append(f"{ind_str}{cls.IND * 2}return svtypes::runtime_capabilities();")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}std::string svtypes_sprint() const override {{")
        lines.append(f"{ind_str}{cls.IND * 2}if (svtypes::begin_dump_object(__svtypes_object_number)) {{")
        lines.append(f'{ind_str}{cls.IND * 3}svtypes::end_dump_object();')
        lines.append(f'{ind_str}{cls.IND * 3}return "<ref#" + std::to_string(__svtypes_object_number) + ">";')
        lines.append(f"{ind_str}{cls.IND * 2}}}")
        lines.append(f'{ind_str}{cls.IND * 2}std::string result = "{cls.__name__}#" + std::to_string(__svtypes_object_number) + "{{";')
        dump_index = 0
        for name, desc in cls.__svtypes_members:
            if isinstance(desc, TypeBase) and not desc.dump:
                continue
            separator = "" if dump_index == 0 else ", "
            if isinstance(desc, SvStruct):
                dump_expression = f"dump_value({name})"
            elif isinstance(desc, SvObject):
                dump_expression = f"svtypes::dump_value(&{name})"
            else:
                dump_expression = f"svtypes::dump_value({name})"
            lines.append(
                f'{ind_str}{cls.IND * 2}result += "{separator}{name}=" + {dump_expression};'
            )
            dump_index += 1
        lines.append(f'{ind_str}{cls.IND * 2}result += "}}";')
        lines.append(f"{ind_str}{cls.IND * 2}svtypes::end_dump_object();")
        lines.append(f"{ind_str}{cls.IND * 2}return result;")
        lines.append(f"{ind_str}{cls.IND}}}")
        lines.append("")
        lines.append(f"{ind_str}{cls.IND}void svtypes_display() const override {{")
        lines.append(f"{ind_str}{cls.IND * 2}std::cout << svtypes_sprint() << std::endl;")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append("")
        # Pack
        lines.append(f"{ind_str}{cls.IND}void pack(std::vector<uint8_t>& bytes) const override {{")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes::PackOperationGuard __svtypes_pack_operation;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes::pack_object_value(this, bytes);")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}void pack_body(std::vector<uint8_t>& bytes) const override {{")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes::register_object(const_cast<{cls.__name__}*>(this));")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}svtypes::pack_object_header({cls._cpp_encoding_type_expr()}, "{cls._encoding_fingerprint_hex()}", {len(cls.__svtypes_fields)}, __svtypes_object_number, bytes);')
        for name, desc in cls.__svtypes_fields:
            if desc.pack_bytes:
                namespace = "" if isinstance(desc, SvStruct) else "svtypes::"
                lines.append(f"{ind_str}{cls.IND}{cls.IND}{namespace}pack({name}, bytes);")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append("")
        # Unpack
        lines.append(f"{ind_str}{cls.IND}void unpack(const std::vector<uint8_t>& bytes, size_t& offset) override {{")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes::require_available(bytes, offset, 1);")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}uint8_t present = bytes[offset++];")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}if (present == 2) throw std::runtime_error(\"SvTypes cannot unpack root reference into existing {cls.__name__} object\");")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}if (present != 1) throw std::runtime_error(\"SvTypes cannot unpack null into existing {cls.__name__} object\");")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}unpack_body(bytes, offset);")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append("")
        lines.append(f"{ind_str}{cls.IND}void unpack_body(const std::vector<uint8_t>& bytes, size_t& offset) override {{")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}uint64_t incoming_svtypes_object_number;")
        lines.append(f'{ind_str}{cls.IND}{cls.IND}svtypes::unpack_object_header({cls._cpp_encoding_type_expr()}, "{cls._encoding_fingerprint_hex()}", {len(cls.__svtypes_fields)}, incoming_svtypes_object_number, bytes, offset);')
        lines.append(f"{ind_str}{cls.IND}{cls.IND}__svtypes_object_number = incoming_svtypes_object_number;")
        lines.append(f"{ind_str}{cls.IND}{cls.IND}svtypes::register_object(this);")
        for name, desc in cls.__svtypes_fields:
            if desc.pack_bytes:
                namespace = "" if isinstance(desc, SvStruct) else "svtypes::"
                lines.append(f"{ind_str}{cls.IND}{cls.IND}{namespace}unpack({name}, bytes, offset);")
        lines.append(f"{ind_str}{cls.IND}}}")

        lines.append(f"{ind_str}}};")
        return "\n".join(lines)

    def sv_decl(self, name: str):
        return f"{SvObject._sv_type_expr(self)} {name}"

    def to_sv_code(self, level=0, name: str | None = None):
        name = name or self._attr_name
        return f"{self.IND * level}{self.sv_decl(name)};"

    def cpp_decl(self, name: str):
        if self.__class__.__svtypes_specialized_from is not None:
            return f"{self.__class__._cpp_specialized_base_expr()} {name}"
        return f"{self.__class__.__name__} {name}"

    @staticmethod
    def _pack_field_value(desc: Any, value: Any, ctx: _PackContext) -> bytes:
        from .collection import AssocArray, DynArray, Queue, Array

        if isinstance(desc, ObjectDescriptor):
            cls = desc.registry.get(desc.cls_name)
            if cls is None:
                raise ValueError(
                    f"Class name '{desc.cls_name}' not found in registry. "
                    f"Available types: {desc.registry.list_types()}"
                )
            if value is not None:
                return value.pack(value, ctx)
            return cls(session=ctx.session, _defer_identity=True).pack(None, ctx)
        if isinstance(desc, SvStruct):
            return desc.pack(value)
        if isinstance(desc, SvObject):
            return desc.pack(value, ctx)
        if isinstance(desc, Array):
            if len(value) != desc._size:
                raise ValueError(f"Expected list of size {desc._size}")
            data = b""
            for item in value:
                data += SvObject._pack_field_value(desc._elem_template, item, ctx)
            return data
        if isinstance(desc, Queue) or isinstance(desc, DynArray):
            if len(value) > desc._max_length:
                raise EncodeError(
                    f"{desc.__class__.__name__} length {len(value)} exceeds encoder limit {desc._max_length}"
                )
            data = struct.pack("<I", len(value))
            for item in value:
                data += SvObject._pack_field_value(desc._elem_template, item, ctx)
            return data
        if isinstance(desc, AssocArray):
            if len(value) > desc._max_length:
                raise EncodeError(
                    f"AssocArray length {len(value)} exceeds encoder limit {desc._max_length}"
                )
            data = struct.pack("<I", len(value))
            keys = sorted(value.keys(), key=desc._key_template.pack)
            for key in keys:
                data += SvObject._pack_field_value(desc._key_template, key, ctx)
                data += SvObject._pack_field_value(desc._val_template, value[key], ctx)
            return data
        return desc.pack(value)

    @staticmethod
    def _unpack_field_value(desc: Any, bytes_: bytes, ctx: _UnpackContext) -> tuple[Any, int]:
        from .collection import AssocArray, DynArray, Queue, Array

        if isinstance(desc, ObjectDescriptor):
            cls = desc.registry.get(desc.cls_name)
            if cls is None:
                raise ValueError(
                    f"Class name '{desc.cls_name}' not found in registry. "
                    f"Available types: {desc.registry.list_types()}"
                )
            return cls(session=ctx.session, _defer_identity=True).unpack(bytes_, ctx)
        if isinstance(desc, SvStruct):
            return desc.unpack(bytes_)
        if isinstance(desc, SvObject):
            return desc.unpack(bytes_, ctx)
        if isinstance(desc, Array):
            offset = 0
            values = []
            for _ in range(desc._size):
                value, count = SvObject._unpack_field_value(desc._elem_template, bytes_[offset:], ctx)
                values.append(value)
                offset += count
            return values, offset
        if isinstance(desc, Queue) or isinstance(desc, DynArray):
            if len(bytes_) < desc.DYN_INFO_BYTES:
                raise ValueError(
                    f"Not enough bytes to unpack {desc.__class__.__name__} length: "
                    f"need {desc.DYN_INFO_BYTES}, got {len(bytes_)}"
                )
            length = struct.unpack("<I", bytes_[:4])[0]
            limit = min(desc._max_length, ctx.limits.max_dynamic_length)
            if length > limit:
                raise ResourceLimitError(
                    f"{desc.__class__.__name__} length {length} exceeds decoder limit {limit}"
                )
            offset = 4
            values = []
            for _ in range(length):
                value, count = SvObject._unpack_field_value(desc._elem_template, bytes_[offset:], ctx)
                values.append(value)
                offset += count
            return values, offset
        if isinstance(desc, AssocArray):
            if len(bytes_) < desc.DYN_INFO_BYTES:
                raise ValueError(
                    f"Not enough bytes to unpack AssocArray length: "
                    f"need {desc.DYN_INFO_BYTES}, got {len(bytes_)}"
                )
            length = struct.unpack("<I", bytes_[:4])[0]
            limit = min(desc._max_length, ctx.limits.max_dynamic_length)
            if length > limit:
                raise ResourceLimitError(
                    f"AssocArray length {length} exceeds decoder limit {limit}"
                )
            offset = 4
            values = {}
            for _ in range(length):
                key, count = SvObject._unpack_field_value(desc._key_template, bytes_[offset:], ctx)
                offset += count
                value, count = SvObject._unpack_field_value(desc._val_template, bytes_[offset:], ctx)
                offset += count
                values[key] = value
            return values, offset
        return desc.unpack(bytes_)

    @staticmethod
    def _assign_unpacked_field(obj: "SvObject", name: str, desc: Any, value: Any) -> None:
        from .collection import AssocArray, DynArray, Queue, Array

        if isinstance(desc, ObjectDescriptor):
            setattr(obj, name, value)
            return
        if isinstance(desc, SvObject):
            object.__setattr__(obj, desc._storage_key, value)
            return
        field = getattr(obj, name)
        if isinstance(desc, Array):
            for index, item in enumerate(value):
                elem_desc = desc._elem_template
                if isinstance(elem_desc, ObjectDescriptor):
                    field._elements[index] = item
                elif isinstance(elem_desc, SvObject):
                    field._elements[index] = item
                else:
                    field._elements[index].value = item
            return
        if isinstance(desc, Queue) or isinstance(desc, DynArray):
            field._elements = []
            for item in value:
                if isinstance(desc._elem_template, ObjectDescriptor):
                    field._elements.append(item)
                elif isinstance(desc._elem_template, SvObject):
                    field._elements.append(item)
                else:
                    elem = copy.deepcopy(desc._elem_template)
                    elem.value = item
                    field._elements.append(elem)
            return
        if isinstance(desc, AssocArray):
            field._elements = {}
            for key, item in value.items():
                if isinstance(desc._val_template, SvObject):
                    field._elements[key] = item
                else:
                    elem = copy.deepcopy(desc._val_template)
                    elem.value = item
                    field._elements[key] = elem
            return
        field.value = value

    def pack(self, value, ctx: PackContext | None = None):
        if ctx is None:
            ctx = PackContext(value.codec_session if isinstance(value, SvObject) else self.codec_session)
        if value is None:
            return b"\x00"
        if not isinstance(value, self.__class__):
             raise TypeError(f"Expected {self.__class__.__name__}, got {type(value)}")

        if id(value) in ctx.seen:
            return b"\x02" + struct.pack("<Q", value.svtypes_object_number)
        ctx.seen[id(value)] = value.svtypes_object_number

        b = b'\x01' + self._pack_object_header(
            self.__class__._encoding_type_name(),
            bytes.fromhex(self.__class__._encoding_fingerprint_hex()),
            len(self.__svtypes_fields),
            value.svtypes_object_number,
        )
        for name, desc in self.__svtypes_fields:
            if isinstance(desc, ObjectDescriptor):
                b += self._pack_field_value(desc, getattr(value, name), ctx)
            else:
                val_obj = getattr(value, name)
                b += self._pack_field_value(desc, val_obj.value, ctx)
        return b

    def unpack(
        self,
        bytes_: bytes,
        ctx: UnpackContext | None = None,
        *,
        _target: "SvObject" | None = None,
    ):
        if ctx is None:
            ctx = UnpackContext(self.codec_session)
        root = ctx._begin(len(bytes_))
        succeeded = False
        try:
            result = self._unpack_transaction(bytes_, ctx, _target=_target)
            succeeded = True
            return result
        finally:
            ctx._finish(root, succeeded)

    def _unpack_transaction(
        self,
        bytes_: bytes,
        ctx: UnpackContext,
        *,
        _target: "SvObject" | None = None,
    ):
        offset = 0
        if len(bytes_) < 1:
            raise ValueError("Not enough bytes to unpack object presence")
        present = bytes_[offset]
        offset += 1
        if present == 0:
            return None, offset
        if present == 2:
            if len(bytes_) < offset + 8:
                raise ValueError("Not enough bytes to unpack object reference")
            object_number = struct.unpack("<Q", bytes_[offset:offset + 8])[0]
            if object_number == 0:
                raise ValueError("SvTypes object reference has id 0")
            ref_obj = ctx.objects.get(object_number) or ctx.session.get(object_number)
            if ref_obj is None:
                raise ValueError(f"SvTypes unresolved object reference id {object_number}")
            if not isinstance(ref_obj, self.__class__):
                raise TypeError(
                    f"SvTypes object reference type mismatch: "
                    f"expected {self.__class__.__name__}, got {ref_obj.__class__.__name__}"
                )
            return ref_obj, offset + 8
        if present != 1:
            raise ValueError(f"Invalid object presence marker: {present}")

        header_offset, object_number = self._unpack_object_header(
            bytes_,
            offset,
            self.__class__._encoding_type_name(),
            bytes.fromhex(self.__class__._encoding_fingerprint_hex()),
            len(self.__svtypes_fields),
        )
        ctx._record_inline_object(object_number)
        existing = ctx.objects.get(object_number) or ctx.session.get(object_number)
        if _target is not None:
            old_id = _target.svtypes_object_number
            if existing is not None and existing is not _target:
                if not isinstance(existing, self.__class__):
                    raise RegistryError(
                        f"SvTypes object number collision for {object_number}: target is {_target.__class__.__name__}, "
                        f"registered object is {existing.__class__.__name__}"
                    )
                ctx._replaceable_bindings[object_number] = existing
            new_inst = _target
        elif existing is not None:
            if not isinstance(existing, self.__class__):
                raise TypeError(
                    f"SvTypes object number collision for {object_number}: "
                    f"expected {self.__class__.__name__}, got {existing.__class__.__name__}"
                )
            new_inst = existing
        else:
            new_inst = self.__class__(session=ctx.session, _defer_identity=True)
            old_id = 0
        if _target is None and existing is not None:
            old_id = existing.svtypes_object_number
        ctx.objects[object_number] = new_inst

        offset = header_offset
        fields = []
        for name, desc in self.__svtypes_fields:
            val, count = self._unpack_field_value(desc, bytes_[offset:], ctx)
            offset += count
            fields.append((name, desc, val))

        ctx._stage(new_inst, object_number, old_id, fields)

        return new_inst, offset

    @staticmethod
    def _pack_object_header(type_name: str, encoding_fingerprint: bytes, field_count: int, object_number: int) -> bytes:
        if len(encoding_fingerprint) != 32:
            raise ValueError("SvTypes encoding fingerprint must contain 32 bytes")
        encoded_name = type_name.encode()
        return (
            b"SVXO"
            + struct.pack("<H", 2)
            + struct.pack("<H", field_count)
            + struct.pack("<Q", object_number)
            + struct.pack("<I", len(encoded_name))
            + encoded_name
            + encoding_fingerprint
        )

    def _unpack_object_header(
        self,
        bytes_: bytes,
        offset: int,
        expected_type_name: str,
        expected_encoding_fingerprint: bytes,
        expected_field_count: int,
    ) -> tuple[int, int]:
        if len(bytes_) < offset + 20:
            raise ValueError("Not enough bytes to unpack object header")
        if bytes_[offset:offset + 4] != b"SVXO":
            raise ValueError("SvTypes object header magic mismatch")
        version = struct.unpack("<H", bytes_[offset + 4:offset + 6])[0]
        if version != 2:
            raise ValueError(f"SvTypes object header version mismatch: {version}")
        field_count = struct.unpack("<H", bytes_[offset + 6:offset + 8])[0]
        object_number = struct.unpack("<Q", bytes_[offset + 8:offset + 16])[0]
        if object_number == 0:
            raise ValueError("SvTypes non-null object envelope has id 0")
        name_len = struct.unpack("<I", bytes_[offset + 16:offset + 20])[0]
        offset += 20
        if len(bytes_) < offset + name_len:
            raise ValueError("Not enough bytes to unpack object type name")
        type_name = bytes_[offset:offset + name_len].decode()
        offset += name_len
        if type_name != expected_type_name:
            raise ValueError(
                f"SvTypes object type mismatch: expected {expected_type_name}, got {type_name}"
            )
        if field_count != expected_field_count:
            raise ValueError(
                f"SvTypes object field-count mismatch for {expected_type_name}: "
                f"expected {expected_field_count}, got {field_count}"
            )
        if len(bytes_) < offset + 32:
            raise ValueError("Not enough bytes to unpack object encoding fingerprint")
        encoding_fingerprint = bytes_[offset:offset + 32]
        if encoding_fingerprint != expected_encoding_fingerprint:
            raise ValueError(
                f"SvTypes object encoding fingerprint mismatch for {expected_type_name}: "
                f"expected {expected_encoding_fingerprint.hex()}, got {encoding_fingerprint.hex()}"
            )
        return offset + 32, object_number


class SvStruct(SvObject):
    """Pure by-value packed composite with no object numberentity or graph envelope."""

    def __init__(self, **kwargs) -> None:
        TypeBase.__init__(self, **kwargs)

    @property
    def svtypes_object_number(self) -> int:
        raise AttributeError("SvStruct values do not have object numbers")

    @property
    def codec_session(self) -> CodecSession:
        raise AttributeError("SvStruct values do not participate in codec sessions")

    @property
    def value(self) -> "SvStruct":
        return self

    @value.setter
    def value(self, val: "SvStruct") -> None:
        if not isinstance(val, self.__class__):
            raise TypeError(f"Expected {self.__class__.__name__}, got {type(val)}")
        for name, desc in self._SvObject__svtypes_members:
            source = getattr(val, name)
            target = getattr(self, name)
            if isinstance(desc, SvStruct):
                target.value = source
            else:
                target.value = copy.deepcopy(source.value)

    def pack(self, value: "SvStruct") -> bytes:
        if not isinstance(value, self.__class__):
            raise TypeError(f"Expected {self.__class__.__name__}, got {type(value)}")
        output = bytearray()
        for name, desc in self._SvObject__svtypes_members:
            member = getattr(value, name)
            output.extend(desc.pack(member if isinstance(desc, SvStruct) else member.value))
        return bytes(output)

    def unpack(self, bytes_: bytes) -> tuple["SvStruct", int]:
        result = self.__class__()
        offset = 0
        for name, desc in self._SvObject__svtypes_members:
            value, count = desc.unpack(bytes_[offset:])
            offset += count
            if isinstance(desc, SvStruct):
                object.__setattr__(result, desc._storage_key, value)
            else:
                getattr(result, name).value = value
        return result, offset

    def from_bytes(self, bytes_: bytes) -> int:
        decoded, count = self.unpack(bytes_)
        self.value = decoded
        return count

    @classmethod
    def to_sv_obj(cls, level=0):
        ind_str = cls.IND * level
        lines = [f"{ind_str}typedef struct packed {{"]
        for name, desc in cls._SvObject__svtypes_members:
             lines.append(desc.to_sv_code(level + 1, name=name))
        lines.append(f"{ind_str}}} {cls.__name__}_t;")
        lines.append("")
        lines.append(f"{ind_str}class {cls.__name__}_packer;")
        lines.append(f"{ind_str}{cls.IND}static function void pack(input {cls.__name__}_t value, ref byte unsigned bytes[$]);")
        for name, desc in cls._SvObject__svtypes_members:
            from .collection import CollectionBase
            if isinstance(desc, CollectionBase):
                lines.extend(desc.sv_pack_loop(f"value.{name}", level + 2, ind_str + cls.IND * 2))
            else:
                lines.extend(cls._sv_pack_lines(f"value.{name}", desc, ind_str + cls.IND * 2))
        lines.append(f"{ind_str}{cls.IND}endfunction")
        lines.append("")
        lines.append(f"{ind_str}{cls.IND}static function void unpack(ref {cls.__name__}_t value, ref byte unsigned bytes[$], ref int offset);")
        for name, desc in cls._SvObject__svtypes_members:
            lines.append(f"{ind_str}{cls.IND * 2}{desc.sv_decl(f'__svtypes_{name}')};")
        for name, desc in cls._SvObject__svtypes_members:
            from .collection import CollectionBase
            temporary = f"__svtypes_{name}"
            if isinstance(desc, CollectionBase):
                lines.extend(desc.sv_unpack_loop(temporary, level + 2, ind_str + cls.IND * 2))
            else:
                lines.extend(cls._sv_unpack_lines(temporary, desc, ind_str + cls.IND * 2))
            lines.append(f"{ind_str}{cls.IND * 2}value.{name} = {temporary};")
        lines.append(f"{ind_str}{cls.IND}endfunction")
        lines.append(f"{ind_str}endclass")
        return "\n".join(lines)

    def sv_decl(self, name: str):
        return f"{self.__class__.__name__}_t {name}"

    def cpp_decl(self, name: str):
        return f"{self.__class__.__name__} {name}"

    @classmethod
    def to_cpp_obj(cls, level=0):
        ind_str = cls.IND * level
        lines = [f"{ind_str}struct {cls.__name__} {{"]
        for name, desc in cls._SvObject__svtypes_members:
            lines.append(f"{ind_str}{cls.IND}{desc.cpp_decl(name)};")
        lines.append(f"{ind_str}}};")
        lines.append("")
        lines.append(f"{ind_str}inline void pack(const {cls.__name__}& value, std::vector<uint8_t>& bytes) {{")
        for name, desc in cls._SvObject__svtypes_members:
            namespace = "" if isinstance(desc, SvStruct) else "svtypes::"
            lines.append(f"{ind_str}{cls.IND}{namespace}pack(value.{name}, bytes);")
        lines.append(f"{ind_str}}}")
        lines.append("")
        lines.append(f"{ind_str}inline void unpack({cls.__name__}& value, const std::vector<uint8_t>& bytes, size_t& offset) {{")
        for name, desc in cls._SvObject__svtypes_members:
            namespace = "" if isinstance(desc, SvStruct) else "svtypes::"
            lines.append(f"{ind_str}{cls.IND}{namespace}unpack(value.{name}, bytes, offset);")
        lines.append(f"{ind_str}}}")
        lines.append("")
        lines.append(f"{ind_str}inline std::string dump_value(const {cls.__name__}& value) {{")
        lines.append(f'{ind_str}{cls.IND}std::string result = "{cls.__name__}{{";')
        for index, (name, desc) in enumerate(cls._SvObject__svtypes_members):
            separator = "" if index == 0 else ", "
            namespace = "" if isinstance(desc, SvStruct) else "svtypes::"
            lines.append(
                f'{ind_str}{cls.IND}result += "{separator}{name}=" + '
                f'{namespace}dump_value(value.{name});'
            )
        lines.append(f'{ind_str}{cls.IND}return result + "}}";')
        lines.append(f"{ind_str}}}")
        return "\n".join(lines)


@overload
def svobj(cls: None = None, *, name: str | None = None, registry: ObjectRegistry | None = None) -> Callable[[type[T]], type[T]]: ...

@overload
def svobj(cls: type[T], *, name: str | None = None, registry: ObjectRegistry | None = None) -> type[T]: ...

def svobj(cls: type[T] | None = None, *, name: str | None = None, registry: ObjectRegistry | None = None) -> type[T] | Callable[[type[T]], type[T]]:
    from .scope import get_package
    import sys
    def decorator(cls: type[T]) -> type[T]:
        if registry is not None:
            reg = registry
        else:
            mod = sys.modules.get(cls.__module__)
            full_pkg_name = (mod.__package__ if mod else None) or "$unit"
            pkg_name = full_pkg_name.split('.')[-1] if full_pkg_name else "$unit"
            reg = get_package(pkg_name)
        reg.register(cls, name)
        return cls
    if cls is not None:
        return decorator(cls)
    return decorator


def Object(
    cls_name: str,
    registry: ObjectRegistry | None = None,
    strict_set: bool = False,
    rand: bool = False,
) -> Any:
    """Declare a class handle.

    ``rand=True`` makes a non-null, already allocated handle an enabled random
    object member of its containing object.  It does not randomize the handle
    value itself and does not allocate a null handle.
    """
    return ObjectDescriptor(cls_name, registry=registry, strict_set=strict_set, rand=rand)


def new(cls_name: str, *args, registry: ObjectRegistry | None = None, **kwargs) -> Any:
    reg = registry or _default_registry
    cls = reg.get(cls_name)
    if cls is not None:
        return cls(*args, **kwargs)
    else:
        raise ValueError(f"Class name '{cls_name}' not found in registry. Available types: {reg.list_types()}")
