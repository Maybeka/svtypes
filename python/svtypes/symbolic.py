"""Template-only descriptors for parameter-dependent packed layouts."""

from __future__ import annotations

from types import FunctionType
from typing import Any

from .base import TypeBase
from .errors import DeclarationError
from .parameter_expr import ParameterExpr


def _is_symbolic(value: Any) -> bool:
    from .parameter import Parameter

    return isinstance(value, (Parameter, ParameterExpr, FunctionType)) or (
        isinstance(value, tuple) and any(_is_symbolic(item) for item in value)
    ) or (
        hasattr(value, "parameters") and not isinstance(value, ParameterExpr)
        and any(_is_symbolic(item) for item in value.parameters)
    )


def has_symbolic_layout(value: Any) -> bool:
    return _is_symbolic(value)


class SymbolicPackedField(TypeBase):
    """A packed field whose shape becomes concrete during specialization."""

    def __init__(
        self,
        factory: type,
        shape: Any,
        *,
        signed: bool,
        declaration_style: str | None,
        values: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        field_kwargs = dict(kwargs)
        field_kwargs.pop("_sv_declaration_style", None)
        super().__init__(**field_kwargs)
        self.factory = factory
        self.shape_spec = self._normalize_shape(shape)
        self.signed = signed
        self.declaration_style = declaration_style
        self.values = values
        self.constructor_kwargs = field_kwargs

    @staticmethod
    def _normalize_shape(shape: Any) -> Any:
        if isinstance(shape, FunctionType):
            return ParameterExpr.parse(shape)
        if isinstance(shape, tuple):
            return tuple(SymbolicPackedField._normalize_shape(item) for item in shape)
        return shape

    @staticmethod
    def _parameter_values(cls: type) -> dict[str, int]:
        from .parameter import Parameter

        result: dict[str, int] = {}
        pending: dict[str, Parameter] = {}
        for base in reversed(cls.__mro__):
            for name, attr in base.__dict__.items():
                if not isinstance(attr, Parameter):
                    continue
                if attr.value is not None and isinstance(attr.value, int):
                    result[name] = attr.value
                elif attr.expression is not None:
                    pending[name] = attr
        while pending:
            ready = [
                name for name, parameter in pending.items()
                if set(parameter.expression.parameters) <= set(result)
            ]
            if not ready:
                raise DeclarationError(
                    f"{cls.__name__} has unresolved parameter expression(s): "
                    f"{', '.join(sorted(pending))}"
                )
            for name in ready:
                result[name] = pending.pop(name).expression.evaluate(result)
        return result

    @staticmethod
    def _render_item(item: Any) -> str:
        from .parameter import Parameter

        if isinstance(item, Parameter):
            if not item._attr_name:
                raise DeclarationError("symbolic layout parameter is not attached to a class")
            return item._attr_name
        if isinstance(item, ParameterExpr):
            return item.render()
        return str(item)

    def _render_dimensions(self) -> tuple[str, ...]:
        values = self.shape_spec if isinstance(self.shape_spec, tuple) else (self.shape_spec,)
        return tuple(self._render_item(item) for item in values)

    @property
    def rand(self) -> bool:
        """Preserve the packed factory's default random qualification.

        A symbolic ``Bit[WIDTH]`` is still a ``Bit`` field; leaving the
        default as :class:`TypeBase`'s ``False`` made the template behave
        differently from an equivalent materialized ``Bit[8]`` field.
        """

        if self.field_options.rand is not None:
            return bool(self.field_options.rand)
        return bool(getattr(self.factory, "_default_rand", False))

    def _resolve_item(self, item: Any, values: dict[str, int]) -> int:
        from .parameter import Parameter

        if isinstance(item, Parameter):
            name = item._attr_name
            if not name or name not in values:
                raise DeclarationError("symbolic layout refers to an unbound parameter")
            return values[name]
        if isinstance(item, ParameterExpr):
            return item.evaluate(values)
        if isinstance(item, bool) or not isinstance(item, int):
            raise DeclarationError(f"symbolic layout resolved to non-integer {item!r}")
        return item

    def resolve(self, cls: type) -> Any:
        values = self._parameter_values(cls)
        dimensions = self.shape_spec if isinstance(self.shape_spec, tuple) else (self.shape_spec,)
        concrete_dimensions = tuple(self._resolve_item(item, values) for item in dimensions)
        if any(dimension <= 0 for dimension in concrete_dimensions):
            raise DeclarationError(
                f"{cls.__name__}.{self._attr_name} resolves to non-positive packed dimension "
                f"{concrete_dimensions!r}"
            )
        shape: int | tuple[int, ...] = concrete_dimensions[0] if len(concrete_dimensions) == 1 else concrete_dimensions
        kwargs = dict(self.constructor_kwargs)
        if self.declaration_style is not None:
            kwargs["declaration_style"] = self.declaration_style
        return self.factory._from_layout(
            shape,
            self.values[0] if self.values else None,
            signed=self.signed,
            **kwargs,
        )

    def sv_decl(self, name: str) -> str:
        dimensions = "".join(f" [({item}) - 1:0]" for item in self._render_dimensions())
        if self.factory.__name__ == "Bit":
            kind = "bit"
        else:
            kind = self.declaration_style or "logic"
        return f"{kind}{' signed' if self.signed else ''}{dimensions} {name}"

    def cpp_decl(self, name: str) -> str:
        dimensions = self._render_dimensions()
        width = dimensions[0] if len(dimensions) == 1 else " * ".join(f"({item})" for item in dimensions)
        signed = "true" if self.signed else "false"
        kind = "BitValue" if self.factory.__name__ == "Bit" else "LogicValue"
        return f"svtypes::{kind}<{width}, {signed}> {name}"

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.sv_decl(name or self._attr_name)};"

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"

    def pack(self, value: Any) -> bytes:
        raise DeclarationError("symbolic packed layout must be specialized before Python encoding")

    def unpack(self, bytes_: bytes) -> tuple[Any, int]:
        raise DeclarationError("symbolic packed layout must be specialized before Python decoding")


class SymbolicArrayField(TypeBase):
    """A fixed unpacked array with a symbolic element layout or length."""

    def __init__(self, element_spec: Any, size_spec: Any, *, kwargs: dict[str, Any]) -> None:
        super().__init__(**kwargs)
        self.element_spec = element_spec
        self.size_spec = SymbolicPackedField._normalize_shape(size_spec)
        self.constructor_kwargs = dict(kwargs)

    def _element(self) -> Any:
        from .typespec import materialize_type

        return materialize_type(self.element_spec, "symbolic Array element")

    @property
    def rand(self):
        if self.field_options.rand is not None:
            return self.field_options.rand
        return bool(getattr(self._element(), "rand", False))

    def _size_items(self) -> tuple[Any, ...]:
        return self.size_spec if isinstance(self.size_spec, tuple) else (self.size_spec,)

    def _render_dimensions(self) -> tuple[str, ...]:
        return tuple(SymbolicPackedField._render_item(item) for item in self._size_items())

    def _render_size(self) -> str:
        """Compatibility accessor for the one-dimensional target helpers."""

        dimensions = self._render_dimensions()
        if len(dimensions) != 1:
            raise DeclarationError("a multi-dimensional symbolic Array has no single size")
        return dimensions[0]

    def _resolve_size(self, cls: type) -> int | tuple[int, ...]:
        values = SymbolicPackedField._parameter_values(cls)
        dimensions = tuple(
            SymbolicPackedField._resolve_item(self, item, values)
            for item in self._size_items()
        )
        if any(size <= 0 for size in dimensions):
            raise DeclarationError(
                f"{cls.__name__}.{self._attr_name} resolves to non-positive array dimension {dimensions!r}"
            )
        return dimensions[0] if len(dimensions) == 1 else dimensions

    def resolve(self, cls: type) -> Any:
        from .collection import Array

        size = self._resolve_size(cls)
        element = self._element()
        if isinstance(element, SymbolicPackedField):
            element = element.resolve(cls)
        return Array[element, size](**self.constructor_kwargs)

    def sv_decl(self, name: str) -> str:
        element = self._element()
        suffix = "".join(f" [({dimension})]" for dimension in self._render_dimensions())
        return element.sv_decl(f"{name}{suffix}")

    def cpp_decl(self, name: str) -> str:
        element = self._element()
        marker = "__svtypes_symbolic_element"
        declaration = element.cpp_decl(marker)
        if marker not in declaration:
            raise DeclarationError("symbolic Array element does not produce a C++ declaration")
        element_type = declaration.split(marker, 1)[0].rstrip()
        for dimension in reversed(self._render_dimensions()):
            element_type = f"std::array<{element_type}, {dimension}>"
        return f"{element_type} {name}"

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.sv_decl(name or self._attr_name)};"

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        dimensions = self._render_dimensions()
        if len(dimensions) > 1:
            nested = SymbolicArrayField(
                self.element_spec, self._size_items()[1:], kwargs={}
            )
            inner_indent = indent + self.IND
            return [
                f"{indent}foreach ({name}[i]) begin",
                *nested.sv_pack_loop(f"{name}[i]", level + 1, inner_indent),
                f"{indent}end",
            ]
        element = self._element()
        return [
            f"{indent}svtypes_pkg::fixed_array_packer#("
            f"{SvObject._sv_type_expr(element)}, {dimensions[0]}, "
            f"{SvObject._sv_packer_expr(element)})::pack({name}, bytes);"
        ]

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        from .object import SvObject

        dimensions = self._render_dimensions()
        if len(dimensions) > 1:
            nested = SymbolicArrayField(
                self.element_spec, self._size_items()[1:], kwargs={}
            )
            inner_indent = indent + self.IND
            return [
                f"{indent}foreach ({name}[i]) begin",
                *nested.sv_unpack_loop(f"{name}[i]", level + 1, inner_indent),
                f"{indent}end",
            ]
        element = self._element()
        return [
            f"{indent}svtypes_pkg::fixed_array_packer#("
            f"{SvObject._sv_type_expr(element)}, {dimensions[0]}, "
            f"{SvObject._sv_packer_expr(element)})::unpack({name}, bytes, offset);"
        ]

    def pack(self, value: Any) -> bytes:
        raise DeclarationError("symbolic array layout must be specialized before Python encoding")

    def unpack(self, bytes_: bytes) -> tuple[Any, int]:
        raise DeclarationError("symbolic array layout must be specialized before Python decoding")


class SymbolicCollectionField(TypeBase):
    """Dynamic, queue, or associative collection with symbolic member types."""

    def __init__(self, factory: type, args: tuple[Any, ...], *, kwargs: dict[str, Any]) -> None:
        super().__init__(**kwargs)
        self.factory = factory
        self.args = args
        self.constructor_kwargs = dict(kwargs)

    @staticmethod
    def _resolve_template(template: Any, cls: type) -> Any:
        from .typespec import materialize_type

        descriptor = materialize_type(template, "symbolic collection member")
        if isinstance(descriptor, SymbolicPackedField):
            return descriptor.resolve(cls)
        if isinstance(descriptor, SymbolicArrayField):
            return descriptor.resolve(cls)
        if isinstance(descriptor, SymbolicCollectionField):
            return descriptor.resolve(cls)
        return descriptor

    def _template(self) -> Any:
        # Dynamic collections have no symbolic length: constructing their
        # ordinary descriptor is safe in template state and provides all
        # target-language declaration/packer helpers.
        from .typespec import materialize_type

        args = tuple(materialize_type(item, "symbolic collection member") for item in self.args)
        return self.factory._from_layout(*args, **self.constructor_kwargs)

    @property
    def rand(self):
        if self.field_options.rand is not None:
            return self.field_options.rand
        template = self._template()
        return bool(getattr(template, "rand", False))

    def resolve(self, cls: type) -> Any:
        return self.factory._from_layout(
            *(self._resolve_template(item, cls) for item in self.args),
            **self.constructor_kwargs,
        )

    def sv_decl(self, name: str) -> str:
        return self._template().sv_decl(name)

    def cpp_decl(self, name: str) -> str:
        return self._template().cpp_decl(name)

    def to_sv_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.sv_decl(name or self._attr_name)};"

    def to_cpp_code(self, level: int = 0, name: str | None = None) -> str:
        return f"{self.IND * level}{self.cpp_decl(name or self._attr_name)};"

    def sv_pack_loop(self, name: str, level: int, indent: str) -> list[str]:
        return self._template().sv_pack_loop(name, level, indent)

    def sv_unpack_loop(self, name: str, level: int, indent: str) -> list[str]:
        return self._template().sv_unpack_loop(name, level, indent)

    def pack(self, value: Any) -> bytes:
        raise DeclarationError("symbolic collection layout must be specialized before Python encoding")

    def unpack(self, bytes_: bytes) -> tuple[Any, int]:
        raise DeclarationError("symbolic collection layout must be specialized before Python decoding")
