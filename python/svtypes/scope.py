from __future__ import annotations
import sys
from typing import TYPE_CHECKING
from .object import ObjectRegistry
from .errors import DeclarationError

if TYPE_CHECKING:
    from .enum import Enum
    from .object import SvObject

class Scope(ObjectRegistry):
    INDENT_STEP = 2
    IND = ' ' * INDENT_STEP

    def __init__(self, name: str, parent: Scope | None = None):
        # Use object.__setattr__ to avoid recursion and premature access in __setattr__
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'parent', parent)
        object.__setattr__(self, '_subscopes', {})
        object.__setattr__(self, '_params', {})
        super().__init__()

    def __getattribute__(self, name):
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            params = object.__getattribute__(self, '_params')
            if name in params:
                return params[name]
            raise

    def __setattr__(self, name, value):
        from .parameter import Parameter
        from .base import TypeBase
        if name in self.__dict__:
            attr = self.__dict__[name]
            if isinstance(attr, Parameter):
                raise AttributeError(f"Direct assignment to '{name}' is disabled. Use '{name}.value = ...' instead.")
        if name in self._params:
            attr = self._params[name]
            if isinstance(attr, Parameter):
                raise AttributeError(f"Direct assignment to '{name}' is disabled. Use '{name}.value = ...' instead.")
        if isinstance(value, TypeBase) and not isinstance(value, Parameter):
            for policy in ("rand", "plusarg", "dump", "cov"):
                if getattr(value.field_options, policy) is True:
                    raise DeclarationError(
                        f"{policy}=True is invalid for package/scope variable {self.path}.{name}"
                    )
        super().__setattr__(name, value)

    def scope(self, name: str) -> Scope:
        if name not in self._subscopes:
            self._subscopes[name] = Scope(name, self)
        return self._subscopes[name]

    @property
    def path(self) -> str:
        if self.parent:
            return f"{self.parent.path}::{self.name}"
        return self.name

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.path}>"

    def add_parameter(self, name: str, param):
        self._params[name] = param

    def collect_module(self, module_name: str):
        """Collect all parameters and types from a module."""
        import sys
        from .parameter import Parameter
        from .object import SvObject, SvStruct

        module = sys.modules.get(module_name)
        if not module:
             return

        for name, attr in module.__dict__.items():
            if name.startswith('_'): continue
            if isinstance(attr, Parameter):
                print('parameter:', name)
                self.add_parameter(name, attr)
            elif attr is SvStruct or attr is SvObject:
                continue
            elif isinstance(attr, type) and issubclass(attr, SvObject):
                print('attribute:', name)
                self.register(attr)
            elif isinstance(attr, type):
                full_paths = attr.__module__.split('.')
                if not (len(full_paths) >= 2 and full_paths[-2] == 'svtypes'):
                    print('udc:', name)
                    self.register(attr)

    def clear(self) -> None:
        super().clear()
        self._subscopes.clear()
        self._params.clear()

    def _special_sv_code(self, level=0):
        ind_str = self.IND * level
        return [
            f'{ind_str}`ifdef SYNTAX_TEST\n'
            f'{ind_str}`include "svtypes_pkg.sv"\n'
            f'{ind_str}`endif'
        ]

    def _ordered_types(self):
        """Return a deterministic dependency-before-user registration order."""
        from .collection import CollectionBase
        from .object import SvObject, SvStruct

        registered = set(self._types.values())

        def member_dependencies(desc):
            if isinstance(desc, CollectionBase):
                dependencies = []
                if hasattr(desc, "_elem_template"):
                    dependencies.extend(member_dependencies(desc._elem_template))
                if hasattr(desc, "_key_template"):
                    dependencies.extend(member_dependencies(desc._key_template))
                if hasattr(desc, "_val_template"):
                    dependencies.extend(member_dependencies(desc._val_template))
                return dependencies
            if isinstance(desc, SvStruct):
                return [desc.__class__]
            if isinstance(desc, SvObject):
                return [desc.__class__]
            return []

        dependencies = {}
        for cls in registered:
            required = set()
            if issubclass(cls, SvObject):
                base = cls.__bases__[0] if cls.__bases__ else None
                if base in registered and base not in (SvObject, SvStruct):
                    required.add(base)
                for _, member in cls._SvObject__svtypes_members:
                    required.update(dep for dep in member_dependencies(member) if dep in registered and dep is not cls)
            dependencies[cls] = required

        ordered = []
        pending = set(registered)
        while pending:
            ready = sorted(
                (cls for cls in pending if not (dependencies[cls] & pending)),
                key=lambda cls: (cls.__module__, cls.__qualname__),
            )
            if not ready:
                names = ", ".join(sorted(cls.__name__ for cls in pending))
                raise DeclarationError(f"generated value-type dependency cycle: {names}")
            ordered.extend(ready)
            pending.difference_update(ready)
        return ordered

    def to_sv_pkg(self, level=0):
        from .enum import Enum
        from .object import SvObject, SvStruct
        from .parameter import Parameter
        from .base import TypeBase
        ind_str = self.IND * level

        is_unit = self.name == "$unit"
        lines = []
        lines += self._special_sv_code(level=level)
        if not is_unit:
            lines.append(f"{ind_str}package {self.name};")
            lines.append(f"{ind_str}{self.IND}timeunit 1ns;")
            lines.append(f"{ind_str}{self.IND}timeprecision 1ps;")
            content_level = level + 1
        else:
            content_level = level
        lines.append(f'{ind_str}  import svtypes_pkg::*;')

        # Parameters and Variables from __dict__
        for name, attr in sorted(self.__dict__.items()):
            if name.startswith('_'): continue
            if isinstance(attr, Parameter):
                lines.append(attr.to_sv_code(content_level, name=name))
            elif isinstance(attr, TypeBase):
                lines.append(f"{self.IND * content_level}{attr.sv_decl(name)};")

        # Parameters and Variables from _params
        for name, attr in sorted(self._params.items()):
            if isinstance(attr, Parameter):
                lines.append(attr.to_sv_code(content_level, name=name))
            elif isinstance(attr, TypeBase):
                lines.append(f"{self.IND * content_level}{attr.sv_decl(name)};")

        # Enums
        ordered_types = self._ordered_types()
        for cls in ordered_types:
            if issubclass(cls, Enum):
                lines.append(cls.to_sv_enum(content_level))

        for cls in ordered_types:
            if issubclass(cls, SvObject) and not issubclass(cls, SvStruct):
                lines.append(f'{ind_str}  typedef class {cls.__name__};')

        # Objects
        for cls in ordered_types:
            if issubclass(cls, SvObject):
                lines.append(cls.to_sv_obj(content_level))

        if not is_unit:
            lines.append(f"{ind_str}endpackage")

        return "\n\n".join(lines)

    def to_cpp_pkg(self, level=0):
        from .enum import Enum
        from .object import SvObject
        from .parameter import Parameter
        from .base import TypeBase
        ind_str = self.IND * level

        is_unit = self.name == "$unit"
        lines = []
        if not is_unit:
            lines.append(f"{ind_str}namespace {self.name} {{")
            content_level = level + 1
        else:
            content_level = level

        # Parameters and Variables
        all_params = {**self._params, **{k:v for k,v in self.__dict__.items() if not k.startswith('_')}}
        content_ind = self.IND * content_level
        for name, attr in sorted(all_params.items()):
            if isinstance(attr, Parameter):
                 val = attr.value
                 if isinstance(val, str):
                     val = f'"{val}"'
                 lines.append(f"{content_ind}{attr.cpp_decl(name)} = {val};")
            elif isinstance(attr, TypeBase):
                 lines.append(f"{content_ind}{attr.cpp_decl(name)};")

        # Enums
        ordered_types = self._ordered_types()
        for cls in ordered_types:
            if issubclass(cls, Enum):
                lines.append(cls.to_cpp_enum(content_level))

        for cls in ordered_types:
            if issubclass(cls, SvObject):
                if getattr(cls, "_SvObject__svtypes_params", []) and cls._SvObject__svtypes_specialized_from is None:
                    template_params = ", ".join(
                        parameter.cpp_decl(name).replace("static constexpr ", "")
                        for name, parameter in getattr(cls, "_SvObject__svtypes_params", [])
                    )
                    lines.append(f"{content_ind}template <{template_params}> struct {cls.__name__};")
                    continue
                lines.append(f"{content_ind}struct {cls.__name__};")

        # Objects
        for cls in ordered_types:
            if issubclass(cls, SvObject):
                lines.append(cls.to_cpp_obj(content_level))

        # Nested scopes
        for name, scope in sorted(self._subscopes.items()):
            lines.append(scope.to_cpp_pkg(content_level))

        if not is_unit:
            lines.append(f"{ind_str}}}")

        return "\n\n".join(lines)

_packages: dict[str, Package] = {}

def get_package(name: str) -> Package:
    if name not in _packages:
        _packages[name] = Package(name)
    return _packages[name]

class Package(Scope):
    """SystemVerilog Package"""
    pass


class Namespace(Scope):
    """Generic C++/Python namespace scope."""
    pass
