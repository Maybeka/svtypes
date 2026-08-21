"""Parameter TypeBase declarations, binding, and declaration rendering."""

from __future__ import annotations

import pytest

from svtypes import (
    Bit,
    DeclarationError,
    Int,
    LongInt,
    Parameter,
    Real,
    ShortReal,
    String,
    SvObject,
)


def test_declared_dtypes_are_unbound_with_type_info():
    assert Parameter(Int).dtype == "int"
    assert Parameter(LongInt).dtype == "longint"
    assert Parameter(String).dtype == "str"
    assert Parameter(Real).dtype == "float"
    assert Parameter(ShortReal).dtype == "shortreal"
    assert Parameter(type).dtype == "type"
    for param in (Parameter(Int), Parameter(type), Parameter(String), Parameter(Real)):
        assert param.is_bound is False
        assert param.value is None


def test_python_builtin_types_are_not_declarations():
    for dtype in (int, bool, str, float):
        with pytest.raises(TypeError, match="unsupported Parameter type"):
            Parameter(dtype)  # type: ignore[arg-type]


def test_value_derives_dtype_when_bound():
    assert Parameter(5).dtype == "int"
    assert Parameter(1.5).dtype == "float"
    assert Parameter("abc").dtype == "str"
    assert Parameter()(7).dtype == "int"
    assert Parameter()(2.5).dtype == "float"
    assert Parameter()("x").dtype == "str"
    # Python bool is an int subclass and binds as an int parameter.
    assert Parameter(True).dtype == "int"
    assert Parameter(True).value == 1


def test_declared_parameter_can_be_bound_later():
    p = Parameter(Int)
    assert p.is_bound is False
    bound = p(9)
    assert bound.is_bound is True
    assert bound.dtype == "int"
    assert bound.value == 9


def test_type_parameter_binding_requires_a_type():
    class Payload(SvObject):
        x = Bit(4)

    p = Parameter(type)(Payload)
    assert p.dtype == "type"
    assert p.value is Payload
    assert p.sv_type_value() == "Payload"

    with pytest.raises(TypeError, match="type parameter"):
        Parameter(type)(5)
    with pytest.raises(TypeError, match="type parameter"):
        Parameter(type)("abc")


def test_value_parameter_rejects_type_binding():
    with pytest.raises(TypeError, match="value parameter"):
        Parameter(Int)(Bit)  # type: ignore[arg-type]


def test_unsupported_dtype_declaration_raises():
    class NotAScalar:
        pass

    with pytest.raises(TypeError, match="unsupported Parameter type"):
        Parameter(NotAScalar)


def test_parameter_is_immutable_after_binding():
    p = Parameter(Int)
    bound = p(1)
    with pytest.raises(AttributeError):
        bound(2)
    with pytest.raises(AttributeError):
        bound.value = 3


def test_unbound_declaration_rendering():
    assert Parameter(Int).sv_decl("W") == "parameter int W"
    assert Parameter(LongInt).sv_decl("W") == "parameter longint W"
    assert Parameter(String).sv_decl("S") == "parameter string S"
    assert Parameter(Real).sv_decl("V") == "parameter real V"
    assert Parameter(ShortReal).sv_decl("V") == "parameter shortreal V"
    assert Parameter(type).sv_decl("T") == "parameter type T"

    assert Parameter(Int).cpp_decl("W").replace("static constexpr ", "") == "int32_t W"
    assert Parameter(LongInt).cpp_decl("W").replace("static constexpr ", "") == "int64_t W"
    assert Parameter(type).cpp_decl("T") == "typename T"


def test_str_and_float_unbound_cpp_are_rejected():
    with pytest.raises(DeclarationError, match="unbound C\\+\\+ template parameter"):
        Parameter(String).cpp_decl("S")
    with pytest.raises(DeclarationError, match="unbound C\\+\\+ template parameter"):
        Parameter(Real).cpp_decl("V")


def test_bound_declaration_rendering():
    assert Parameter(Int)(5).sv_decl("W") == "parameter int W"
    assert Parameter(Int)(5).to_sv_code(name="W").strip() == "parameter int W = 32'd5;"
    assert Parameter(String)("x").to_sv_code(name="S").strip() == 'parameter string S = "x";'
    assert Parameter(Int)(5).cpp_decl("W") == "static constexpr int32_t W"

    class Payload(SvObject):
        x = Bit(4)

    type_param = Parameter(type)(Payload)
    assert type_param.sv_decl("T") == "parameter type T = Payload"
    assert type_param.cpp_decl("T") == "typename T = Payload"


def test_untyped_unbound_parameter_requires_declaration_for_codegen():
    p = Parameter()
    assert p.dtype is None
    with pytest.raises(DeclarationError, match="declare a type"):
        p.sv_decl("W")
    with pytest.raises(DeclarationError, match="declare a type"):
        p.cpp_decl("W")


def test_declared_class_parameter_rejects_in_place_binding():
    class Tpl(SvObject):
        W = Parameter(Int)
        data = Bit(8)

    with pytest.raises(TypeError, match="already declared in a class"):
        Tpl.W(4)
    # The template stays unbound and cannot be instantiated.
    with pytest.raises(DeclarationError, match="parameterized template"):
        Tpl()
